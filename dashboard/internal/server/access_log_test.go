package server

import (
	"bytes"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestAccessLog_DoesNotLeakQueryString is a regression test for the v0.2.0
// SSE bearer-token leak: chi's default middleware.Logger writes the full
// RequestURI (path + raw query) to stdout, so SSE clients authenticating via
// ?token=<secret> (which they must, because EventSource cannot set custom
// headers) end up with their bearer tokens persisted to container logs and
// any downstream aggregator (Promtail/Loki, k8s log scrape, etc.).
//
// accessLog logs r.URL.Path explicitly and NEVER serializes the query
// string. This test asserts that property by hitting a representative SSE
// path with a recognisable secret query value and verifying the secret does
// not appear anywhere in the captured log output.
func TestAccessLog_DoesNotLeakQueryString(t *testing.T) {
	const secret = "ssetoken-must-not-appear-in-logs-12345"

	var buf bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&buf, nil))

	handler := accessLog(logger)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	for _, path := range []string{
		"/events?token=" + secret,
		"/events?topics=agent&token=" + secret,
		"/api/hosts?secret=" + secret,         // any future query-string secret
		"/?password=" + secret + "&other=foo", // belt-and-suspenders coverage
	} {
		t.Run(path, func(t *testing.T) {
			buf.Reset()
			req := httptest.NewRequest(http.MethodGet, path, nil)
			rr := httptest.NewRecorder()
			handler.ServeHTTP(rr, req)
			if rr.Code != http.StatusOK {
				t.Fatalf("expected 200, got %d", rr.Code)
			}
			if strings.Contains(buf.String(), secret) {
				t.Fatalf("access log leaked query-string secret %q.\nlog output was: %s", secret, buf.String())
			}
		})
	}
}

// TestAccessLog_LogsPathAndStatus asserts the logger still does its primary
// job — incident triage needs method, path, and status to be present.
func TestAccessLog_LogsPathAndStatus(t *testing.T) {
	var buf bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&buf, nil))
	handler := accessLog(logger)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusTeapot)
	}))

	req := httptest.NewRequest(http.MethodGet, "/agents?token=dropme", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	out := buf.String()
	for _, want := range []string{
		"method=GET",
		"path=/agents",
		"status=418",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("access log missing %q.\nlog output: %s", want, out)
		}
	}
	// Belt: also ensure the query string is gone from the path field
	// regardless of secret content.
	if strings.Contains(out, "token=") {
		t.Errorf("access log unexpectedly contained query string. log output: %s", out)
	}
}
