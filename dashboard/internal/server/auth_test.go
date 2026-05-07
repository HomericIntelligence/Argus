package server

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
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

// captureSlog redirects slog.Default() into a buffer for the duration of
// the test, returning the buffer and a restore function. Tests must call
// the restore function in a t.Cleanup so failures don't leak the test
// logger into other tests running in parallel.
func captureSlog(t *testing.T) *bytes.Buffer {
	t.Helper()
	prev := slog.Default()
	var buf bytes.Buffer
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return &buf
}

// TestAuth_LogsFailures asserts that every failed auth path emits exactly
// one slog.Warn line with the documented field set, and that the line
// NEVER contains the offered credential or token. Closes the §8 audit
// finding "no audit-log channel for security events; failed auth attempts
// are silently 401'd; not even an slog.Warn in checkBasic/checkBearer."
//
// Each case captures slog output, makes one request, and asserts:
//   - exactly one "auth failure" line was emitted
//   - the expected mode + reason discriminator are present
//   - the offered credential/token does NOT appear anywhere in the line
func TestAuth_LogsFailures(t *testing.T) {
	const offeredSecret = "REDACTED-MUST-NEVER-APPEAR-IN-LOGS"

	cases := []struct {
		name       string
		setup      func() http.Handler
		req        func() *http.Request
		wantMode   string
		wantReason string
	}{
		{
			name:  "bearer missing header",
			setup: func() http.Handler { return applyMiddleware(AuthBearer, "", "", "secret", okHandler) },
			req: func() *http.Request {
				return httptest.NewRequest(http.MethodGet, "/readyz", nil)
			},
			wantMode:   "bearer",
			wantReason: "missing-header",
		},
		{
			name:  "bearer wrong header token",
			setup: func() http.Handler { return applyMiddleware(AuthBearer, "", "", "secret", okHandler) },
			req: func() *http.Request {
				r := httptest.NewRequest(http.MethodGet, "/readyz", nil)
				r.Header.Set("Authorization", "Bearer "+offeredSecret)
				return r
			},
			wantMode:   "bearer",
			wantReason: "wrong-token",
		},
		{
			name:  "bearer wrong query token",
			setup: func() http.Handler { return applyMiddleware(AuthBearer, "", "", "secret", okHandler) },
			req: func() *http.Request {
				return httptest.NewRequest(http.MethodGet, "/events?token="+offeredSecret, nil)
			},
			wantMode:   "bearer",
			wantReason: "wrong-token",
		},
		{
			name:  "bearer empty configured token",
			setup: func() http.Handler { return applyMiddleware(AuthBearer, "", "", "", okHandler) },
			req: func() *http.Request {
				r := httptest.NewRequest(http.MethodGet, "/readyz", nil)
				r.Header.Set("Authorization", "Bearer "+offeredSecret)
				return r
			},
			wantMode:   "bearer",
			wantReason: "empty-configured-token",
		},
		{
			name:  "basic missing header",
			setup: func() http.Handler { return applyMiddleware(AuthBasic, "u", "p", "", okHandler) },
			req: func() *http.Request {
				return httptest.NewRequest(http.MethodGet, "/readyz", nil)
			},
			wantMode:   "basic",
			wantReason: "missing-header",
		},
		{
			name:  "basic wrong creds",
			setup: func() http.Handler { return applyMiddleware(AuthBasic, "u", "p", "", okHandler) },
			req: func() *http.Request {
				r := httptest.NewRequest(http.MethodGet, "/readyz", nil)
				bad := base64.StdEncoding.EncodeToString([]byte("wronguser:" + offeredSecret))
				r.Header.Set("Authorization", "Basic "+bad)
				return r
			},
			wantMode:   "basic",
			wantReason: "wrong-creds",
		},
		{
			name:  "basic malformed base64",
			setup: func() http.Handler { return applyMiddleware(AuthBasic, "u", "p", "", okHandler) },
			req: func() *http.Request {
				r := httptest.NewRequest(http.MethodGet, "/readyz", nil)
				r.Header.Set("Authorization", "Basic !!!not-b64!!!")
				return r
			},
			wantMode:   "basic",
			wantReason: "malformed-base64",
		},
		{
			name:  "unknown mode",
			setup: func() http.Handler { return applyMiddleware(AuthMode("Bearer"), "", "", "secret", okHandler) },
			req: func() *http.Request {
				r := httptest.NewRequest(http.MethodGet, "/readyz", nil)
				r.Header.Set("Authorization", "Bearer "+offeredSecret)
				return r
			},
			wantMode:   "Bearer",
			wantReason: "unknown-mode",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			buf := captureSlog(t)
			handler := tc.setup()

			handler.ServeHTTP(httptest.NewRecorder(), tc.req())

			out := buf.String()
			lines := nonEmptyLines(out)
			if len(lines) != 1 {
				t.Fatalf("want exactly one log line, got %d:\n%s", len(lines), out)
			}
			line := lines[0]
			for _, want := range []string{
				`level=WARN`,
				`msg="auth failure"`,
				`mode=` + quoteIfNeeded(tc.wantMode),
				`reason=` + quoteIfNeeded(tc.wantReason),
			} {
				if !strings.Contains(line, want) {
					t.Errorf("log line missing %q.\nfull line: %s", want, line)
				}
			}
			if strings.Contains(out, offeredSecret) {
				t.Fatalf("LOG LEAKED THE OFFERED CREDENTIAL — this is a security regression.\noutput:\n%s", out)
			}
		})
	}
}

// TestAuth_NoLogOnSuccess asserts the happy path does not log — only
// failures should be in the security event stream.
func TestAuth_NoLogOnSuccess(t *testing.T) {
	buf := captureSlog(t)
	handler := applyMiddleware(AuthBearer, "", "", "secret", okHandler)

	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	req.Header.Set("Authorization", "Bearer secret")
	handler.ServeHTTP(httptest.NewRecorder(), req)

	if out := buf.String(); strings.Contains(out, "auth failure") {
		t.Fatalf("happy path must not emit auth-failure log; got: %s", out)
	}
}

// nonEmptyLines splits s on newlines and drops any empty trailing line
// from a final \n. Avoids spurious "want 1 got 2" failures.
func nonEmptyLines(s string) []string {
	var out []string
	for _, line := range strings.Split(s, "\n") {
		if strings.TrimSpace(line) != "" {
			out = append(out, line)
		}
	}
	return out
}

// quoteIfNeeded wraps a value in double quotes if slog's TextHandler
// would do so. slog quotes strings containing whitespace or special
// characters; bare alphanumerics with hyphens are not quoted. Our
// expected mode/reason values fall into the bare camp, but be defensive
// in case future cases include spaces.
func quoteIfNeeded(v string) string {
	if strings.ContainsAny(v, " \t\"=") || v == "" {
		return `"` + v + `"`
	}
	return v
}
