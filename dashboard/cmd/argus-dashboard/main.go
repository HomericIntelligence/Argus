package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

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
	bus := events.NewBus(256)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Start Tailscale device refresher.
	tsSrc := tailscale.NewSource(cfg)
	tsRefresher := tailscale.NewRefresher(tsSrc, cache, 30*time.Second)
	go tsRefresher.Start(ctx)

	// Start probe runner.
	prober := store.NewProber(cache, 10*time.Second)
	go prober.Start(ctx)

	// Start Agamemnon poller (agents + tasks at the configured interval).
	agamemnonPoller := poller.NewAgamemnonPoller(cfg, cache)
	go agamemnonPoller.Start(ctx, cfg.PollAgamemnonMs)

	// Start NATS monitoring poller (varz + jsz every 5s).
	natsPoller := poller.NewNATSPoller(cfg, cache)
	go natsPoller.Start(ctx, 5*time.Second)

	// Start the JetStream subscriber that bridges NATS events into events.Bus
	// for the SSE fan-out. This is the central event spine of Atlas — without
	// it SSE clients only receive heartbeats. Start runs in a goroutine and
	// returns nil only after ctx is cancelled; an early non-nil return
	// indicates a fatal initialisation error (unreachable NATS, no streams
	// attached) which we surface but do not exit on so the rest of the
	// dashboard remains operable.
	natsSubscriber := atlnats.New(atlnats.Config{
		NATSURL: cfg.NATSURL,
		Streams: atlnats.DefaultStreams(),
	}, bus)
	go func() {
		if err := natsSubscriber.Start(ctx); err != nil {
			slog.Error("nats subscriber failed to start; SSE will deliver heartbeats only", "err", err)
		}
	}()

	srv := server.New(cfg, bus, cache)

	if err := srv.Run(ctx); err != nil {
		slog.Error("server error", "err", err)
		os.Exit(1)
	}
}
