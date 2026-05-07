package server

import (
	"encoding/base64"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

// okHandler returns 200 OK for any request; used as the downstream handler in tests.
var okHandler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
})

func applyMiddleware(mode AuthMode, user, pass, token string, next http.Handler) http.Handler {
	return Middleware(mode, user, pass, token)(next)
}

// --- AuthNone ---

func TestAuthNone_AllRequestsPass(t *testing.T) {
	handler := applyMiddleware(AuthNone, "", "", "", okHandler)

	for _, path := range []string{"/", "/healthz", "/events?token=whatever"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		if rr.Code != http.StatusOK {
			t.Errorf("path %q: expected 200, got %d", path, rr.Code)
		}
	}
}

// --- AuthBearer ---

func TestAuthBearer_NoToken_Returns401(t *testing.T) {
	handler := applyMiddleware(AuthBearer, "", "", "secret", okHandler)

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusUnauthorized {
		t.Errorf("expected 401, got %d", rr.Code)
	}
}

func TestAuthBearer_CorrectHeaderToken_Returns200(t *testing.T) {
	handler := applyMiddleware(AuthBearer, "", "", "secret", okHandler)

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("Authorization", "Bearer secret")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rr.Code)
	}
}

func TestAuthBearer_CorrectQueryToken_Returns200(t *testing.T) {
	handler := applyMiddleware(AuthBearer, "", "", "secret", okHandler)

	req := httptest.NewRequest(http.MethodGet, "/?token=secret", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rr.Code)
	}
}

func TestAuthBearer_WrongToken_Returns401(t *testing.T) {
	handler := applyMiddleware(AuthBearer, "", "", "secret", okHandler)

	for _, tc := range []struct {
		name string
		req  *http.Request
	}{
		{
			name: "wrong header token",
			req: func() *http.Request {
				r := httptest.NewRequest(http.MethodGet, "/", nil)
				r.Header.Set("Authorization", "Bearer wrong")
				return r
			}(),
		},
		{
			name: "wrong query token",
			req:  httptest.NewRequest(http.MethodGet, "/?token=wrong", nil),
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rr := httptest.NewRecorder()
			handler.ServeHTTP(rr, tc.req)
			if rr.Code != http.StatusUnauthorized {
				t.Errorf("expected 401, got %d", rr.Code)
			}
		})
	}
}

// --- AuthBasic ---

func TestAuthBasic_WrongCreds_Returns401(t *testing.T) {
	handler := applyMiddleware(AuthBasic, "admin", "hunter2", "", okHandler)

	for _, tc := range []struct {
		name string
		req  *http.Request
	}{
		{
			name: "no auth header",
			req:  httptest.NewRequest(http.MethodGet, "/", nil),
		},
		{
			name: "wrong password",
			req: func() *http.Request {
				r := httptest.NewRequest(http.MethodGet, "/", nil)
				r.Header.Set("Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte("admin:wrong")))
				return r
			}(),
		},
		{
			name: "wrong username",
			req: func() *http.Request {
				r := httptest.NewRequest(http.MethodGet, "/", nil)
				r.Header.Set("Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte("root:hunter2")))
				return r
			}(),
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rr := httptest.NewRecorder()
			handler.ServeHTTP(rr, tc.req)
			if rr.Code != http.StatusUnauthorized {
				t.Errorf("%s: expected 401, got %d", tc.name, rr.Code)
			}
			// Must include WWW-Authenticate header.
			if rr.Header().Get("WWW-Authenticate") == "" {
				t.Errorf("%s: missing WWW-Authenticate header", tc.name)
			}
		})
	}
}

func TestAuthBasic_CorrectCreds_Returns200(t *testing.T) {
	handler := applyMiddleware(AuthBasic, "admin", "hunter2", "", okHandler)

	creds := base64.StdEncoding.EncodeToString([]byte("admin:hunter2"))
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("Authorization", "Basic "+creds)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rr.Code)
	}
}

func TestAuthBearer_EmptyConfiguredToken_Returns401(t *testing.T) {
	// When ATLAS_AUTH_BEARER_TOKEN is unset (empty string), bearer mode must
	// reject all requests — including ones that send an empty Bearer value.
	handler := applyMiddleware(AuthBearer, "", "", "", okHandler)

	for _, tc := range []struct {
		name string
		req  *http.Request
	}{
		{"no auth", httptest.NewRequest(http.MethodGet, "/", nil)},
		{
			"empty bearer header",
			func() *http.Request {
				r := httptest.NewRequest(http.MethodGet, "/", nil)
				r.Header.Set("Authorization", "Bearer ")
				return r
			}(),
		},
		{"empty query token", httptest.NewRequest(http.MethodGet, "/?token=", nil)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rr := httptest.NewRecorder()
			handler.ServeHTTP(rr, tc.req)
			if rr.Code != http.StatusUnauthorized {
				t.Errorf("expected 401, got %d", rr.Code)
			}
		})
	}
}

// --- SSE / EventSource compatibility ---

func TestAuthBearer_SSE_QueryToken_Returns200(t *testing.T) {
	handler := applyMiddleware(AuthBearer, "", "", "ssesecret", okHandler)

	// EventSource sends Accept: text/event-stream and cannot set custom headers,
	// so the token must be passed via ?token=.
	req := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/events?token=%s", "ssesecret"), nil)
	req.Header.Set("Accept", "text/event-stream")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200 for SSE with ?token=, got %d", rr.Code)
	}
}

// TestMiddleware_UnknownModeFailsClosed locks in the defense-in-depth half
// of the v0.2.0 auth-bypass fix. Originally the middleware's switch had a
// default branch that fell through to next.ServeHTTP — so any value other
// than the three typed constants ("none", "basic", "bearer") silently
// allowed every request. config.Validate now normalizes the value at startup
// AND rejects unknown modes, but if anything ever bypasses Validate (for
// example a future code path that constructs Server with a hand-built
// Config), the middleware itself must refuse to fail open.
func TestMiddleware_UnknownModeFailsClosed(t *testing.T) {
	for _, mode := range []AuthMode{
		AuthMode("Bearer"), // mixed case — the exact bypass shape from v0.2.0
		AuthMode("BEARER"),
		AuthMode("magic"),
		AuthMode(""),
	} {
		handler := applyMiddleware(mode, "u", "p", "secret", okHandler)
		req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		if rr.Code != http.StatusUnauthorized {
			t.Errorf("mode %q: expected 401, got %d (middleware must fail closed on unknown mode)", string(mode), rr.Code)
		}
	}
}
