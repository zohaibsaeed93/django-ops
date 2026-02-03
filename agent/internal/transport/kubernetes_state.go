package transport

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"time"

	agentv1 "github.com/zohaibsaeed93/django-ops/agent/gen/agentv1"
)

const kubernetesStateFile = ".djangoops-agent-kubernetes.json"

type persistedKubernetesResult struct {
	Status           string `json:"status"`
	HelmRevision     uint32 `json:"helm_revision,omitempty"`
	PreviousRevision uint32 `json:"previous_revision,omitempty"`
	RollbackStatus   string `json:"rollback_status,omitempty"`
	ErrorCode        string `json:"error_code,omitempty"`
}

type persistedKubernetesJob struct {
	Fingerprint string                     `json:"fingerprint"`
	State       string                     `json:"state"`
	Result      *persistedKubernetesResult `json:"result,omitempty"`
	UpdatedAt   int64                      `json:"updated_at"`
}

type persistedKubernetesState struct {
	Jobs map[string]persistedKubernetesJob `json:"jobs"`
}

func kubernetesFingerprint(request *agentv1.KubernetesReleaseRequest) string {
	payload := struct {
		Namespace        string
		ReleaseName      string
		ChartPath        string
		Image            string
		ValuesJSON       string
		CredentialRef    string
		MigrationTimeout uint32
		RolloutTimeout   uint32
		RollbackPolicy   string
		Mode             string
		PreviousRevision uint32
	}{
		request.Namespace,
		request.ReleaseName,
		request.ChartPath,
		request.Image,
		request.ValuesJson,
		request.CredentialRef,
		request.MigrationTimeoutSeconds,
		request.RolloutTimeoutSeconds,
		request.RollbackPolicy,
		request.Mode,
		request.PreviousRevision,
	}
	encoded, _ := json.Marshal(payload)
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

func (r *Runner) kubernetesStatePath() (string, error) {
	if r.Config.ProjectRoot == "" {
		return "", errors.New("project root unavailable for durable Kubernetes state")
	}
	return filepath.Join(r.Config.ProjectRoot, kubernetesStateFile), nil
}

func readKubernetesState(path string) (persistedKubernetesState, error) {
	state := persistedKubernetesState{Jobs: map[string]persistedKubernetesJob{}}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return state, nil
	}
	if err != nil {
		return state, err
	}
	if err := json.Unmarshal(data, &state); err != nil {
		return persistedKubernetesState{}, err
	}
	if state.Jobs == nil {
		state.Jobs = map[string]persistedKubernetesJob{}
	}
	return state, nil
}

func pruneCompletedKubernetesJobs(state *persistedKubernetesState, target int) {
	if len(state.Jobs) <= target {
		return
	}
	type pair struct {
		id string
		at int64
	}
	completed := make([]pair, 0, len(state.Jobs))
	for id, job := range state.Jobs {
		if job.State == "completed" {
			completed = append(completed, pair{id, job.UpdatedAt})
		}
	}
	sort.Slice(completed, func(i, j int) bool { return completed[i].at < completed[j].at })
	for len(state.Jobs) > target && len(completed) > 0 {
		delete(state.Jobs, completed[0].id)
		completed = completed[1:]
	}
}

func writeKubernetesState(path string, state persistedKubernetesState) error {
	pruneCompletedKubernetesJobs(&state, maxCompletedJobs)
	if len(state.Jobs) > maxCompletedJobs {
		return errors.New("durable Kubernetes operation ledger capacity reached")
	}
	encoded, err := json.Marshal(state)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".djangoops-kubernetes-state-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(encoded); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}

func (r *Runner) beginKubernetesOperation(jobID, fingerprint string) (persistedKubernetesJob, bool, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	path, err := r.kubernetesStatePath()
	if err != nil {
		return persistedKubernetesJob{}, false, err
	}
	state, err := readKubernetesState(path)
	if err != nil {
		return persistedKubernetesJob{}, false, err
	}
	if existing, ok := state.Jobs[jobID]; ok {
		return existing, true, nil
	}
	// Keep the durable ledger bounded even across repeated process restarts.
	// Completed entries are evicted oldest-first; unresolved in-flight entries
	// are never discarded automatically because doing so could permit a
	// duplicate migration. If only uncertain entries remain, fail closed.
	pruneCompletedKubernetesJobs(&state, maxCompletedJobs-1)
	if len(state.Jobs) >= maxCompletedJobs {
		return persistedKubernetesJob{}, false, errors.New("durable Kubernetes operation ledger capacity reached")
	}
	job := persistedKubernetesJob{Fingerprint: fingerprint, State: "in_flight", UpdatedAt: time.Now().UnixMilli()}
	state.Jobs[jobID] = job
	if err := writeKubernetesState(path, state); err != nil {
		return persistedKubernetesJob{}, false, err
	}
	return job, false, nil
}

func (r *Runner) completeKubernetesOperation(jobID, fingerprint string, result persistedKubernetesResult) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	path, err := r.kubernetesStatePath()
	if err != nil {
		return err
	}
	state, err := readKubernetesState(path)
	if err != nil {
		return err
	}
	state.Jobs[jobID] = persistedKubernetesJob{
		Fingerprint: fingerprint,
		State:       "completed",
		Result:      &result,
		UpdatedAt:   time.Now().UnixMilli(),
	}
	return writeKubernetesState(path, state)
}

func persistedResultFrame(jobID string, sequence uint64, result persistedKubernetesResult) *agentv1.AgentFrame {
	return &agentv1.AgentFrame{Payload: &agentv1.AgentFrame_KubernetesResult{KubernetesResult: &agentv1.KubernetesReleaseResult{
		JobId:            jobID,
		Sequence:         sequence,
		Status:           result.Status,
		HelmRevision:     result.HelmRevision,
		PreviousRevision: result.PreviousRevision,
		RollbackStatus:   result.RollbackStatus,
		ErrorCode:        result.ErrorCode,
	}}}
}
