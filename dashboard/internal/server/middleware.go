package server

import (
	"fmt"
	"net/http"
)

// securityHeaders adds baseline HTTP security headers to every response.
// The frame-src directive includes the configured Grafana, Loki, and (when set)
// NATS dashboard URLs so that embedded iframes are permitted by CSP.
// X-Frame-Options is set to SAMEORIGIN to allow the Atlas dashboard to embed
// its own panels.
//
// The iframe-target URLs are guaranteed safe to interpolate here because
// config.Config.Validate (called at startup in cmd/argus-dashboard/main.go)
// rejects any value containing whitespace, semicolons, quotes, or non-http(s)
// schemes. Do NOT concatenate user-controlled or unvalidated strings into
// header values without re-running that validation.
func (s *Server) securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "SAMEORIGIN")
		w.Header().Set("Referrer-Policy", "strict-origin-when-cross-origin")

		frameSrc := fmt.Sprintf("'self' %s %s", s.cfg.GrafanaURL, s.cfg.LokiURL)
		if s.cfg.NATSDashboardURL != "" {
			frameSrc += " " + s.cfg.NATSDashboardURL
		}
		w.Header().Set(
			"Content-Security-Policy",
			fmt.Sprintf(
				"default-src 'self'; script-src 'self' https://unpkg.com; connect-src 'self'; style-src 'self'; img-src 'self' data:; frame-src %s",
				frameSrc,
			),
		)
		next.ServeHTTP(w, r)
	})
}
