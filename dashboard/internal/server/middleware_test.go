package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/HomericIntelligence/atlas/internal/config"
)

// minimalServer is the smallest Server value needed to invoke
// securityHeaders. The middleware reads cfg.GrafanaURL / LokiURL /
// NATSDashboardURL only.
func minimalServer(t *testing.T, grafana, loki, natsDash string) *Server {
	t.Helper()
	return &Server{
		cfg: &config.Config{
			GrafanaURL:       grafana,
			LokiURL:          loki,
			NATSDashboardURL: natsDash,
		},
	}
}

// runMiddleware invokes securityHeaders on a no-op next handler and returns
// the recorded response.
func runMiddleware(t *testing.T, s *Server) *httptest.ResponseRecorder {
	t.Helper()
	rr := httptest.NewRecorder()
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	s.securityHeaders(next).ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/", nil))
	return rr
}

func TestSecurityHeaders_AlwaysSetsBaseline(t *testing.T) {
	rr := runMiddleware(t, minimalServer(t, "http://grafana:3000", "http://loki:3100", ""))
	for header, want := range map[string]string{
		"X-Content-Type-Options": "nosniff",
		"X-Frame-Options":        "SAMEORIGIN",
		"Referrer-Policy":        "strict-origin-when-cross-origin",
	} {
		if got := rr.Header().Get(header); got != want {
			t.Errorf("%s: got %q, want %q", header, got, want)
		}
	}
}

func TestSecurityHeaders_CSPIncludesGrafanaAndLoki(t *testing.T) {
	rr := runMiddleware(t, minimalServer(t, "http://grafana:3000", "http://loki:3100", ""))
	csp := rr.Header().Get("Content-Security-Policy")
	if !strings.Contains(csp, "frame-src 'self' http://grafana:3000 http://loki:3100") {
		t.Errorf("CSP frame-src missing grafana/loki: %s", csp)
	}
	if !strings.Contains(csp, "default-src 'self'") {
		t.Errorf("CSP missing default-src 'self': %s", csp)
	}
}

func TestSecurityHeaders_CSPOmitsEmptyNATSDashboard(t *testing.T) {
	rr := runMiddleware(t, minimalServer(t, "http://grafana:3000", "http://loki:3100", ""))
	csp := rr.Header().Get("Content-Security-Policy")
	// frame-src should end after loki; no trailing token from empty NATSDashboardURL.
	if strings.Contains(csp, "frame-src 'self' http://grafana:3000 http://loki:3100 ") {
		t.Errorf("CSP has trailing token after loki even though NATSDashboardURL is empty: %s", csp)
	}
}

func TestSecurityHeaders_CSPIncludesNATSDashboardWhenSet(t *testing.T) {
	rr := runMiddleware(t, minimalServer(t, "http://grafana:3000", "http://loki:3100", "http://nats-dash:8081"))
	csp := rr.Header().Get("Content-Security-Policy")
	if !strings.Contains(csp, "http://nats-dash:8081") {
		t.Errorf("CSP missing NATSDashboardURL: %s", csp)
	}
}

// TestSecurityHeaders_ValidatedURLsCannotInjectCSP is a regression test for
// the audit's S8 CRITICAL #3 finding. config.Validate (called at startup)
// rejects URLs containing whitespace, semicolons, or quotes — values that
// would otherwise split the CSP frame-src directive when concatenated here.
// We assert that securityHeaders behaves correctly on the post-validation
// (clean) input it actually receives in production, by checking the CSP
// header has no embedded ';' beyond the canonical directive separators.
func TestSecurityHeaders_NoUnexpectedSemicolons(t *testing.T) {
	rr := runMiddleware(t, minimalServer(t, "http://grafana:3000", "http://loki:3100", "http://nats-dash:8081"))
	csp := rr.Header().Get("Content-Security-Policy")
	// Canonical directives are separated by "; ". Count semicolons; the
	// expected value is one less than the number of directives:
	// default-src; script-src; connect-src; style-src; img-src; frame-src
	// → 5 semicolons.
	const want = 5
	if got := strings.Count(csp, ";"); got != want {
		t.Errorf("CSP semicolon count: got %d, want %d (directive injection?). header=%q",
			got, want, csp)
	}
}
