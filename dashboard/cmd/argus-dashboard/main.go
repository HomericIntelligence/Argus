// Command argus-dashboard is the Atlas dashboard binary: it loads
// configuration, wires the cache/bus/pollers/HTTP server, and serves the
// observability UI for the HomericIntelligence mesh.
package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/HomericIntelligence/atlas/internal/config"
	"github.com/HomericIntelligence/atlas/internal/events"
	atlnats "github.com/HomericIntelligence/atlas/internal/nats"
	"github.com/HomericIntelligence/atlas/internal/poller"
	"github.com/HomericIntelligence/atlas/internal/server"
	"github.com/HomericIntelligence/atlas/internal/store"
	"github.com/HomericIntelligence/atlas/internal/tailscale"
	"github.com/HomericIntelligence/atlas/internal/version"
)

func main() {
	cfg := config.Load()
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel}))
	slog.SetDefault(logger)

	if err := cfg.Validate(logger); err != nil {
		slog.Error("invalid configuration — refusing to start", "err", err)
		os.Exit(2)
	}

	slog.Info("starting atlas", "version", version.Version, "addr", cfg.ListenAddr, "auth_mode", cfg.AuthMode)

	cache := store.NewCache()
	bus := events.NewBus(cfg.BusRingCapacity)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Construct the server first so we can hand its metrics + readyz registry
	// to the components below before they start. server.New constructs the
	// HTTP listener but does not Run it until srv.Run.
	srv := server.New(cfg, bus, cache)
	metrics := srv.Metrics()
	ready := srv.Ready()

	// Define readiness budgets per component. 2× the poll interval gives one
	// dropped cycle of headroom before a component is considered unready —
	// avoids flapping while still surfacing genuine outages within a few
	// seconds.
	agamemnonReadyMaxAge := 2 * cfg.PollAgamemnon
	natsReadyMaxAge := 2 * cfg.NATSPollInterval

	// Start Tailscale device refresher.
	tsSrc := tailscale.NewSource(cfg)
	tsRefresher := tailscale.NewRefresher(tsSrc, cache, cfg.TailscaleRefreshInterval)
	go tsRefresher.Start(ctx)

	// Start probe runner.
	prober := store.NewProber(cache, cfg.ProbeInterval)
	go prober.Start(ctx)

	// Start Agamemnon poller (agents + tasks at the configured interval) and
	// register it with metrics + readiness.
	agamemnonPoller := poller.NewAgamemnonPoller(cfg, cache)
	agamemnonPoller.SetMetrics(metrics)
	ready.Register(server.PollerCheck(agamemnonPoller, agamemnonReadyMaxAge))
	go agamemnonPoller.Start(ctx, cfg.PollAgamemnon)

	// Start NATS monitoring poller (varz + jsz at cfg.NATSPollInterval).
	natsPoller := poller.NewNATSPoller(cfg, cache)
	natsPoller.SetMetrics(metrics)
	ready.Register(server.PollerCheck(natsPoller, natsReadyMaxAge))
	go natsPoller.Start(ctx, cfg.NATSPollInterval)

	// Start the JetStream subscriber that bridges NATS events into events.Bus
	// for the SSE fan-out. This is the central event spine of Atlas — without
	// it SSE clients only receive heartbeats. Start runs in a goroutine and
	// returns nil only after ctx is cancelled; an early non-nil return
	// indicates a fatal initialisation error (unreachable NATS, no streams
	// attached) which we surface but do not exit on so the rest of the
	// dashboard remains operable. /readyz reflects the failure either way.
	streams := atlnats.DefaultStreams()
	natsSubscriber := atlnats.New(atlnats.Config{
		NATSURL:       cfg.NATSURL,
		Streams:       streams,
		AckWait:       cfg.NATSAckWait,
		MaxAckPending: cfg.NATSMaxAckPending,
	}, bus)
	natsSubscriber.SetMetrics(metrics)
	ready.Register(server.NATSCheck("nats-subscriber", natsSubscriber, len(streams)))
	go func() {
		if err := natsSubscriber.Start(ctx); err != nil {
			slog.Error("nats subscriber failed to start; SSE will deliver heartbeats only", "err", err)
		}
	}()

	if err := srv.Run(ctx); err != nil {
		slog.Error("server error", "err", err)
		os.Exit(1)
	}
}
