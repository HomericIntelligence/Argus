package server

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/HomericIntelligence/atlas/internal/config"
	"github.com/HomericIntelligence/atlas/internal/events"
	"github.com/HomericIntelligence/atlas/internal/handlers"
	"github.com/HomericIntelligence/atlas/internal/mnemosyne"
	"github.com/HomericIntelligence/atlas/internal/store"
)

type Server struct {
	cfg          *config.Config
	srv          *http.Server
	bus          *events.Bus
	sse          *handlers.SSE
	apiHandler   *handlers.Hosts
	hostsHandler *handlers.HostsHandler
	metrics      *AtlasMetrics
	ready        *ReadyRegistry
}

func New(cfg *config.Config, bus *events.Bus, cache *store.Cache) *Server {
	metrics := newAtlasMetrics()
	// Apply SSE tunables from cfg before constructing the handler so the
	// first ServeHTTP call sees the operator-configured values. These are
	// process-wide atomics; the SSE handler reads them on every connect.
	if cfg.SSEHeartbeatInterval > 0 {
		handlers.SetHeartbeatInterval(cfg.SSEHeartbeatInterval)
	}
	handlers.SetSubscriberBuffer(cfg.SSESubscriberBuffer)
	sse := handlers.NewSSE(bus)
	sse.SetMetrics(metrics)

	s := &Server{
		cfg:        cfg,
		bus:        bus,
		sse:        sse,
		apiHandler: handlers.NewHosts(cache),
		hostsHandler: handlers.NewHostsHandler(cache).
			WithGrafanaURL(cfg.GrafanaURL).
			WithNATSURLs(cfg.NATSDashboardURL, cfg.NATSTopURL, cfg.NATSMonURL).
			WithMnemoReader(mnemosyne.NewReader(cfg.MnemosyneSkillsDir)),
		metrics: metrics,
		ready:   &ReadyRegistry{},
	}
	s.srv = &http.Server{
		Addr:         cfg.ListenAddr,
		Handler:      s.routes(),
		ReadTimeout:  cfg.HTTPReadTimeout,
		WriteTimeout: 0, // SSE connections are long-lived; disable write timeout.
		IdleTimeout:  cfg.HTTPIdleTimeout,
	}
	return s
}

// Metrics exposes the *AtlasMetrics instance so the composition root can wire
// it into pollers and the NATS subscriber via their SetMetrics methods.
func (s *Server) Metrics() *AtlasMetrics {
	return s.metrics
}

// Ready exposes the readiness registry so the composition root can register
// per-component checks (one per poller, one for the NATS subscriber, etc.).
func (s *Server) Ready() *ReadyRegistry {
	return s.ready
}

func (s *Server) Run(ctx context.Context) error {
	errCh := make(chan error, 1)
	go func() {
		if err := s.srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		shutCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		return s.srv.Shutdown(shutCtx)
	}
}

// MetricsHandler returns an http.HandlerFunc that exposes Prometheus metrics.
func (s *Server) MetricsHandler() http.HandlerFunc {
	return s.metrics.Handler()
}
