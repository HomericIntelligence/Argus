package poller

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

// maxResponseBytes caps how much we will read from any upstream JSON
// endpoint before giving up. Defends against a compromised or hostile
// upstream (Agamemnon, Nestor, NATS monitoring) streaming a multi-GB
// response and OOM'ing Atlas (the compose memory limit is 128 MiB).
//
// 16 MiB is an order of magnitude above any realistic /v1/agents,
// /varz, /jsz?detail=1, or /connz payload — even a 1k-stream JetStream
// cluster's /jsz?detail=1 fits comfortably inside it — while staying
// well below the OOM threshold.
//
// Declared as a var (not a const) so tests can lower it without staging
// a 16 MiB fake response. Production paths must NEVER mutate this; if a
// future deployment legitimately needs a higher ceiling, prefer adding a
// config knob over runtime mutation.
var maxResponseBytes int64 = 16 << 20 // 16 MiB

// MetricsSink is the metric-recording surface a poller needs. *server.AtlasMetrics
// satisfies this interface; tests can pass a no-op or a counting fake. Pollers
// take this through SetMetrics rather than at construction so the Server can
// finalise the metric set after composing other dependencies.
//
// IncEndpointError is emitted on a separate metric series (atlas_poll_endpoint_errors_total)
// for pollers that fan out to multiple HTTP endpoints in a single cycle (e.g.
// NATSPoller hitting /varz, /jsz?detail=1, /connz). It is keyed by both the
// poller source name and the per-endpoint label so operators can answer "is
// /connz consistently failing or just /jsz?" without affecting the existing
// atlas_poll_errors_total{source} series or its alert rules.
type MetricsSink interface {
	IncPollError(source string)
	IncEndpointError(source, endpoint string)
	ObservePollDuration(source string, seconds float64)
}

// noopMetrics satisfies MetricsSink with no side effects. Used as the default
// value so callers that never call SetMetrics still work.
type noopMetrics struct{}

func (noopMetrics) IncPollError(string)               {}
func (noopMetrics) IncEndpointError(string, string)   {}
func (noopMetrics) ObservePollDuration(string, float64) {}

// base is a shared helper for HTTP-based pollers. It tracks per-instance
// readiness state (last successful poll timestamp + last error) for the
// /readyz aggregator and forwards poll-result events to a MetricsSink.
//
// All exported methods are safe for concurrent use.
type base struct {
	name   string
	client *http.Client

	mu          sync.RWMutex
	lastSuccess time.Time // zero value means "never succeeded"
	lastError   error

	metrics atomic.Pointer[MetricsSink] // never nil; defaults to noopMetrics
}

// newBase initialises a base with a no-op metrics sink. Callers should call
// SetMetrics from the composition root (cmd/argus-dashboard/main.go) once the
// real metrics object exists.
//
// Returns *base (pointer) because base contains a sync.RWMutex, which must
// never be copied (govet copylocks). Concrete pollers (AgamemnonPoller,
// NATSPoller) therefore embed *base, not base.
func newBase(name string, client *http.Client) *base {
	b := &base{name: name, client: client}
	var sink MetricsSink = noopMetrics{}
	b.metrics.Store(&sink)
	return b
}

// SetMetrics swaps in a real MetricsSink. Safe to call concurrently with
// running polls.
func (b *base) SetMetrics(m MetricsSink) {
	b.metrics.Store(&m)
}

// LastSuccess returns the timestamp of the most recent successful poll, or
// the zero value if no poll has ever succeeded.
func (b *base) LastSuccess() time.Time {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.lastSuccess
}

// LastError returns the error from the most recent poll attempt, or nil if
// the most recent attempt succeeded.
func (b *base) LastError() error {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.lastError
}

// Name returns the source name used for metric labels and readyz reporting.
func (b *base) Name() string { return b.name }

// recordResult is called by the concrete poller after each fetch cycle to
// update LastSuccess/LastError and emit metrics. Pass elapsed=0 to skip the
// duration histogram observation (e.g. when the cycle was a no-op).
func (b *base) recordResult(err error, elapsed time.Duration) {
	b.mu.Lock()
	if err == nil {
		b.lastSuccess = time.Now()
		b.lastError = nil
	} else {
		b.lastError = err
	}
	b.mu.Unlock()

	sink := *b.metrics.Load()
	if err != nil {
		sink.IncPollError(b.name)
	}
	if elapsed > 0 {
		sink.ObservePollDuration(b.name, elapsed.Seconds())
	}
}

// incEndpointError emits a per-endpoint failure metric without disturbing the
// poll-cycle accounting. Used by pollers that fan out to multiple HTTP
// endpoints in a single cycle so operators can attribute failures to the
// specific endpoint that broke. recordResult still tallies the cycle-level
// outcome via IncPollError.
func (b *base) incEndpointError(endpoint string) {
	sink := *b.metrics.Load()
	sink.IncEndpointError(b.name, endpoint)
}

// getJSON performs a GET request to url and JSON-decodes the response body into dst.
// It returns an error if the request fails or the status code is not 200.
// The error includes the URL so callers can distinguish failures across
// multiple endpoints.
func (b *base) getJSON(ctx context.Context, url string, dst any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("build request for %s: %w", url, err)
	}
	resp, err := b.client.Do(req)
	if err != nil {
		return fmt.Errorf("GET %s: %w", url, err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s: HTTP %d", url, resp.StatusCode)
	}
	// Cap how much we will pull from the upstream before failing. See
	// maxResponseBytes above for the rationale; io.LimitReader returns EOF
	// at the cap, which the JSON decoder surfaces as a syntax error if the
	// payload was actually truncated mid-document.
	if err := json.NewDecoder(io.LimitReader(resp.Body, maxResponseBytes)).Decode(dst); err != nil {
		return fmt.Errorf("decode %s (cap %d bytes): %w", url, maxResponseBytes, err)
	}
	return nil
}
