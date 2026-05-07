// Regression test for the §3/§9 audit MAJOR finding:
//
//	"NATS subscriber MaxReconnects(0) disables reconnection — single network
//	 blip = permanent disconnect until process restart."
//
// Run with:
//
//	go test -tags=integration ./tests/integration/...
//
//go:build integration

package integration_test

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	natsserver "github.com/nats-io/nats-server/v2/server"
	natsgo "github.com/nats-io/nats.go"

	"github.com/HomericIntelligence/atlas/internal/events"
	atlnats "github.com/HomericIntelligence/atlas/internal/nats"
)

// createPersistentJetStreams is like createJetStreams in nats_spine_test.go
// but uses FileStorage so the streams + durable consumer state survive a
// server Shutdown that reuses the same JetStream StoreDir. This is what
// allows the reconnect test to prove that nats.go transparently re-subscribes
// the durable consumers after a reconnect — with MemoryStorage the durable
// state would be wiped on Shutdown and the test could not distinguish a real
// reattach from a fresh subscribe.
func createPersistentJetStreams(t *testing.T, url string) {
	t.Helper()
	nc, err := natsgo.Connect(url)
	if err != nil {
		t.Fatalf("connect for stream creation: %v", err)
	}
	defer nc.Close()

	js, err := nc.JetStream()
	if err != nil {
		t.Fatalf("jetstream context: %v", err)
	}

	for _, sc := range atlnats.DefaultStreams() {
		_, err := js.AddStream(&natsgo.StreamConfig{
			Name:     sc.Stream,
			Subjects: sc.Subjects,
			Storage:  natsgo.FileStorage,
		})
		if err != nil {
			t.Fatalf("add stream %q: %v", sc.Stream, err)
		}
	}
}

// startEmbeddedNATSOnPort launches an in-process NATS server on a specific
// port so a follow-up call after Shutdown() can rebind the same port. This is
// required for the reconnect test: nats.go's reconnect targets the original
// URL, so the second server must answer at the same host:port.
//
// We pick the ephemeral port once via Port: -1, then reuse it for the second
// boot. The same JetStream StoreDir is reused so durable consumers persist
// across the restart (which proves the JetStream-consumer round-trip after
// reconnect, not just connection liveness).
func startEmbeddedNATSOnPort(t *testing.T, port int, storeDir string) (*natsserver.Server, int) {
	t.Helper()
	opts := &natsserver.Options{
		Host:       "127.0.0.1",
		Port:       port,
		JetStream:  true,
		StoreDir:   filepath.Join(storeDir, "js"),
		NoLog:      true,
		NoSigs:     true,
		MaxPayload: 1 << 20,
	}
	s, err := natsserver.NewServer(opts)
	if err != nil {
		t.Fatalf("new nats server: %v", err)
	}
	go s.Start()
	if !s.ReadyForConnections(5 * time.Second) {
		s.Shutdown()
		t.Fatal("embedded NATS server failed to become ready in 5s")
	}
	// Resolve the actual bound port (only meaningful when port == -1).
	addr := s.Addr()
	if addr == nil {
		s.Shutdown()
		t.Fatal("embedded NATS server has no address")
	}
	tcp, ok := addr.(interface{ Port() int })
	if !ok {
		// fall back to opts.Port for the typed cases we know about.
		return s, opts.Port
	}
	return s, tcp.Port()
}

// TestNATSSubscriber_ReconnectsAfterServerRestart proves the audit fix:
//   - Subscriber comes up, Ready()==true.
//   - Server goes down → Ready() flips to false (DisconnectErrHandler fires).
//   - Server comes back on the same port → Ready() flips to true
//     (ReconnectHandler fires) and a freshly-published JetStream message
//     reaches the events.Bus, proving the JetStream consumers self-reattached.
//
// This is the regression that locks in the audit fix: if anyone reverts to
// MaxReconnects(0) the second Ready()-true wait will time out.
func TestNATSSubscriber_ReconnectsAfterServerRestart(t *testing.T) {
	storeDir, err := os.MkdirTemp("", "atlas-jetstream-reconnect-")
	if err != nil {
		t.Fatalf("mkdir temp jetstream dir: %v", err)
	}
	defer os.RemoveAll(storeDir)

	// Boot #1: use an ephemeral port and capture the resolved port.
	srv, port := startEmbeddedNATSOnPort(t, -1, storeDir)
	url := srv.ClientURL()

	// Create the canonical Atlas streams with FILE storage so the stream
	// definitions and durable-consumer state persist across the server
	// restart that uses the same JetStream StoreDir below. (MemoryStorage,
	// used in nats_spine_test.go, would lose all consumer state on Shutdown
	// and defeat the test.)
	createPersistentJetStreams(t, url)

	bus := events.NewBus(64)
	sub := atlnats.New(atlnats.Config{
		NATSURL: url,
		Streams: atlnats.DefaultStreams(),
	}, bus)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	startErr := make(chan error, 1)
	go func() { startErr <- sub.Start(ctx) }()

	// Wait for initial Ready().
	if !waitFor(t, sub.Ready, 5*time.Second, true) {
		t.Fatal("subscriber did not become ready within 5s of Start")
	}

	rx := bus.Subscribe(64)
	defer bus.Unsubscribe(rx)

	// Bring the server down. Wait for Ready() to flip to false within ~3s
	// (DisconnectErrHandler fires roughly synchronously with Shutdown).
	srv.Shutdown()
	srv.WaitForShutdown()
	if !waitFor(t, sub.Ready, 3*time.Second, false) {
		t.Fatal("Ready() did not flip to false within 3s of NATS shutdown — DisconnectErrHandler not wired?")
	}

	// Bring the server back on the same port. nats.go will retry
	// indefinitely (MaxReconnects(-1)) at ReconnectWait+jitter (~2–2.5s).
	srv2, _ := startEmbeddedNATSOnPort(t, port, storeDir)
	defer func() {
		srv2.Shutdown()
		srv2.WaitForShutdown()
	}()

	// Wait for Ready() to flip back to true within ~10s
	// (one ReconnectWait + 2s jitter ceiling + slack).
	if !waitFor(t, sub.Ready, 10*time.Second, true) {
		t.Fatal("Ready() did not flip back to true within 10s of NATS restart — ReconnectHandler not wired or MaxReconnects(0) regression?")
	}

	// Drain any leftover events from the bus before publishing the probe so
	// our assertion below is unambiguous.
	for drained := false; !drained; {
		select {
		case <-rx:
		default:
			drained = true
		}
	}

	// Publish a probe message and verify it round-trips through the bus.
	// This is the strongest signal that JetStream consumers self-reattached
	// after the reconnect.
	pubNC, err := natsgo.Connect(srv2.ClientURL())
	if err != nil {
		t.Fatalf("publisher connect: %v", err)
	}
	defer pubNC.Close()
	pubJS, err := pubNC.JetStream()
	if err != nil {
		t.Fatalf("publisher jetstream: %v", err)
	}

	// JetStream may need a moment to settle on the freshly-restarted server
	// (stream metadata replay, consumer reattach). Retry the probe publish
	// for a few seconds before giving up.
	probeSubj := "hi.agents.reconnect-probe.heartbeat"
	payload, _ := json.Marshal(map[string]string{"subject": probeSubj})
	pubDeadline := time.Now().Add(5 * time.Second)
	for {
		_, err = pubJS.Publish(probeSubj, payload)
		if err == nil {
			break
		}
		if time.Now().After(pubDeadline) {
			t.Fatalf("publish probe to %q: %v", probeSubj, err)
		}
		time.Sleep(100 * time.Millisecond)
	}

	deadline := time.Now().Add(5 * time.Second)
	for {
		if time.Now().After(deadline) {
			t.Fatal("did not receive probe event on bus within 5s after reconnect — JetStream consumer did not re-attach")
		}
		select {
		case e := <-rx:
			if e.Subject == probeSubj {
				return // success
			}
		case <-time.After(100 * time.Millisecond):
		}
	}
}

// waitFor polls fn at 25ms intervals until it returns want or until the
// deadline elapses. Returns true if fn matched want within timeout.
func waitFor(t *testing.T, fn func() bool, timeout time.Duration, want bool) bool {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if fn() == want {
			return true
		}
		time.Sleep(25 * time.Millisecond)
	}
	return fn() == want
}
