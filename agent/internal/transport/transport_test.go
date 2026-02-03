package transport

import (
	"context"
	"fmt"
	"testing"

	agentv1 "github.com/zohaibsaeed93/django-ops/agent/gen/agentv1"
	"github.com/zohaibsaeed93/django-ops/agent/internal/config"
)

func TestCompletedRetentionIsBounded(t *testing.T) {
	runner := &Runner{}
	for i := 0; i < maxCompletedJobs+20; i++ {
		runner.markCompleted(fmt.Sprintf("job-%d", i))
	}
	if len(runner.completed) != maxCompletedJobs {
		t.Fatalf("completed map grew to %d, want %d", len(runner.completed), maxCompletedJobs)
	}
	if len(runner.completedOrder) != maxCompletedJobs {
		t.Fatalf("completed order grew to %d, want %d", len(runner.completedOrder), maxCompletedJobs)
	}
	if runner.wasCompleted("job-0") {
		t.Fatal("oldest completed ID should have been evicted")
	}
	if !runner.wasCompleted(fmt.Sprintf("job-%d", maxCompletedJobs+19)) {
		t.Fatal("newest completed ID should remain retained")
	}
}

func TestAdmissionFailsClosedAtCapacity(t *testing.T) {
	active := map[string]activeJob{}
	for i := 0; i < maxActiveJobs; i++ {
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		_ = ctx
		if got := admitJob(active, fmt.Sprintf("job-%d", i), cancel); got != admitted {
			t.Fatalf("job %d was not admitted: %v", i, got)
		}
	}
	_, cancel := context.WithCancel(context.Background())
	defer cancel()
	if got := admitJob(active, "overflow", cancel); got != atCapacity {
		t.Fatalf("overflow admission = %v, want atCapacity", got)
	}
	if len(active) != maxActiveJobs {
		t.Fatalf("active jobs grew to %d, want %d", len(active), maxActiveJobs)
	}
	if got := admitJob(active, "job-0", cancel); got != duplicate {
		t.Fatalf("duplicate admission = %v, want duplicate", got)
	}
}

func TestKubernetesStateSurvivesRunnerRestartAndReplaysResult(t *testing.T) {
	root := t.TempDir()
	request := &agentv1.KubernetesReleaseRequest{
		JobId:                   "k8s-restart-1",
		Namespace:               "project",
		ReleaseName:             "app",
		ChartPath:               "charts/djangoops",
		Image:                   "registry/app@sha256:" + fmt.Sprintf("%064d", 0),
		ValuesJson:              "{}",
		CredentialRef:           "file:cluster",
		MigrationTimeoutSeconds: 120,
		RolloutTimeoutSeconds:   120,
		RollbackPolicy:          "block-after-migration",
		Mode:                    "release",
	}
	fingerprint := kubernetesFingerprint(request)
	first := &Runner{Config: config.Config{ProjectRoot: root}}
	_, existed, err := first.beginKubernetesOperation(request.JobId, fingerprint)
	if err != nil || existed {
		t.Fatalf("begin = existed %v err %v", existed, err)
	}
	want := persistedKubernetesResult{Status: "succeeded", HelmRevision: 4, PreviousRevision: 3}
	if err := first.completeKubernetesOperation(request.JobId, fingerprint, want); err != nil {
		t.Fatal(err)
	}

	second := &Runner{Config: config.Config{ProjectRoot: root}}
	got, existed, err := second.beginKubernetesOperation(request.JobId, fingerprint)
	if err != nil || !existed {
		t.Fatalf("restart lookup = existed %v err %v", existed, err)
	}
	if got.State != "completed" || got.Result == nil || got.Result.HelmRevision != want.HelmRevision {
		t.Fatalf("persisted result = %#v", got)
	}
}

func TestKubernetesStateKeepsInFlightRecoveryBoundaryAcrossRestart(t *testing.T) {
	root := t.TempDir()
	request := &agentv1.KubernetesReleaseRequest{JobId: "k8s-inflight", Namespace: "project"}
	fingerprint := kubernetesFingerprint(request)
	first := &Runner{Config: config.Config{ProjectRoot: root}}
	if _, existed, err := first.beginKubernetesOperation(request.JobId, fingerprint); err != nil || existed {
		t.Fatalf("initial begin = existed %v err %v", existed, err)
	}
	second := &Runner{Config: config.Config{ProjectRoot: root}}
	got, existed, err := second.beginKubernetesOperation(request.JobId, fingerprint)
	if err != nil || !existed || got.State != "in_flight" {
		t.Fatalf("restart state = %#v existed=%v err=%v", got, existed, err)
	}
}

func TestKubernetesStateFailsClosedWhenOnlyUncertainEntriesFillLedger(t *testing.T) {
	root := t.TempDir()
	runner := &Runner{Config: config.Config{ProjectRoot: root}}
	for i := 0; i < maxCompletedJobs; i++ {
		jobID := fmt.Sprintf("k8s-uncertain-%d", i)
		if _, existed, err := runner.beginKubernetesOperation(jobID, fmt.Sprintf("fp-%d", i)); err != nil || existed {
			t.Fatalf("fill %d = existed %v err %v", i, existed, err)
		}
	}
	if _, _, err := runner.beginKubernetesOperation("k8s-overflow", "fp-overflow"); err == nil {
		t.Fatal("expected durable ledger capacity failure")
	}
	state, err := readKubernetesState(root + "/" + kubernetesStateFile)
	if err != nil {
		t.Fatal(err)
	}
	if len(state.Jobs) != maxCompletedJobs {
		t.Fatalf("durable ledger grew to %d, want %d", len(state.Jobs), maxCompletedJobs)
	}
}
