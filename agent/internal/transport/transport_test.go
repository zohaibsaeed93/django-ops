package transport

import (
	"context"
	"fmt"
	"testing"
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
