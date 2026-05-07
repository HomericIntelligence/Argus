package server

import (
	"bytes"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/HomericIntelligence/atlas/internal/config"
	"github.com/HomericIntelligence/atlas/internal/events"
	"github.com/HomericIntelligence/atlas/internal/store"
)

// captureSlogForRealIPTest swaps slog.Default() for a buffered logger
// for the duration of the calling test, restoring on cleanup. Inlined
// here (rather than imported) because this PR is stacked off the v0.2.1
// security base; PR #460 introduces a sibling helper of the same shape
// in auth_test.go. After both merge, consolidate into a single
// test-helper file.
func captureSlogForRealIPTest(t *testing.T) *bytes.Buffer {
	t.Helper()
	prev := slog.Default()
	var buf bytes.Buffer
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return &buf
}

// TestRoutes_DoesNotTrustForwardedFor is a regression test for the §8
// audit MAJOR finding "middleware.RealIP trusts X-Forwarded-For
// unconditionally." Atlas exposes :3002 directly in compose with no
// upstream proxy stripping XFF — so trusting client-supplied XFF
// would let any reachable client spoof the source IP that lands in
// access logs and (post #460) in the auth-failure security event
// stream.
//
// We exercise the real s.routes() middleware chain so this test
// catches a future regression where someone re-adds
// middleware.RealIP without realising the deployment shape.
func TestRoutes_DoesNotTrustForwardedFor(t *testing.T) {
	s := New(&config.Config{
		AuthMode: "none", // pass-through; we want to exercise middleware not auth
	}, events.NewBus(8), store.NewCache())

	// Build a request with a spoofed X-Forwarded-For chain. /livez is
	// unauthenticated and goes through the same middleware stack as
	// every other route — so a successful regression test on /livez
	// proves the property for all routes.
	req := httptest.NewRequest(http.MethodGet, "/livez", nil)
	req.Header.Set("X-Forwarded-For", "1.2.3.4, 5.6.7.8")
	req.Header.Set("X-Real-IP", "9.10.11.12")
	req.RemoteAddr = "127.0.0.1:54321" // what the kernel sees

	rr := httptest.NewRecorder()
	s.routes().ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("/livez expected 200, got %d", rr.Code)
	}

	// The downstream handler doesn't echo RemoteAddr, but the access
	// log middleware does. Capture the slog output and assert that
	// the log line carries the kernel-visible RemoteAddr, NOT the
	// spoofed XFF / X-Real-IP values.
	//
	// We re-issue with slog captured this time. The first ServeHTTP
	// call above also exercised the chain end-to-end; this second
	// call gives us the inspectable log line.
	buf := captureSlogForRealIPTest(t)
	rr2 := httptest.NewRecorder()
	s.routes().ServeHTTP(rr2, req)

	out := buf.String()
	for _, spoofed := range []string{"1.2.3.4", "5.6.7.8", "9.10.11.12"} {
		if strings.Contains(out, spoofed) {
			t.Errorf("access log contained spoofed IP %q — RealIP must not be trusted on a directly-exposed port.\nlog:\n%s", spoofed, out)
		}
	}
	if !strings.Contains(out, "127.0.0.1:54321") {
		t.Errorf("access log missing the kernel-visible RemoteAddr 127.0.0.1:54321.\nlog:\n%s", out)
	}
}
