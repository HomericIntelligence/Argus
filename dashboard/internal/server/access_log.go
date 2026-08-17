// Package server constructs the Atlas HTTP server: routes, middleware
// (auth, rate-limit, access log, security headers), and the readiness
// registry that the composition root populates.
package server

import (
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5/middleware"

	"github.com/HomericIntelligence/atlas/internal/logsafe"
)

// accessLog is a minimal request logger that does NOT include the request's
// query string or headers in the log line. It replaces chi's default
// middleware.Logger which writes the full RequestURI — that path leaks the
// SSE bearer token (passed as ?token=<secret> by EventSource clients) to
// stdout and onward to any log aggregator (Promtail/Loki, k8s log scrape,
// etc.).
//
// We intentionally log only: method, status, response bytes, route path,
// remote addr, and elapsed duration. That is enough for incident triage
// without ever serializing a query string. If a future feature needs to log
// query parameters, it must do so explicitly with an allow-list — never the
// raw URI.
func accessLog(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
			next.ServeHTTP(ww, r)
			logger.Info(
				"http",
				"method", r.Method,
				"path", logsafe.Value(r.URL.Path), // path only — never RequestURI / RawQuery
				"status", ww.Status(),
				"bytes", ww.BytesWritten(),
				"duration_ms", time.Since(start).Milliseconds(),
				"remote", logsafe.Value(r.RemoteAddr),
			)
		})
	}
}
