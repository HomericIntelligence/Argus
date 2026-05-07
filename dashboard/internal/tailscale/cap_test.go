package tailscale

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"strings"
	"testing"
)

// This file lives in package tailscale (not tailscale_test) so it can
// override the package-private maxResponseBytes for fast, deterministic
// regression coverage of the §8 audit cap. tailscale_test.go is an
// external test package and cannot reach unexported symbols.

// TestAPISource_BoundedByMaxResponseBytes asserts that an oversized
// upstream response from the Tailscale Central API does not allow Atlas
// to allocate unbounded memory. Hostile/compromised plane scenario.
func TestAPISource_BoundedByMaxResponseBytes(t *testing.T) {
	prev := maxResponseBytes
	maxResponseBytes = 256
	t.Cleanup(func() { maxResponseBytes = prev })

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		// Stream ~10 KiB of valid-looking device array — well over the cap.
		_, _ = w.Write([]byte(`{"devices":[{"hostname":"`))
		_, _ = w.Write([]byte(strings.Repeat("a", 10000)))
		_, _ = w.Write([]byte(`","addresses":["100.100.0.1"],"online":true,"lastSeen":"2026-01-01T00:00:00Z"}]}`))
	}))
	t.Cleanup(srv.Close)

	src := APISource{
		APIKey:  "test-key",
		Tailnet: "example.com",
		HTTPClient: &http.Client{
			Transport: rewriteHostTransport{base: http.DefaultTransport, target: srv.URL},
		},
	}

	if _, err := src.Devices(context.Background()); err == nil {
		t.Fatalf("APISource.Devices must error when upstream exceeds cap; got nil")
	} else if !strings.Contains(err.Error(), "parse JSON") {
		t.Fatalf("error must come from the JSON parse path; got: %v", err)
	}
}

// TestCLISource_BoundedByMaxResponseBytes is the subprocess equivalent.
// We arrange for `tailscale status --json` to emit a multi-KiB JSON
// document, lower the cap, and assert that ReadAll returns truncated
// content that fails to JSON-decode — proving io.LimitReader is active.
func TestCLISource_BoundedByMaxResponseBytes(t *testing.T) {
	prev := maxResponseBytes
	maxResponseBytes = 256
	t.Cleanup(func() { maxResponseBytes = prev })

	dir := t.TempDir()
	fakeTailscale := dir + "/tailscale"
	// Build a >cap-sized JSON payload in Go so we don't have to wrestle
	// with shell quoting. The document is structurally valid JSON; the
	// only reason it fails to decode in the test is that LimitReader
	// truncated it after maxResponseBytes.
	bigName := strings.Repeat("x", 4000)
	bigJSON := `{"Self":{"HostName":"` + bigName + `","TailscaleIPs":["100.0.0.1"],"Online":true,"LastSeen":"2026-01-01T00:00:00Z"},"Peer":{}}`
	// `cat <<'EOF'` heredoc keeps the shell from interpreting any of the
	// JSON content. Single-quoted EOF marker disables variable expansion.
	script := "#!/bin/sh\ncat <<'EOF'\n" + bigJSON + "\nEOF\n"
	if err := os.WriteFile(fakeTailscale, []byte(script), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	if err := os.Chmod(fakeTailscale, 0o700); err != nil { //nolint:gosec // exec bit required for test
		t.Fatalf("Chmod: %v", err)
	}

	t.Setenv("PATH", dir+":"+os.Getenv("PATH"))
	if p, err := exec.LookPath("tailscale"); err != nil || p != fakeTailscale {
		t.Skipf("fake tailscale not first in PATH (got %q, %v); skipping", p, err)
	}

	src := CLISource{}
	if _, err := src.Devices(context.Background()); err == nil {
		t.Fatalf("CLISource.Devices must error when subprocess output exceeds cap; got nil")
	} else if !strings.Contains(err.Error(), "parse JSON") {
		t.Fatalf("error must come from the JSON parse path (proves cap fired before decode); got: %v", err)
	}
}

// rewriteHostTransport is a tiny test helper duplicated from
// tailscale_test.go because that file lives in the external test package.
// It rewrites the outgoing request's scheme+host to point at the test
// server, leaving the path untouched.
type rewriteHostTransport struct {
	base   http.RoundTripper
	target string
}

func (rt rewriteHostTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	cloned := req.Clone(req.Context())
	parsed, err := http.NewRequest(http.MethodGet, rt.target, nil)
	if err != nil {
		return nil, err
	}
	cloned.URL.Scheme = parsed.URL.Scheme
	cloned.URL.Host = parsed.URL.Host
	return rt.base.RoundTrip(cloned)
}
