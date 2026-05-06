package poller

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

// MetricsSink is the metric-recording surface a poller needs. *server.AtlasMetrics
// satisfies this interface; tests can pass a no-op or a counting fake. Pollers
// take this through SetMetrics rather than at construction so the Server can
// finalise the metric set after composing other dependencies.
type MetricsSink interface {
	IncPollError(source string)
	ObservePollDuration(source string, seconds float64)
}

// noopMetrics satisfies MetricsSink with no side effects. Used as the default
// value so callers that never call SetMetrics still work.
type noopMetrics struct{}

func (noopMetrics) IncPollError(string)              {}
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
func newBase(name string, client *http.Client) base {
	b := base{name: name, client: client}
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
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s: HTTP %d", url, resp.StatusCode)
	}
	if err := json.NewDecoder(resp.Body).Decode(dst); err != nil {
		return fmt.Errorf("decode %s: %w", url, err)
	}
	return nil
}
