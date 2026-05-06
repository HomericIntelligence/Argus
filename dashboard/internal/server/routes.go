package server

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

func (s *Server) routes() http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.RealIP)
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(s.securityHeaders)

	// /livez is the unauthenticated liveness probe required by k8s — it must
	// always return 200 if the process is up. /healthz is kept as an alias for
	// back-compat. /readyz and /metrics are auth-gated below: readiness reveals
	// upstream component status (operational data), and metrics expose internal
	// Prometheus state that should not be public.
	r.Get("/livez", s.handleLivez)
	r.Get("/healthz", s.handleLivez)

	r.Group(func(r chi.Router) {
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
func (s *Server) handleLivez(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func (s *Server) handleOverview(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write(overviewHTML) //nolint:errcheck
}
