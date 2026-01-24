package transport

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"log"
	"math/rand"
	"os"
	"regexp"
	"sync"
	"time"

	agentv1 "github.com/zohaibsaeed93/django-ops/agent/gen/agentv1"
	"github.com/zohaibsaeed93/django-ops/agent/internal/config"
	"github.com/zohaibsaeed93/django-ops/agent/internal/diagnostics"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

const maxCompletedJobs = 256
const maxActiveJobs = 4

var safeJobID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

type Runner struct {
	Config         config.Config
	completed      map[string]bool
	completedOrder []string
	mu             sync.Mutex
}

type activeJob struct {
	cancel context.CancelFunc
}

type admissionResult int

const (
	admitted admissionResult = iota
	duplicate
	atCapacity
)

func (r *Runner) Run(ctx context.Context) error {
	r.ensureCompleted()
	backoff := time.Second
	rng := rand.New(rand.NewSource(time.Now().UnixNano())) //nolint:gosec // reconnect jitter is not security-sensitive
	for ctx.Err() == nil {
		err := r.connectOnce(ctx)
		if ctx.Err() != nil {
			return nil
		}
		if err != nil {
			log.Printf("control connection ended; reconnecting")
		}
		jitter := time.Duration(rng.Int63n(int64(backoff/2 + 1)))
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(backoff + jitter):
		}
		if backoff < 16*time.Second {
			backoff *= 2
		}
	}
	return nil
}

func (r *Runner) connectOnce(parent context.Context) error {
	r.ensureCompleted()
	caPEM, err := os.ReadFile(r.Config.CAFile)
	if err != nil {
		return fmt.Errorf("read control-plane CA: %w", err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		return errors.New("control-plane CA file contains no certificates")
	}
	tlsConfig := &tls.Config{
		MinVersion: tls.VersionTLS12,
		RootCAs:    roots,
		ServerName: r.Config.ServerName,
	}
	connectCtx, cancelConnect := context.WithTimeout(parent, 5*time.Second)
	defer cancelConnect()
	conn, err := grpc.DialContext(
		connectCtx,
		r.Config.Endpoint,
		grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)),
		grpc.WithBlock(),
	)
	if err != nil {
		return fmt.Errorf("connect control plane: %w", err)
	}
	defer conn.Close()

	streamCtx, cancelStream := context.WithCancel(parent)
	defer cancelStream()
	stream, err := agentv1.NewAgentControlClient(conn).Connect(streamCtx)
	if err != nil {
		return fmt.Errorf("open control stream: %w", err)
	}
	var sendMu sync.Mutex
	send := func(frame *agentv1.AgentFrame) error {
		sendMu.Lock()
		defer sendMu.Unlock()
		return stream.Send(frame)
	}
	if err := send(&agentv1.AgentFrame{Payload: &agentv1.AgentFrame_Hello{Hello: &agentv1.AgentHello{
		AgentId:      r.Config.AgentID,
		Protocol:     &agentv1.ProtocolVersion{Major: r.Config.ProtocolMajor, Minor: 0},
		Capabilities: []string{"django_diagnostics_v1"},
		AuthToken:    r.Config.AuthToken,
	}}}); err != nil {
		return err
	}

	active := map[string]activeJob{}
	var activeMu sync.Mutex
	cancelAll := func() {
		activeMu.Lock()
		defer activeMu.Unlock()
		for _, job := range active {
			job.cancel()
		}
	}
	defer cancelAll()

	go func() {
		ticker := time.NewTicker(r.Config.HeartbeatEvery)
		defer ticker.Stop()
		for {
			select {
			case <-streamCtx.Done():
				return
			case now := <-ticker.C:
				_ = send(&agentv1.AgentFrame{Payload: &agentv1.AgentFrame_Heartbeat{Heartbeat: &agentv1.Heartbeat{UnixMillis: now.UnixMilli()}}})
			}
		}
	}()

	for {
		frame, err := stream.Recv()
		if err != nil {
			cancelStream()
			return err
		}
		if request := frame.GetDiagnostics(); request != nil {
			if !safeJobID.MatchString(request.JobId) {
				continue
			}
			if r.wasCompleted(request.JobId) {
				_ = send(resultFrame(request.JobId, 1, "rejected", []diagnostics.Check{{Name: "job_id", Status: "fail", Detail: "retained completed job IDs are not replayed"}}))
				continue
			}
			jobCtx, cancel := context.WithCancel(streamCtx)
			activeMu.Lock()
			admission := admitJob(active, request.JobId, cancel)
			activeMu.Unlock()
			switch admission {
			case duplicate:
				cancel()
				continue
			case atCapacity:
				cancel()
				_ = send(resultFrame(request.JobId, 1, "rejected", []diagnostics.Check{{Name: "resource_limit", Status: "fail", Detail: "agent diagnostics capacity reached"}}))
				r.markCompleted(request.JobId)
				continue
			}
			go r.execute(jobCtx, request, send, func() {
				activeMu.Lock()
				delete(active, request.JobId)
				activeMu.Unlock()
			})
		} else if request := frame.GetCancel(); request != nil {
			activeMu.Lock()
			job, exists := active[request.JobId]
			activeMu.Unlock()
			if exists {
				job.cancel()
			}
		}
	}
}

func admitJob(active map[string]activeJob, jobID string, cancel context.CancelFunc) admissionResult {
	if _, exists := active[jobID]; exists {
		return duplicate
	}
	if len(active) >= maxActiveJobs {
		return atCapacity
	}
	active[jobID] = activeJob{cancel: cancel}
	return admitted
}

func (r *Runner) ensureCompleted() {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.completed == nil {
		r.completed = map[string]bool{}
	}
}

func (r *Runner) wasCompleted(jobID string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.completed[jobID]
}

func (r *Runner) markCompleted(jobID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.completed == nil {
		r.completed = map[string]bool{}
	}
	if r.completed[jobID] {
		return
	}
	if len(r.completedOrder) >= maxCompletedJobs {
		evicted := r.completedOrder[0]
		r.completedOrder = r.completedOrder[1:]
		delete(r.completed, evicted)
	}
	r.completed[jobID] = true
	r.completedOrder = append(r.completedOrder, jobID)
}

func (r *Runner) execute(ctx context.Context, request *agentv1.DiagnosticsRequest, send func(*agentv1.AgentFrame) error, done func()) {
	defer done()
	sequence := uint64(1)
	diagnosticRunner := diagnostics.Runner{ProjectRoot: r.Config.ProjectRoot, ComposeFile: r.Config.ComposeFile}
	checks, status := diagnosticRunner.Run(ctx, request.ReleaseRoot, func(phase, message string) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		frame := &agentv1.AgentFrame{Payload: &agentv1.AgentFrame_Progress{Progress: &agentv1.ProgressEvent{
			JobId: request.JobId, Sequence: sequence, Phase: phase, Message: message,
		}}}
		sequence++
		return send(frame)
	})
	if ctx.Err() != nil {
		status = "canceled"
	}
	_ = send(resultFrame(request.JobId, sequence, status, checks))
	r.markCompleted(request.JobId)
}

func resultFrame(jobID string, sequence uint64, status string, checks []diagnostics.Check) *agentv1.AgentFrame {
	protoChecks := make([]*agentv1.DiagnosticCheck, 0, len(checks))
	for _, check := range checks {
		protoChecks = append(protoChecks, &agentv1.DiagnosticCheck{Name: check.Name, Status: check.Status, Detail: check.Detail})
	}
	return &agentv1.AgentFrame{Payload: &agentv1.AgentFrame_Result{Result: &agentv1.DiagnosticsResult{
		JobId: jobID, Sequence: sequence, Status: status, Checks: protoChecks,
	}}}
}
