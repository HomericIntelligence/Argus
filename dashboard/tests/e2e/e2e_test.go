//go:build e2e

// Package e2e contains end-to-end tests that hit a running Atlas instance
// over HTTP. Run with:
//
//	go test -tags=e2e ./tests/e2e/...
//
// Configuration:
//
//	ATLAS_E2E_URL    base URL, default http://localhost:3002
//	ATLAS_E2E_TOKEN  bearer token; required for any test that hits an
//	                 auth-gated endpoint (/readyz, /metrics, /events,
//	                 /api/*, anything other than /livez and /healthz).
//
// These tests assume Atlas is running with ATLAS_AUTH_MODE=bearer and the
// matching token. If ATLAS_E2E_TOKEN is unset, auth-required tests skip.
package e2e

import (
	"bufio"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"
)

func atlasURL() string {
	if u := os.Getenv("ATLAS_E2E_URL"); u != "" {
		return u
	}
	return "http://localhost:3002"
}

// authReq builds a GET request with bearer auth. Skips the test if no token
// is configured — auth-required endpoints can't be exercised without it.
func authReq(t *testing.T, path string) *http.Request {
	t.Helper()
	tok := os.Getenv("ATLAS_E2E_TOKEN")
	if tok == "" {
		t.Skip("ATLAS_E2E_TOKEN not set; skipping auth-required endpoint test")
	}
	req, err := http.NewRequest(http.MethodGet, atlasURL()+path, nil)
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	return req
}

// TestLivez verifies the unauthenticated liveness probe (k8s required).
// /healthz is kept as an alias and asserted alongside.
func TestLivez(t *testing.T) {
	for _, path := range []string{"/livez", "/healthz"} {
		t.Run(path, func(t *testing.T) {
			resp, err := http.Get(atlasURL() + path)
			if err != nil {
				t.Fatalf("GET %s: %v", path, err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("GET %s: got %d, want 200", path, resp.StatusCode)
			}
		})
	}
}

// TestReadyz_Unauthenticated_Returns401 verifies that the /readyz endpoint
// is auth-gated (PR #444 audit S8 CRITICAL #1 fix).
func TestReadyz_Unauthenticated_Returns401(t *testing.T) {
	resp, err := http.Get(atlasURL() + "/readyz")
	if err != nil {
		t.Fatalf("GET /readyz: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("GET /readyz unauthenticated: got %d, want 401", resp.StatusCode)
	}
}

// TestReadyz_Authenticated_HasComponentsArray verifies /readyz returns the
// per-component JSON shape regardless of whether the components are OK
// (200 vs 503 depends on the running deployment).
func TestReadyz_Authenticated_HasComponentsArray(t *testing.T) {
	req := authReq(t, "/readyz")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET /readyz: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("GET /readyz: got %d, want 200 or 503", resp.StatusCode)
	}
	var body struct {
		OK         bool `json:"ok"`
		Components []struct {
			Name        string `json:"name"`
			OK          bool   `json:"ok"`
			Error       string `json:"error,omitempty"`
			LastSuccess string `json:"last_success,omitempty"`
		} `json:"components"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode /readyz body: %v", err)
	}
	if len(body.Components) == 0 {
		t.Fatal("/readyz returned empty components array; expected at least one (nats-subscriber, agamemnon, nats poller)")
	}
	// 200 and ok must agree.
	if (resp.StatusCode == 200) != body.OK {
		t.Errorf("status code %d does not match body.ok=%v", resp.StatusCode, body.OK)
	}
}

// TestMetrics_Unauthenticated_Returns401 verifies /metrics is auth-gated
// (PR #444 audit S8 CRITICAL #1 fix).
func TestMetrics_Unauthenticated_Returns401(t *testing.T) {
	resp, err := http.Get(atlasURL() + "/metrics")
	if err != nil {
		t.Fatalf("GET /metrics: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("GET /metrics unauthenticated: got %d, want 401", resp.StatusCode)
	}
}

// TestMetrics_Authenticated_ContainsExpectedMetrics verifies the Prometheus
// exposition format includes the headline Atlas metrics so dashboards and
// alert rules can rely on them.
func TestMetrics_Authenticated_ContainsExpectedMetrics(t *testing.T) {
	req := authReq(t, "/metrics")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET /metrics: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET /metrics: got %d, want 200", resp.StatusCode)
	}

	ct := resp.Header.Get("Content-Type")
	if !strings.HasPrefix(ct, "text/plain; version=0.0.4") {
		t.Errorf("Content-Type: got %q, want prefix text/plain; version=0.0.4", ct)
	}

	body := make([]byte, 1<<20)
	n, _ := resp.Body.Read(body)
	text := string(body[:n])

	for _, want := range []string{
		"atlas_build_info",
		"atlas_nats_connected",
		"atlas_sse_connected_clients",
		"atlas_poll_errors_total",
		"atlas_poll_duration_seconds",
		"atlas_nats_messages_processed_total",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("/metrics body missing metric %q", want)
		}
	}
}

// TestSSE_AuthenticatedHeartbeat verifies that the SSE endpoint is reachable
// with bearer auth and emits at least one frame within a short window. We
// don't require a real event — heartbeats fire every HeartbeatInterval, but
// the default is 15s so we set the test client timeout slightly above.
func TestSSE_AuthenticatedHeartbeat(t *testing.T) {
	req := authReq(t, "/events")
	req.Header.Set("Accept", "text/event-stream")
	client := &http.Client{Timeout: 20 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatalf("GET /events: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET /events: got %d, want 200", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Errorf("Content-Type: got %q, want text/event-stream", ct)
	}

	// Read at least one line and assert it's an SSE frame (heartbeat
	// is `: heartbeat\n` per server/handlers/sse.go).
	br := bufio.NewReader(resp.Body)
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		line, err := br.ReadString('\n')
		if err != nil {
			t.Fatalf("read SSE line: %v", err)
		}
		// Ignore empty separator lines; first non-empty line should be a
		// SSE comment (heartbeat) or an event: line.
		line = strings.TrimRight(line, "\r\n")
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, ":") || strings.HasPrefix(line, "event:") || strings.HasPrefix(line, "data:") {
			return // success
		}
		t.Fatalf("unexpected first SSE line: %q", line)
	}
	t.Fatal("no SSE frame received within 20s")
}

// TestSSE_QueryTokenAuth verifies the EventSource compatibility path: SSE
// clients can pass the bearer token via ?token= because EventSource cannot
// set custom request headers.
func TestSSE_QueryTokenAuth(t *testing.T) {
	tok := os.Getenv("ATLAS_E2E_TOKEN")
	if tok == "" {
		t.Skip("ATLAS_E2E_TOKEN not set; skipping query-token SSE test")
	}
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(atlasURL() + "/events?token=" + tok)
	if err != nil {
		t.Fatalf("GET /events?token=…: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET /events?token=…: got %d, want 200", resp.StatusCode)
	}
}
