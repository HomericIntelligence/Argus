package server

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/HomericIntelligence/atlas/internal/config"
	"github.com/HomericIntelligence/atlas/internal/events"
	"github.com/HomericIntelligence/atlas/internal/store"
)

// TestRateLimit_DisabledWhenZero is the escape-hatch contract. Setting
// either ATLAS_RATE_LIMIT_PER_MIN=0 or ATLAS_LIVEZ_RATE_LIMIT_PER_MIN=0
// must turn the corresponding limiter into a transparent pass-through.
// Documented in config.go and rate_limit.go; this test enforces it.
func TestRateLimit_DisabledWhenZero(t *testing.T) {
	mw := rateLimit(0)
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// Hit the same handler 100 times from the same IP — far above any
	// realistic budget. With reqPerMin=0 every one must return 200.
	for i := 0; i < 100; i++ {
		req := httptest.NewRequest(http.MethodGet, "/livez", nil)
		req.RemoteAddr = "10.0.0.1:1234"
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		if rr.Code != http.StatusOK {
			t.Fatalf("request %d: rate-limit-disabled handler must always return 200, got %d", i, rr.Code)
		}
	}
}

// TestRateLimit_EnforcesBudget is the positive contract. Once an IP
// exhausts its per-minute budget, subsequent requests within the window
// must return 429.
func TestRateLimit_EnforcesBudget(t *testing.T) {
	mw := rateLimit(2)
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	hit := func(ip string) int {
		req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
		req.RemoteAddr = ip + ":54321"
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		return rr.Code
	}

	// First two requests from 10.0.0.1 succeed (budget = 2).
	for i := 1; i <= 2; i++ {
		if got := hit("10.0.0.1"); got != http.StatusOK {
			t.Fatalf("request %d from 10.0.0.1: expected 200, got %d", i, got)
		}
	}
	// Third must be 429.
	if got := hit("10.0.0.1"); got != http.StatusTooManyRequests {
		t.Fatalf("request 3 from 10.0.0.1: expected 429, got %d", got)
	}

	// Different IP has its own bucket — must not be affected by 10.0.0.1's
	// exhaustion.
	if got := hit("10.0.0.2"); got != http.StatusOK {
		t.Fatalf("request from 10.0.0.2 must be unaffected by 10.0.0.1's budget; got %d", got)
	}
}

// TestRoutes_LivezAndMainHaveSeparateBuckets is the integration regression
// that matters most. The audit fix splits routes into two groups with
// independent limiters; a future refactor that accidentally collapses them
// into one shared bucket would let `/livez` flooding starve `/readyz`,
// `/metrics`, and the UI (or vice versa).
//
// We exercise the actual s.routes() wiring with budgets of 2 each:
//   - exhaust /livez (3 requests; 3rd is 429)
//   - assert /readyz from the same IP still returns its normal status
//     (401 in this test setup, since auth-mode=bearer and no token sent)
//     — proving /readyz did NOT inherit /livez's exhausted bucket.
func TestRoutes_LivezAndMainHaveSeparateBuckets(t *testing.T) {
	s := New(&config.Config{
		AuthMode:             "bearer",
		AuthBearerToken:      "test-secret",
		RateLimitPerMin:      2,
		LivezRateLimitPerMin: 2,
	}, events.NewBus(8), store.NewCache())

	// Build the handler ONCE so the per-IP rate-limit state persists
	// across requests (httprate keeps its bucket map inside the
	// middleware closure; rebuilding the handler resets it). This
	// matches how the real Server uses routes() — constructed once at
	// startup and reused for every request.
	handler := s.routes()
	const sameIP = "192.0.2.42:9999"

	hit := func(path string) int {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.RemoteAddr = sameIP
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		return rr.Code
	}

	// Exhaust /livez (budget = 2): two 200s then a 429.
	if got := hit("/livez"); got != http.StatusOK {
		t.Fatalf("/livez request 1: expected 200, got %d", got)
	}
	if got := hit("/livez"); got != http.StatusOK {
		t.Fatalf("/livez request 2: expected 200, got %d", got)
	}
	if got := hit("/livez"); got != http.StatusTooManyRequests {
		t.Fatalf("/livez request 3: expected 429 (budget exhausted), got %d", got)
	}

	// /readyz from the SAME IP must still get past the rate limiter and
	// hit the auth middleware (which 401s because we sent no token).
	// A 429 here would mean the two groups share a bucket — regression.
	if got := hit("/readyz"); got != http.StatusUnauthorized {
		t.Fatalf("/readyz must have its own bucket; expected 401 (auth failure), got %d", got)
	}
}

// TestRoutes_MainBudgetAppliesToAuthGatedRoutes is a sibling assertion:
// the main per-IP budget gates /readyz, /metrics, and the UI as
// expected. Verifies the limiter is BEFORE the auth middleware in the
// chain (so 429 fires without invoking constant-time auth comparison or
// emitting an auth-failure log line per scanner request — see the
// comment in routes.go).
func TestRoutes_MainBudgetAppliesToAuthGatedRoutes(t *testing.T) {
	s := New(&config.Config{
		AuthMode:             "bearer",
		AuthBearerToken:      "test-secret",
		RateLimitPerMin:      2,
		LivezRateLimitPerMin: 240,
	}, events.NewBus(8), store.NewCache())

	// See the note in TestRoutes_LivezAndMainHaveSeparateBuckets — build
	// the handler once so httprate's per-IP state survives across requests.
	handler := s.routes()
	const sameIP = "198.51.100.7:1234"

	hit := func(path string) int {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.RemoteAddr = sameIP
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		return rr.Code
	}

	// Two unauthenticated requests to /readyz produce 401 (rate limit
	// allows them through to the auth check, which rejects).
	if got := hit("/readyz"); got != http.StatusUnauthorized {
		t.Fatalf("/readyz request 1: expected 401, got %d", got)
	}
	if got := hit("/readyz"); got != http.StatusUnauthorized {
		t.Fatalf("/readyz request 2: expected 401, got %d", got)
	}
	// Third request: rate limit fires BEFORE auth.
	if got := hit("/readyz"); got != http.StatusTooManyRequests {
		t.Fatalf("/readyz request 3: expected 429 (limit before auth), got %d", got)
	}
}
