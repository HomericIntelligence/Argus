// Package integration_test contains tests that exercise multiple Atlas
// subsystems end-to-end. These tests are slower than unit tests and may bind
// network ports, so they are kept in a separate package and gated behind the
// `integration` build tag.
//
// Run with:
//
//	go test -tags=integration ./tests/integration/...
//
// CI runs them in the standard test job after the unit tests; the build tag
// prevents accidental inclusion in fast `go test ./...` invocations during
// development.
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

// startEmbeddedNATS launches an in-process NATS server with JetStream enabled
// and returns its client URL plus a teardown func. The server uses an
// ephemeral port and a tempdir for JetStream storage.
func startEmbeddedNATS(t *testing.T) (string, func()) {
	t.Helper()
	storeDir, err := os.MkdirTemp("", "atlas-jetstream-")
	if err != nil {
		t.Fatalf("mkdir temp jetstream dir: %v", err)
	}
	opts := &natsserver.Options{
		Host:       "127.0.0.1",
		Port:       -1, // ephemeral
		JetStream:  true,
		StoreDir:   filepath.Join(storeDir, "js"),
		NoLog:      true,
		NoSigs:     true,
		MaxPayload: 1 << 20,
	}
	s, err := natsserver.NewServer(opts)
	if err != nil {
		os.RemoveAll(storeDir)
		t.Fatalf("new nats server: %v", err)
	}
	go s.Start()
	if !s.ReadyForConnections(5 * time.Second) {
		s.Shutdown()
		os.RemoveAll(storeDir)
		t.Fatal("embedded NATS server failed to become ready in 5s")
	}
	teardown := func() {
		s.Shutdown()
		s.WaitForShutdown()
		_ = os.RemoveAll(storeDir)
	}
	return s.ClientURL(), teardown
}

// createJetStreams creates the six canonical Atlas streams on the embedded
// server so that DefaultStreams subscribers have something to attach to.
func createJetStreams(t *testing.T, url string) {
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
			Storage:  natsgo.MemoryStorage,
		})
		if err != nil {
			t.Fatalf("add stream %q: %v", sc.Stream, err)
		}
	}
}

// TestNATSSpine_PublishToBus is the end-to-end test for Epic 151's central
// architectural deliverable: a JetStream message must reach an SSE-style
// events.Bus subscriber via Atlas's nats.Subscriber. If this test fails the
// "live status via NATS SSE fan-out" promise is broken.
func TestNATSSpine_PublishToBus(t *testing.T) {
	url, teardown := startEmbeddedNATS(t)
	defer teardown()
	createJetStreams(t, url)

	bus := events.NewBus(64)
	sub := atlnats.New(atlnats.Config{
		NATSURL: url,
		Streams: atlnats.DefaultStreams(),
	}, bus)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	startErr := make(chan error, 1)
	go func() { startErr <- sub.Start(ctx) }()

	// Wait for Ready() — Start has attached all subscriptions.
	deadline := time.Now().Add(5 * time.Second)
	for !sub.Ready() {
		if time.Now().After(deadline) {
			t.Fatal("subscriber did not become ready within 5s")
		}
		time.Sleep(20 * time.Millisecond)
	}

	if got := sub.Attached(); got != 6 {
		t.Fatalf("Attached() = %d, want 6 (one per DefaultStreams entry)", got)
	}

	// Now subscribe to the bus and publish one message per stream.
	rx := bus.Subscribe(64)
	defer bus.Unsubscribe(rx)

	pubNC, err := natsgo.Connect(url)
	if err != nil {
		t.Fatalf("publisher connect: %v", err)
	}
	defer pubNC.Close()
	pubJS, err := pubNC.JetStream()
	if err != nil {
		t.Fatalf("publisher jetstream: %v", err)
	}

	// One published subject per default stream.
	pubSubjects := []string{
		"hi.agents.host1.heartbeat",
		"hi.tasks.team1.task1.completed",
		"hi.myrmidon.host1.swarm1.result",
		"hi.research.expt1.update",
		"hi.pipeline.run1.stage1.completed",
		"hi.logs.atlas.info",
	}
	for _, subj := range pubSubjects {
		payload, _ := json.Marshal(map[string]string{"subject": subj})
		if _, err := pubJS.Publish(subj, payload); err != nil {
			t.Fatalf("publish to %q: %v", subj, err)
		}
	}

	// Drain up to 6 events from the bus subscriber within the deadline.
	gotSubjects := make(map[string]bool)
	deadline = time.Now().Add(5 * time.Second)
	for len(gotSubjects) < 6 && time.Now().Before(deadline) {
		select {
		case e := <-rx:
			gotSubjects[e.Subject] = true
		case <-time.After(100 * time.Millisecond):
		}
	}

	if len(gotSubjects) != 6 {
		t.Fatalf("received %d distinct subjects, want 6; got %v", len(gotSubjects), gotSubjects)
	}
	for _, subj := range pubSubjects {
		if !gotSubjects[subj] {
			t.Errorf("did not receive event for subject %q", subj)
		}
	}

	// Cancel and verify Ready() flips back to false.
	cancel()
	select {
	case <-startErr:
	case <-time.After(5 * time.Second):
		t.Fatal("Start did not return within 5s of context cancel")
	}
	if sub.Ready() {
		t.Error("Ready() must return false after Start exits")
	}
}

// TestNATSSpine_NoStreams_FailsFast verifies that Start returns a non-nil
// error when none of the configured streams exist on the server (zero
// attached). This protects operators from a silently-broken Atlas instance
// that connects to NATS but has no consumers.
func TestNATSSpine_NoStreams_FailsFast(t *testing.T) {
	url, teardown := startEmbeddedNATS(t)
	defer teardown()
	// Deliberately skip createJetStreams — every Subscribe should fail.

	bus := events.NewBus(64)
	sub := atlnats.New(atlnats.Config{
		NATSURL: url,
		Streams: atlnats.DefaultStreams(),
	}, bus)

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	err := sub.Start(ctx)
	if err == nil {
		t.Fatal("Start: want non-nil error when zero streams attach, got nil")
	}
	if sub.Ready() {
		t.Error("Ready() must be false when zero streams attached")
	}
}
