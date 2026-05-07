package poller

import (
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

// countingMetrics is a concurrent-safe MetricsSink that records call counts.
type countingMetrics struct {
	pollErrors       atomic.Int64
	endpointErrors   atomic.Int64
	durationObserves atomic.Int64
	lastSource       atomic.Value // string
	lastEndpoint     atomic.Value // string
}

func (c *countingMetrics) IncPollError(source string) {
	c.pollErrors.Add(1)
	c.lastSource.Store(source)
}

func (c *countingMetrics) IncEndpointError(source, endpoint string) {
	c.endpointErrors.Add(1)
	c.lastSource.Store(source)
	c.lastEndpoint.Store(endpoint)
}

func (c *countingMetrics) ObservePollDuration(source string, _ float64) {
	c.durationObserves.Add(1)
	c.lastSource.Store(source)
}

func TestRecordResult_SuccessUpdatesLastSuccessAndDuration(t *testing.T) {
	b := newBase("test", nil)
	m := &countingMetrics{}
	b.SetMetrics(m)

	b.recordResult(nil, 50*time.Millisecond)

	if b.LastSuccess().IsZero() {
		t.Error("LastSuccess must be non-zero after a successful recordResult")
	}
	if b.LastError() != nil {
		t.Errorf("LastError must be nil after success; got %v", b.LastError())
	}
	if got := m.pollErrors.Load(); got != 0 {
		t.Errorf("pollErrors: got %d, want 0", got)
	}
	if got := m.durationObserves.Load(); got != 1 {
		t.Errorf("durationObserves: got %d, want 1", got)
	}
}

func TestRecordResult_ErrorIncrementsCounterAndKeepsLastSuccess(t *testing.T) {
	b := newBase("agamemnon", nil)
	m := &countingMetrics{}
	b.SetMetrics(m)

	// Seed with a past success.
	b.recordResult(nil, 10*time.Millisecond)
	earlier := b.LastSuccess()

	// Now report a failure.
	b.recordResult(errors.New("boom"), 0)

	if b.LastSuccess() != earlier {
		t.Error("LastSuccess must be preserved across a failure")
	}
	if b.LastError() == nil {
		t.Error("LastError must be set after recordResult(err, _)")
	}
	if got := m.pollErrors.Load(); got != 1 {
		t.Errorf("pollErrors: got %d, want 1", got)
	}
	src, _ := m.lastSource.Load().(string)
	if src != "agamemnon" {
		t.Errorf("source label: got %q, want %q", src, "agamemnon")
	}
}

func TestRecordResult_ZeroElapsedSkipsHistogram(t *testing.T) {
	b := newBase("test", nil)
	m := &countingMetrics{}
	b.SetMetrics(m)

	b.recordResult(errors.New("boom"), 0) // no duration
	if got := m.durationObserves.Load(); got != 0 {
		t.Errorf("durationObserves: got %d, want 0 when elapsed=0", got)
	}
}

func TestSetMetrics_Concurrent(_ *testing.T) {
	b := newBase("test", nil)
	done := make(chan struct{})
	go func() {
		for i := 0; i < 1000; i++ {
			b.SetMetrics(&countingMetrics{})
		}
		close(done)
	}()
	for i := 0; i < 1000; i++ {
		b.recordResult(nil, time.Microsecond)
	}
	<-done
}

func TestNoopMetrics_IsDefault(_ *testing.T) {
	b := newBase("test", nil)
	// recordResult must not panic even though we never set a real sink.
	b.recordResult(nil, time.Millisecond)
	b.recordResult(errors.New("x"), time.Millisecond)
}
