package telemetry

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

var safeCorrelation = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

var allowedKind = map[string]bool{
	"connection":         true,
	"diagnostic":         true,
	"kubernetes_release": true,
}

var allowedOutcome = map[string]bool{
	"succeeded":         true,
	"failed":            true,
	"cancelled":         true,
	"disconnected":      true,
	"overloaded":        true,
	"recovery_required": true,
}

type event struct {
	Kind        string
	Outcome     string
	Correlation string
	DurationMS  int64
}

type Snapshot struct {
	Dropped      uint64
	ExportErrors uint64
	Queued       int
}

type Recorder struct {
	endpoint string
	queue    chan event
	client   *http.Client
	started  sync.Once
	dropped  atomic.Uint64
	errors   atomic.Uint64
}

func New(endpoint string, capacity int) (*Recorder, error) {
	if capacity < 8 {
		capacity = 8
	}
	if capacity > 1024 {
		capacity = 1024
	}
	validated, err := validateEndpoint(endpoint)
	if err != nil {
		return nil, err
	}
	return &Recorder{
		endpoint: validated,
		queue:    make(chan event, capacity),
		client:   &http.Client{Timeout: time.Second},
	}, nil
}

func (r *Recorder) Observe(kind, outcome, correlation string, duration time.Duration) {
	if r == nil || r.endpoint == "" {
		return
	}
	if !allowedKind[kind] {
		kind = "diagnostic"
	}
	if !allowedOutcome[outcome] {
		outcome = "failed"
	}
	if !safeCorrelation.MatchString(correlation) {
		correlation = "invalid"
	}
	e := event{Kind: kind, Outcome: outcome, Correlation: correlation, DurationMS: max(0, duration.Milliseconds())}
	select {
	case r.queue <- e:
		r.started.Do(func() { go r.run() })
	default:
		r.dropped.Add(1)
	}
}

func (r *Recorder) Snapshot() Snapshot {
	if r == nil {
		return Snapshot{}
	}
	return Snapshot{Dropped: r.dropped.Load(), ExportErrors: r.errors.Load(), Queued: len(r.queue)}
}

func (r *Recorder) run() {
	for e := range r.queue {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		err := r.export(ctx, e)
		cancel()
		if err != nil {
			r.errors.Add(1)
		}
	}
}

func (r *Recorder) export(ctx context.Context, e event) error {
	payload := map[string]any{
		"resourceSpans": []any{map[string]any{
			"resource": map[string]any{"attributes": []any{map[string]any{
				"key": "service.name", "value": map[string]any{"stringValue": "djangoops-agent"},
			}}},
			"scopeSpans": []any{map[string]any{
				"scope": map[string]any{"name": "djangoops-agent"},
				"spans": []any{map[string]any{
					"traceId": randomHex(16),
					"spanId":  randomHex(8),
					"name":    fmt.Sprintf("djangoops.%s", e.Kind),
					"attributes": []any{
						map[string]any{"key": "djangoops.outcome", "value": map[string]any{"stringValue": e.Outcome}},
						map[string]any{"key": "djangoops.correlation_id", "value": map[string]any{"stringValue": e.Correlation}},
						map[string]any{"key": "djangoops.duration_ms", "value": map[string]any{"intValue": e.DurationMS}},
					},
				}},
			}},
		}},
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, r.endpoint+"/v1/traces", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := r.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("OTLP exporter status %d", resp.StatusCode)
	}
	return nil
}

func validateEndpoint(value string) (string, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", nil
	}
	parsed, err := url.Parse(value)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" {
		return "", fmt.Errorf("invalid OTLP endpoint")
	}
	if parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", fmt.Errorf("OTLP endpoint must not embed credentials/query/fragment")
	}
	return strings.TrimRight(value, "/"), nil
}

func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return strings.Repeat("1", n*2)
	}
	return hex.EncodeToString(b)
}
