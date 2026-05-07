package server

import (
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

func (s *Server) routes() http.Handler {
	r := chi.NewRouter()
	// We deliberately do NOT use middleware.RealIP. RealIP rewrites
	// r.RemoteAddr from any client-supplied X-Forwarded-For / X-Real-IP
	// header — which is correct only when an upstream proxy strips and
	// re-issues those headers from a trusted source. Atlas exposes :3002
	// directly in compose with no upstream proxy, so trusting client-
	// supplied XFF lets anyone spoof the source IP that ends up in
	// access logs and (post #460) in auth-failure security events. If a
	// future deployment runs Atlas behind nginx/Caddy/etc., the right
	// fix is to gate this on an explicit ATLAS_TRUST_PROXY env var, not
	// to re-enable RealIP unconditionally.
	// accessLog replaces chi's default middleware.Logger to avoid leaking the
	// SSE bearer token (passed as ?token=<secret>) into stdout/Loki. The
	// default Logger formats the full RequestURI; accessLog logs r.URL.Path
	// only and drops the query string entirely.
	r.Use(accessLog(slog.Default()))
	r.Use(middleware.Recoverer)
	r.Use(s.securityHeaders)

	// /livez is the unauthenticated liveness probe required by k8s — it must
	// always return 200 if the process is up. /healthz is kept as an alias for
	// back-compat. /readyz and /metrics are auth-gated below: readiness reveals
	// upstream component status (operational data), and metrics expose internal
	// Prometheus state that should not be public.
	//
	// The liveness routes get their own (higher) rate-limit budget because
	// kubelets, sidecar agents, and external monitors typically probe them
	// every few seconds — the standard 30/min budget that protects every
	// other route would trip those probes and make k8s mark the pod dead.
	// Default LivezRateLimitPerMin is 240 (k8s 5s probes = 12/min, with
	// generous headroom for sidecars + retries).
	r.Group(func(r chi.Router) {
		r.Use(rateLimit(s.cfg.LivezRateLimitPerMin))
		r.Get("/livez", s.handleLivez)
		r.Get("/healthz", s.handleLivez)
	})

	r.Group(func(r chi.Router) {
		// Per-IP rate limit applies BEFORE auth — exhausting an IP's
		// budget on /readyz or /metrics returns 429 without ever
		// invoking the auth path, which costs less than constant-time
		// token comparison and saves an auth-failure log line per
		// scanner request. Default RateLimitPerMin is 30.
		r.Use(rateLimit(s.cfg.RateLimitPerMin))
		r.Use(Middleware(AuthMode(s.cfg.AuthMode), s.cfg.AuthUser, s.cfg.AuthPass, s.cfg.AuthBearerToken))

		r.Get("/readyz", MakeReadyzHandler(s.ready))
		r.Get("/metrics", s.MetricsHandler())

		r.Get("/", s.handleOverview)
		r.Get("/hosts", s.hostsHandler.ServeHTTP)
		r.Get("/api/hosts", s.apiHandler.ServeHTTP)
		r.Get("/partials/host/{name}", s.hostsHandler.Partial)
		r.Get("/agents", s.hostsHandler.AgentsPage)
		r.Get("/partials/agents/table", s.hostsHandler.AgentsTablePartial)
		r.Get("/agents/{id}", s.hostsHandler.AgentDetail)
		r.Get("/tasks/{id}", s.hostsHandler.TaskDetail)
		r.Get("/grafana", s.hostsHandler.GrafanaPage)
		r.Get("/nats", s.hostsHandler.NATSPage)
		r.Get("/partials/nats/streams", s.hostsHandler.NATSStreamsPartial)
		r.Get("/partials/nats/connections", s.hostsHandler.NATSConnsPartial)
		r.Get("/mnemosyne", s.hostsHandler.MnemosynePage)
		r.Get("/partials/mnemosyne/search", s.hostsHandler.MnemosyneSearch)
		r.Get("/partials/mnemosyne/skill/{name}", s.hostsHandler.MnemosyneSkillBody)
		r.Get("/events", s.sse.ServeHTTP)
		r.Handle("/static/*", http.StripPrefix("/static/", http.FileServer(http.Dir("web/static"))))
	})

	return r
}

// handleLivez is the unauthenticated liveness probe. It returns 200 if the
// process is up — it does NOT validate upstream connectivity. Use /readyz for
// component-level readiness aggregation.
func (s *Server) handleLivez(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func (s *Server) handleOverview(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(overviewHTML)
}
