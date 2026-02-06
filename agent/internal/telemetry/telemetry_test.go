package telemetry

import (
	"testing"
	"time"
)

func TestEndpointRejectsEmbeddedCredentials(t *testing.T) {
	if _, err := New("https://user:pass@example.test", 16); err == nil {
		t.Fatal("expected credential-bearing endpoint to be rejected")
	}
}

func TestQueueIsBoundedAndDropsWithoutBlocking(t *testing.T) {
	r, err := New("https://127.0.0.1:1", 8)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < cap(r.queue); i++ {
		r.queue <- event{Kind: "diagnostic", Outcome: "succeeded", Correlation: "prefill"}
	}
	r.Observe("diagnostic", "succeeded", "diag-safe", time.Millisecond)
	s := r.Snapshot()
	if s.Queued != 8 {
		t.Fatalf("queue bound changed: %d", s.Queued)
	}
	if s.Dropped != 1 {
		t.Fatalf("expected one deterministic drop, got %d", s.Dropped)
	}
}

func TestInvalidCardinalityValuesAreCollapsed(t *testing.T) {
	r, err := New("https://127.0.0.1:1", 8)
	if err != nil {
		t.Fatal(err)
	}
	r.Observe("attacker\nkind", "attacker-outcome", "bad\ncorrelation", time.Millisecond)
	if r.Snapshot().Queued > 8 {
		t.Fatal("queue bound violated")
	}
}
