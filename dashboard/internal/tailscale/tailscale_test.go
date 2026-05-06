package tailscale_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/HomericIntelligence/atlas/internal/store"
	"github.com/HomericIntelligence/atlas/internal/tailscale"
)

// ---------------------------------------------------------------------------
// TestStaticSource
// ---------------------------------------------------------------------------

// TestStaticSource sets env vars and asserts two devices with expected IPs.
// Note: t.Setenv and t.Parallel are mutually exclusive; these tests are serial.
func TestStaticSource(t *testing.T) {
	t.Setenv("ATLAS_WORKER_HOST_IP", "100.64.0.1")
	t.Setenv("ATLAS_CONTROL_HOST_IP", "100.64.0.2")

	src := tailscale.StaticSource{}
	devices, err := src.Devices(context.Background())
	if err != nil {
		t.Fatalf("StaticSource.Devices: unexpected error: %v", err)
	}

	if len(devices) != 2 {
		t.Fatalf("got %d devices; want 2", len(devices))
	}

	byName := make(map[string]tailscale.Device, 2)
	for _, d := range devices {
		byName[d.Hostname] = d
	}

	worker, ok := byName["worker"]
	if !ok {
		t.Fatal("no device with hostname \"worker\"")
	}
	if worker.TailscaleIP != "100.64.0.1" {
		t.Errorf("worker IP = %q; want \"100.64.0.1\"", worker.TailscaleIP)
	}
	if !worker.Online {
		t.Error("worker.Online = false; want true")
	}

	control, ok := byName["control"]
	if !ok {
		t.Fatal("no device with hostname \"control\"")
	}
	if control.TailscaleIP != "100.64.0.2" {
		t.Errorf("control IP = %q; want \"100.64.0.2\"", control.TailscaleIP)
	}
	if !control.Online {
		t.Error("control.Online = false; want true")
	}
}

func TestStaticSource_DefaultIPs(t *testing.T) {
	// Ensure env vars are unset so defaults kick in.
	t.Setenv("ATLAS_WORKER_HOST_IP", "")
	t.Setenv("ATLAS_CONTROL_HOST_IP", "")

	src := tailscale.StaticSource{}
	devices, err := src.Devices(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(devices) != 2 {
		t.Fatalf("got %d devices; want 2", len(devices))
	}
	// When env vars are empty, fallback should be "127.0.0.1".
	for _, d := range devices {
		if d.TailscaleIP != "127.0.0.1" {
			t.Errorf("device %q IP = %q; want \"127.0.0.1\"", d.Hostname, d.TailscaleIP)
		}
	}
}

// ---------------------------------------------------------------------------
// TestCLISource_ParseError
// ---------------------------------------------------------------------------

// TestCLISource_BinaryNotFound verifies CLISource returns an error when the
// tailscale binary is not on PATH.
func TestCLISource_BinaryNotFound(t *testing.T) {
	// Override PATH to a directory with no tailscale binary.
	t.Setenv("PATH", "/nonexistent-dir")

	src := tailscale.CLISource{}
	_, err := src.Devices(context.Background())
	if err == nil {
		t.Fatal("expected error when tailscale binary is not found; got nil")
	}
}

// TestCLISource_ParseError writes a fake tailscale script that emits invalid
// JSON and asserts that CLISource returns a parse error.
func TestCLISource_ParseError(t *testing.T) {
	// Write a fake "tailscale" script that emits invalid JSON.
	dir := t.TempDir()
	fakeTailscale := dir + "/tailscale"
	script := "#!/bin/sh\necho 'not valid json'\n"
	// Write at 0o600 (owner read/write only — gosec G306 requires ≤0o600 on
	// WriteFile), then add the execute bit via chmod so the shell can run
	// it. Final mode 0o700: owner-only read/write/execute, no group/world
	// access. This is strictly more secure than the prior 0o755.
	if err := os.WriteFile(fakeTailscale, []byte(script), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	// 0o700 is the minimum mode that makes the test script executable. The
	// file lives in t.TempDir() (per-test private directory, mode 0o700,
	// owned by the test runner UID), and only the test runner needs to run
	// it. No group/world bits at any point — strictly more secure than the
	// prior 0o755. gosec G302 still flags any chmod above 0o600; that rule
	// is incorrect here because it cannot model "this file MUST be
	// executable for the test to work."
	if err := os.Chmod(fakeTailscale, 0o700); err != nil { //nolint:gosec // see comment above
		t.Fatalf("Chmod: %v", err)
	}

	// Put our fake script at the front of PATH.
	origPath := os.Getenv("PATH")
	t.Setenv("PATH", dir+":"+origPath)

	// Verify our fake script is picked up.
	if p, err := exec.LookPath("tailscale"); err != nil || p != fakeTailscale {
		t.Skipf("fake tailscale not first in PATH (got %q, %v); skipping", p, err)
	}

	src := tailscale.CLISource{}
	_, err := src.Devices(context.Background())
	if err == nil {
		t.Fatal("expected parse error; got nil")
	}
}

// ---------------------------------------------------------------------------
// TestAPISource
// ---------------------------------------------------------------------------

// TestAPISource spins up an httptest.Server returning sample Tailscale API JSON
// and asserts that two devices are correctly parsed.
func TestAPISource(t *testing.T) {
	t.Parallel()

	now := time.Now().UTC().Truncate(time.Second)
	payload := map[string]interface{}{
		"devices": []map[string]interface{}{
			{
				"hostname":  "host-a",
				"addresses": []string{"100.100.0.1", "fd7a::1"},
				"online":    true,
				"lastSeen":  now.Format(time.RFC3339),
			},
			{
				"hostname":  "host-b",
				"addresses": []string{"100.100.0.2"},
				"online":    false,
				"lastSeen":  now.Add(-time.Minute).Format(time.RFC3339),
			},
		},
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify Authorization header.
		auth := r.Header.Get("Authorization")
		if auth != "Bearer test-key" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(payload); err != nil {
			t.Errorf("encode: %v", err)
		}
	}))
	t.Cleanup(srv.Close)

	// Point the API source at our test server via a custom transport that
	// rewrites the host in outgoing requests.
	src := tailscale.APISource{
		APIKey:  "test-key",
		Tailnet: "example.com",
		HTTPClient: &http.Client{
			Transport: &rewriteTransport{
				base:    srv.Client().Transport,
				baseURL: srv.URL,
			},
		},
	}

	devices, err := src.Devices(context.Background())
	if err != nil {
		t.Fatalf("APISource.Devices: %v", err)
	}

	if len(devices) != 2 {
		t.Fatalf("got %d devices; want 2", len(devices))
	}

	if devices[0].Hostname != "host-a" {
		t.Errorf("devices[0].Hostname = %q; want \"host-a\"", devices[0].Hostname)
	}
	if devices[0].TailscaleIP != "100.100.0.1" {
		t.Errorf("devices[0].TailscaleIP = %q; want \"100.100.0.1\"", devices[0].TailscaleIP)
	}
	if !devices[0].Online {
		t.Error("devices[0].Online = false; want true")
	}
	if devices[1].Hostname != "host-b" {
		t.Errorf("devices[1].Hostname = %q; want \"host-b\"", devices[1].Hostname)
	}
	if devices[1].Online {
		t.Error("devices[1].Online = true; want false")
	}
}

// rewriteTransport replaces the host in any outgoing request with the test
// server base URL, allowing APISource to be tested without hitting the real
// Tailscale API.
type rewriteTransport struct {
	base    http.RoundTripper
	baseURL string
}

func (rt *rewriteTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	cloned := req.Clone(req.Context())
	// Parse the test server URL to get scheme and host.
	srv, err := http.NewRequest(http.MethodGet, rt.baseURL, nil)
	if err != nil {
		return nil, err
	}
	cloned.URL.Scheme = srv.URL.Scheme
	cloned.URL.Host = srv.URL.Host
	return rt.base.RoundTrip(cloned)
}

// ---------------------------------------------------------------------------
// TestAutoSource_FallsThrough
// ---------------------------------------------------------------------------

// TestAutoSource_FallsThrough verifies that when CLI fails (binary not found)
// and no API credentials are provided, AutoSource falls through to StaticSource.
func TestAutoSource_FallsThrough(t *testing.T) {
	t.Setenv("ATLAS_WORKER_HOST_IP", "10.0.0.1")
	t.Setenv("ATLAS_CONTROL_HOST_IP", "10.0.0.2")
	// Force CLI to fail by pointing PATH to an empty directory.
	t.Setenv("PATH", t.TempDir())

	// AutoSource with no API credentials: CLI fails, API skipped, static used.
	auto := tailscale.AutoSource{} // Cfg is nil -> no API attempted
	devices, err := auto.Devices(context.Background())
	if err != nil {
		t.Fatalf("AutoSource.Devices: unexpected error: %v", err)
	}
	if len(devices) != 2 {
		t.Fatalf("got %d devices; want 2", len(devices))
	}

	byName := make(map[string]tailscale.Device, 2)
	for _, d := range devices {
		byName[d.Hostname] = d
	}
	if byName["worker"].TailscaleIP != "10.0.0.1" {
		t.Errorf("worker IP = %q; want \"10.0.0.1\"", byName["worker"].TailscaleIP)
	}
	if byName["control"].TailscaleIP != "10.0.0.2" {
		t.Errorf("control IP = %q; want \"10.0.0.2\"", byName["control"].TailscaleIP)
	}
}

// ---------------------------------------------------------------------------
// TestRefresher_UpdatesCache
// ---------------------------------------------------------------------------

// TestRefresher_UpdatesCache runs the refresher with a 50ms interval for 120ms,
// asserts the cache has devices, then cancels the context and verifies the
// goroutine exits cleanly within 200ms.
func TestRefresher_UpdatesCache(t *testing.T) {
	t.Setenv("ATLAS_WORKER_HOST_IP", "192.168.1.1")
	t.Setenv("ATLAS_CONTROL_HOST_IP", "192.168.1.2")

	src := tailscale.StaticSource{}
	cache := store.NewCache()
	r := tailscale.NewRefresher(src, cache, 50*time.Millisecond)

	ctx, cancel := context.WithCancel(context.Background())

	done := make(chan struct{})
	go func() {
		defer close(done)
		r.Start(ctx)
	}()

	// Wait up to 120ms for the cache to be populated (initial poll is immediate).
	deadline := time.Now().Add(120 * time.Millisecond)
	for time.Now().Before(deadline) {
		if d := cache.GetDevices(); len(d) > 0 {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}

	devices := cache.GetDevices()
	if len(devices) == 0 {
		t.Fatal("cache has no devices after 120ms; expected the refresher to populate it")
	}
	if len(devices) != 2 {
		t.Fatalf("got %d devices; want 2", len(devices))
	}

	// Cancel and verify the goroutine exits.
	cancel()
	select {
	case <-done:
		// Goroutine exited cleanly -- no leak.
	case <-time.After(200 * time.Millisecond):
		t.Error("refresher goroutine did not exit within 200ms after context cancel")
	}
}
