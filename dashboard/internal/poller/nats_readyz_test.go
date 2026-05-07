package poller

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/HomericIntelligence/atlas/internal/store"
)

// canonical successful payloads reused across the per-endpoint failure cases.
const (
	canonicalVarzJSON       = `{"connections":5,"in_msgs":1000,"out_msgs":800}`
	canonicalJszJSON        = `{"num_streams":3}`
	canonicalJszDetailJSON  = `{"streams":[{"config":{"name":"S1","subjects":["s.>"]},"state":{"messages":1,"bytes":2,"consumer_count":3},"created":"2024-01-01T00:00:00Z"}]}`
	canonicalConnzJSON      = `{"connections":[{"name":"c1","ip":"127.0.0.1","subscriptions":1,"in_msgs":1,"out_msgs":2,"uptime":"1s"}]}`
)

// natsHandler is a configurable httptest handler that returns 500 for any
// endpoint listed in `failing` and the canonical successful payload for the
// rest. Endpoints are matched by the test-friendly key strings ("varz",
// "jsz", "jsz_detail", "connz") that mirror the IncEndpointError labels.
func natsHandler(failing map[string]bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var key string
		switch r.URL.Path {
		case "/varz":
			key = "varz"
		case "/jsz":
			if r.URL.Query().Get("detail") == "1" {
				key = "jsz_detail"
			} else {
				key = "jsz"
			}
		case "/connz":
			key = "connz"
		default:
			http.NotFound(w, r)
			return
		}
		if failing[key] {
			http.Error(w, "synthetic failure", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		switch key {
		case "varz":
			_, _ = w.Write([]byte(canonicalVarzJSON))
		case "jsz":
			_, _ = w.Write([]byte(canonicalJszJSON))
		case "jsz_detail":
			_, _ = w.Write([]byte(canonicalJszDetailJSON))
		case "connz":
			_, _ = w.Write([]byte(canonicalConnzJSON))
		}
	}
}

// endpointCountingMetrics counts both cycle-level and per-endpoint failures
// so the regression tests can assert the dedicated-method wiring works.
type endpointCountingMetrics struct {
	pollErrors      atomic.Int64
	endpointErrors  sync.Map // endpoint string -> *atomic.Int64
}

func (c *endpointCountingMetrics) IncPollError(string) {
	c.pollErrors.Add(1)
}

func (c *endpointCountingMetrics) IncEndpointError(_, endpoint string) {
	v, _ := c.endpointErrors.LoadOrStore(endpoint, &atomic.Int64{})
	v.(*atomic.Int64).Add(1)
}

func (c *endpointCountingMetrics) ObservePollDuration(string, float64) {}

func (c *endpointCountingMetrics) endpointCount(endpoint string) int64 {
	v, ok := c.endpointErrors.Load(endpoint)
	if !ok {
		return 0
	}
	return v.(*atomic.Int64).Load()
}

// TestNATSPoller_AllEndpointsSucceed_LastErrorNil is test case 1: every
// endpoint returns 200, so recordResult(nil, _) — LastError() must be nil and
// LastSuccess() must advance.
func TestNATSPoller_AllEndpointsSucceed_LastErrorNil(t *testing.T) {
	srv := httptest.NewServer(natsHandler(nil))
	defer srv.Close()

	cache := store.NewCache()
	cfg := makeConfig("", srv.URL)
	p := NewNATSPoller(cfg, cache)
	m := &endpointCountingMetrics{}
	p.SetMetrics(m)

	p.fetch(context.Background())

	if err := p.LastError(); err != nil {
		t.Fatalf("LastError must be nil when all three endpoints succeed; got %v", err)
	}
	if p.LastSuccess().IsZero() {
		t.Error("LastSuccess must advance when all three endpoints succeed")
	}
	if got := m.pollErrors.Load(); got != 0 {
		t.Errorf("pollErrors: got %d, want 0", got)
	}

	// All three caches should reflect the canonical payloads.
	if stats := cache.GetNATSStats(); stats.Connections != 5 || stats.Streams != 3 {
		t.Errorf("stats cache: got %+v, want connections=5 streams=3", stats)
	}
	if streams := cache.GetNATSStreams(); len(streams) != 1 || streams[0].Name != "S1" {
		t.Errorf("streams cache: got %+v, want one stream named S1", streams)
	}
	if conns := cache.GetNATSConns(); len(conns) != 1 || conns[0].Name != "c1" {
		t.Errorf("conns cache: got %+v, want one connection named c1", conns)
	}
}

// TestNATSPoller_VarzFails_LastErrorMentionsVarz is test case 2.
func TestNATSPoller_VarzFails_LastErrorMentionsVarz(t *testing.T) {
	srv := httptest.NewServer(natsHandler(map[string]bool{"varz": true}))
	defer srv.Close()

	cache := store.NewCache()
	cfg := makeConfig("", srv.URL)
	p := NewNATSPoller(cfg, cache)
	m := &endpointCountingMetrics{}
	p.SetMetrics(m)

	p.fetch(context.Background())

	err := p.LastError()
	if err == nil {
		t.Fatal("LastError must be non-nil when /varz fails")
	}
	if !strings.Contains(err.Error(), "varz") {
		t.Errorf("LastError must mention varz; got %q", err.Error())
	}
	if got := m.endpointCount("varz"); got != 1 {
		t.Errorf("IncEndpointError(varz): got %d, want 1", got)
	}
}

// TestNATSPoller_JszDetailFails_LastErrorMentionsJsz is test case 3 — the
// detail endpoint failure must now gate readiness.
func TestNATSPoller_JszDetailFails_LastErrorMentionsJsz(t *testing.T) {
	srv := httptest.NewServer(natsHandler(map[string]bool{"jsz_detail": true}))
	defer srv.Close()

	cache := store.NewCache()
	cfg := makeConfig("", srv.URL)
	p := NewNATSPoller(cfg, cache)
	m := &endpointCountingMetrics{}
	p.SetMetrics(m)

	p.fetch(context.Background())

	err := p.LastError()
	if err == nil {
		t.Fatal("LastError must be non-nil when /jsz?detail=1 fails")
	}
	if !strings.Contains(err.Error(), "jsz") {
		t.Errorf("LastError must mention jsz; got %q", err.Error())
	}
	if got := m.endpointCount("jsz_detail"); got != 1 {
		t.Errorf("IncEndpointError(jsz_detail): got %d, want 1", got)
	}

	// /varz+/jsz still succeeded so stats cache is fresh; /connz also still
	// succeeded so its cache is fresh too. Streams cache is the one left
	// untouched (verified by absence of the canonical "S1" entry).
	if streams := cache.GetNATSStreams(); len(streams) != 0 {
		t.Errorf("streams cache should be untouched on /jsz?detail=1 failure; got %+v", streams)
	}
}

// TestNATSPoller_ConnzFails_LastErrorMentionsConnz is the regression case for
// the §3 audit MAJOR finding.
func TestNATSPoller_ConnzFails_LastErrorMentionsConnz(t *testing.T) {
	srv := httptest.NewServer(natsHandler(map[string]bool{"connz": true}))
	defer srv.Close()

	cache := store.NewCache()
	cfg := makeConfig("", srv.URL)
	p := NewNATSPoller(cfg, cache)
	m := &endpointCountingMetrics{}
	p.SetMetrics(m)

	p.fetch(context.Background())

	err := p.LastError()
	if err == nil {
		t.Fatal("REGRESSION: /connz failure must surface in LastError; before the fix, only /varz+/jsz failures gated readiness, so /readyz would say OK while the connection list went stale")
	}
	if !strings.Contains(err.Error(), "connz") {
		t.Errorf("LastError must mention connz; got %q", err.Error())
	}
	if got := m.endpointCount("connz"); got != 1 {
		t.Errorf("IncEndpointError(connz): got %d, want 1", got)
	}
	if got := m.pollErrors.Load(); got != 1 {
		t.Errorf("cycle-level pollErrors: got %d, want 1", got)
	}
}

// TestNATSPoller_TwoEndpointsFail_JoinedErrorMentionsBoth is test case 5.
func TestNATSPoller_TwoEndpointsFail_JoinedErrorMentionsBoth(t *testing.T) {
	srv := httptest.NewServer(natsHandler(map[string]bool{"varz": true, "connz": true}))
	defer srv.Close()

	cache := store.NewCache()
	cfg := makeConfig("", srv.URL)
	p := NewNATSPoller(cfg, cache)
	m := &endpointCountingMetrics{}
	p.SetMetrics(m)

	p.fetch(context.Background())

	err := p.LastError()
	if err == nil {
		t.Fatal("LastError must be non-nil when two endpoints fail")
	}
	if !strings.Contains(err.Error(), "varz") {
		t.Errorf("joined LastError must mention varz; got %q", err.Error())
	}
	if !strings.Contains(err.Error(), "connz") {
		t.Errorf("joined LastError must mention connz; got %q", err.Error())
	}
	// Cycle-level counter increments exactly once per failed cycle, even
	// though two endpoints failed. The endpoint-level counter increments
	// once per failing endpoint.
	if got := m.pollErrors.Load(); got != 1 {
		t.Errorf("cycle-level pollErrors: got %d, want 1 (one cycle, regardless of how many endpoints failed)", got)
	}
	if got := m.endpointCount("varz"); got != 1 {
		t.Errorf("endpoint varz: got %d, want 1", got)
	}
	if got := m.endpointCount("connz"); got != 1 {
		t.Errorf("endpoint connz: got %d, want 1", got)
	}
}
