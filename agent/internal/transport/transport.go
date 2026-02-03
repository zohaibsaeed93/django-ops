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
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	agentv1 "github.com/zohaibsaeed93/django-ops/agent/gen/agentv1"
	"github.com/zohaibsaeed93/django-ops/agent/internal/config"
	"github.com/zohaibsaeed93/django-ops/agent/internal/diagnostics"
	kubeexec "github.com/zohaibsaeed93/django-ops/agent/internal/kubernetes"
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

type activeJob struct{ cancel context.CancelFunc }
type admissionResult int

const (
	admitted admissionResult = iota
	duplicate
	atCapacity
)

func (r *Runner) Run(ctx context.Context) error {
	r.ensureCompleted()
	backoff := time.Second
	rng := rand.New(rand.NewSource(time.Now().UnixNano())) //nolint:gosec
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
	tlsConfig := &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots, ServerName: r.Config.ServerName}
	connectCtx, cancelConnect := context.WithTimeout(parent, 5*time.Second)
	defer cancelConnect()
	conn, err := grpc.DialContext(connectCtx, r.Config.Endpoint, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)), grpc.WithBlock())
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
	capabilities := []string{"django_diagnostics_v1"}
	if r.Config.CredentialRoot != "" {
		capabilities = append(capabilities, "kubernetes_release_v1")
	}
	if err := send(&agentv1.AgentFrame{Payload: &agentv1.AgentFrame_Hello{Hello: &agentv1.AgentHello{AgentId: r.Config.AgentID, Protocol: &agentv1.ProtocolVersion{Major: r.Config.ProtocolMajor}, Capabilities: capabilities, AuthToken: r.Config.AuthToken}}}); err != nil {
		return err
	}
	active := map[string]activeJob{}
	var activeMu sync.Mutex
	defer func() {
		activeMu.Lock()
		defer activeMu.Unlock()
		for _, job := range active {
			job.cancel()
		}
	}()
	go r.heartbeat(streamCtx, r.Config.HeartbeatEvery, send)
	for {
		frame, err := stream.Recv()
		if err != nil {
			cancelStream()
			return err
		}
		if request := frame.GetDiagnostics(); request != nil {
			r.startDiagnostics(streamCtx, request, active, &activeMu, send)
		} else if request := frame.GetKubernetesRelease(); request != nil {
			r.startKubernetes(streamCtx, request, active, &activeMu, send)
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

func (r *Runner) heartbeat(ctx context.Context, interval time.Duration, send func(*agentv1.AgentFrame) error) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			_ = send(&agentv1.AgentFrame{Payload: &agentv1.AgentFrame_Heartbeat{Heartbeat: &agentv1.Heartbeat{UnixMillis: now.UnixMilli()}}})
		}
	}
}

func (r *Runner) admit(ctx context.Context, jobID string, active map[string]activeJob, mu *sync.Mutex) (context.Context, context.CancelFunc, admissionResult) {
	jobCtx, cancel := context.WithCancel(ctx)
	if !safeJobID.MatchString(jobID) || r.wasCompleted(jobID) {
		return jobCtx, cancel, duplicate
	}
	mu.Lock()
	result := admitJob(active, jobID, cancel)
	mu.Unlock()
	return jobCtx, cancel, result
}

func (r *Runner) startDiagnostics(ctx context.Context, request *agentv1.DiagnosticsRequest, active map[string]activeJob, mu *sync.Mutex, send func(*agentv1.AgentFrame) error) {
	jobCtx, cancel, result := r.admit(ctx, request.JobId, active, mu)
	if result != admitted {
		cancel()
		return
	}
	go r.executeDiagnostics(jobCtx, request, send, func() {
		mu.Lock()
		delete(active, request.JobId)
		mu.Unlock()
	})
}

func (r *Runner) startKubernetes(ctx context.Context, request *agentv1.KubernetesReleaseRequest, active map[string]activeJob, mu *sync.Mutex, send func(*agentv1.AgentFrame) error) {
	if !safeJobID.MatchString(request.JobId) {
		_ = send(persistedResultFrame(request.JobId, 1, persistedKubernetesResult{Status: "failed", ErrorCode: "INVALID_OPERATION_ID"}))
		return
	}
	jobCtx, cancel := context.WithCancel(ctx)
	mu.Lock()
	admission := admitJob(active, request.JobId, cancel)
	mu.Unlock()
	if admission != admitted {
		cancel()
		return
	}
	fingerprint := kubernetesFingerprint(request)
	persisted, existed, err := r.beginKubernetesOperation(request.JobId, fingerprint)
	if err != nil {
		mu.Lock()
		delete(active, request.JobId)
		mu.Unlock()
		cancel()
		_ = send(persistedResultFrame(request.JobId, 1, persistedKubernetesResult{Status: "failed", ErrorCode: "DURABLE_STATE_UNAVAILABLE"}))
		return
	}
	if existed {
		mu.Lock()
		delete(active, request.JobId)
		mu.Unlock()
		cancel()
		if persisted.Fingerprint != fingerprint {
			_ = send(persistedResultFrame(request.JobId, 1, persistedKubernetesResult{Status: "failed", ErrorCode: "OPERATION_ID_CONFLICT"}))
			return
		}
		if persisted.State == "completed" && persisted.Result != nil {
			_ = send(persistedResultFrame(request.JobId, 1, *persisted.Result))
			return
		}
		// The prior process/connection stopped after durable admission but before a
		// replayable terminal result. Never rerun migration/Helm blindly: surface
		// an explicit recovery boundary instead of risking duplicate DB mutation.
		_ = send(persistedResultFrame(request.JobId, 1, persistedKubernetesResult{Status: "failed", ErrorCode: "RECOVERY_REQUIRED"}))
		return
	}
	go r.executeKubernetes(jobCtx, request, fingerprint, send, func() {
		mu.Lock()
		delete(active, request.JobId)
		mu.Unlock()
	})
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

func (r *Runner) executeDiagnostics(ctx context.Context, request *agentv1.DiagnosticsRequest, send func(*agentv1.AgentFrame) error, done func()) {
	defer done()
	sequence := uint64(1)
	runner := diagnostics.Runner{ProjectRoot: r.Config.ProjectRoot, ComposeFile: r.Config.ComposeFile}
	checks, status := runner.Run(ctx, request.ReleaseRoot, func(phase, message string) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		frame := &agentv1.AgentFrame{Payload: &agentv1.AgentFrame_Progress{Progress: &agentv1.ProgressEvent{JobId: request.JobId, Sequence: sequence, Phase: phase, Message: message}}}
		sequence++
		return send(frame)
	})
	if ctx.Err() != nil {
		status = "canceled"
	}
	_ = send(resultFrame(request.JobId, sequence, status, checks))
	r.markCompleted(request.JobId)
}

func (r *Runner) executeKubernetes(ctx context.Context, request *agentv1.KubernetesReleaseRequest, fingerprint string, send func(*agentv1.AgentFrame) error, done func()) {
	defer done()
	sequence := uint64(1)
	runner := kubeexec.Runner{ProjectRoot: r.Config.ProjectRoot, ResolveKubeconfig: r.resolveKubeconfig}
	result := runner.Run(ctx, kubeexec.Request{Namespace: request.Namespace, ReleaseName: request.ReleaseName, ChartPath: request.ChartPath, Image: request.Image, ValuesJSON: request.ValuesJson, CredentialRef: request.CredentialRef, MigrationTimeout: time.Duration(request.MigrationTimeoutSeconds) * time.Second, RolloutTimeout: time.Duration(request.RolloutTimeoutSeconds) * time.Second, RollbackPolicy: request.RollbackPolicy, Mode: request.Mode, PreviousRevision: request.PreviousRevision}, func(phase, message string) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		frame := &agentv1.AgentFrame{Payload: &agentv1.AgentFrame_KubernetesProgress{KubernetesProgress: &agentv1.KubernetesProgressEvent{JobId: request.JobId, Sequence: sequence, Phase: phase, Message: message}}}
		sequence++
		return send(frame)
	})
	if ctx.Err() != nil {
		result.Status = "cancelled"
		result.ErrorCode = "CANCELLED"
	}
	persisted := persistedKubernetesResult{Status: result.Status, HelmRevision: result.HelmRevision, PreviousRevision: result.PreviousRevision, RollbackStatus: result.RollbackStatus, ErrorCode: result.ErrorCode}
	if err := r.completeKubernetesOperation(request.JobId, fingerprint, persisted); err != nil {
		persisted = persistedKubernetesResult{Status: "failed", ErrorCode: "DURABLE_STATE_UNAVAILABLE"}
	}
	_ = send(persistedResultFrame(request.JobId, sequence, persisted))
	r.markCompleted(request.JobId)
}

func (r *Runner) resolveKubeconfig(ref string) (string, error) {
	if r.Config.CredentialRoot == "" || !strings.HasPrefix(ref, "file:") {
		return "", errors.New("unsupported Kubernetes credential reference")
	}
	name := strings.TrimPrefix(ref, "file:")
	if !safeJobID.MatchString(name) {
		return "", errors.New("invalid Kubernetes credential reference")
	}
	candidate := filepath.Join(r.Config.CredentialRoot, name)
	info, err := os.Stat(candidate)
	if err != nil || info.IsDir() || info.Mode().Perm()&0o077 != 0 {
		return "", errors.New("Kubernetes credential file unavailable or permissions too broad")
	}
	return candidate, nil
}

func resultFrame(jobID string, sequence uint64, status string, checks []diagnostics.Check) *agentv1.AgentFrame {
	protoChecks := make([]*agentv1.DiagnosticCheck, 0, len(checks))
	for _, check := range checks {
		protoChecks = append(protoChecks, &agentv1.DiagnosticCheck{Name: check.Name, Status: check.Status, Detail: check.Detail})
	}
	return &agentv1.AgentFrame{Payload: &agentv1.AgentFrame_Result{Result: &agentv1.DiagnosticsResult{JobId: jobID, Sequence: sequence, Status: status, Checks: protoChecks}}}
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
