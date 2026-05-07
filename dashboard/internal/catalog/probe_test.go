package catalog

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// makeClient returns an *http.Client with the given timeout.
func makeClient(timeout time.Duration) *http.Client {
	return &http.Client{Timeout: timeout}
}

// probeWith runs ProbeAll after temporarily replacing KnownServices with svcs.
// This must only be called from non-parallel tests, or tests that ensure mutual
// exclusion around the global, to avoid the data race.
//
// For parallel-safe tests we call doProbe directly instead.

// TestProbeAll_Healthy: httptest servers on N ports, assert all OK:true and LatencyMs >= 0.
// This test runs sequentially (not t.Parallel) because it mutates KnownServices.
func TestProbeAll_Healthy(t *testing.T) {
	const numServices = 3
	svcs := make([]ServiceDef, numServices)

	for i := 0; i < numServices; i++ {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusOK)
		}))
		t.Cleanup(srv.Close)
		addr := srv.Listener.Addr().String()
		var port int
		if _, err := parseHostPort(addr, &port); err != nil {
			t.Fatalf("parse addr: %v", err)
		}
		svcs[i] = ServiceDef{
			Name:       "healthy-svc",
			Port:       port,
			HealthPath: "/",
			Proto:      "http",
		}
	}

	// Swap KnownServices for this test (sequential, so no race).
	orig := KnownServices
	KnownServices = svcs
	defer func() { KnownServices = orig }()

	hosts := []HostAddr{
		{Hostname: "host0", IP: "127.0.0.1"},
		{Hostname: "host1", IP: "127.0.0.1"},
	}
	client := makeClient(2 * time.Second)
	results := ProbeAll(context.Background(), hosts, client)

	expected := len(hosts) * numServices
	if len(results) != expected {
		t.Fatalf("expected %d results, got %d", expected, len(results))
	}
	for _, r := range results {
		if !r.OK {
			t.Errorf("expected OK=true for %s@%s (url=%s)", r.Name, r.Host, r.URL)
		}
		if r.LatencyMs < 0 {
			t.Errorf("expected LatencyMs >= 0, got %d", r.LatencyMs)
		}
	}
}

// TestProbeAll_Unhealthy: server returns 503, assert OK:false.
// Sequential (not t.Parallel) to avoid race on KnownServices.
func TestProbeAll_Unhealthy(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	t.Cleanup(srv.Close)

	addr := srv.Listener.Addr().String()
	var port int
	if _, err := parseHostPort(addr, &port); err != nil {
		t.Fatalf("parse addr: %v", err)
	}

	orig := KnownServices
	KnownServices = []ServiceDef{
		{Name: "unhealthy-svc", Port: port, HealthPath: "/", Proto: "http"},
	}
	defer func() { KnownServices = orig }()

	hosts := []HostAddr{{Hostname: "badhost", IP: "127.0.0.1"}}
	client := makeClient(2 * time.Second)
	results := ProbeAll(context.Background(), hosts, client)

	if len(results) != 1 {
		t.Fatalf("expected 1 result, got %d", len(results))
	}
	if results[0].OK {
		t.Errorf("expected OK=false for 503 response, got true")
	}
}

// TestProbeAll_ConcurrentWallTime: 12 services each sleeping ~100ms should all
// complete within 500ms when using the 16-worker pool.
// Sequential (not t.Parallel) to avoid race on KnownServices.
func TestProbeAll_ConcurrentWallTime(t *testing.T) {
	const numSvcs = 12
	const probeDelay = 100 * time.Millisecond

	svcs := make([]ServiceDef, numSvcs)
	for i := 0; i < numSvcs; i++ {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			time.Sleep(probeDelay)
			w.WriteHeader(http.StatusOK)
		}))
		t.Cleanup(srv.Close)
		addr := srv.Listener.Addr().String()
		var port int
		if _, err := parseHostPort(addr, &port); err != nil {
			t.Fatalf("parse addr for svc %d: %v", i, err)
		}
		svcs[i] = ServiceDef{
			Name:       "slow-svc",
			Port:       port,
			HealthPath: "/",
			Proto:      "http",
		}
	}

	orig := KnownServices
	KnownServices = svcs
	defer func() { KnownServices = orig }()

	hosts := []HostAddr{{Hostname: "timing-host", IP: "127.0.0.1"}}
	// Generous client timeout so the probe response completes rather than times out.
	client := makeClient(2 * time.Second)

	start := time.Now()
	results := ProbeAll(context.Background(), hosts, client)
	elapsed := time.Since(start)

	if len(results) != numSvcs {
		t.Fatalf("expected %d results, got %d", numSvcs, len(results))
	}

	const maxWall = 500 * time.Millisecond
	if elapsed >= maxWall {
		t.Errorf("wall time %v >= %v — probes may not be running concurrently", elapsed, maxWall)
	}
}

// parseHostPort parses "host:port" and sets *port. Returns (host, nil) on success.
func parseHostPort(addr string, port *int) (string, error) {
	for i := len(addr) - 1; i >= 0; i-- {
		if addr[i] == ':' {
			var p int
			for _, c := range addr[i+1:] {
				if c < '0' || c > '9' {
					return "", &parseError{addr}
				}
				p = p*10 + int(c-'0')
			}
			*port = p
			return addr[:i], nil
		}
	}
	return "", &parseError{addr}
}

type parseError struct{ addr string }

func (e *parseError) Error() string { return "no port in " + e.addr }
