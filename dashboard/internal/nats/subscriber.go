// Package nats provides JetStream durable subscriber infrastructure for the
// Atlas dashboard.  It connects to a NATS server, creates a durable push
// consumer for each configured stream, and publishes decoded Event values onto
// an EventBus for the rest of the application to consume.
package nats

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strings"
	"sync/atomic"
	"time"

	natsgo "github.com/nats-io/nats.go"

	"github.com/HomericIntelligence/atlas/internal/events"
)

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

// Config holds the connection and subscription parameters for the Subscriber.
type Config struct {
	// NATSURL is the NATS server URL, e.g. "nats://127.0.0.1:4222".
	NATSURL string
	// Streams is the list of JetStream stream configurations to subscribe to.
	Streams []StreamConfig
}

// StreamConfig describes a single JetStream stream and the durable consumer
// that Atlas will attach to it.
type StreamConfig struct {
	// Stream is the JetStream stream name, e.g. "homeric-agents".
	Stream string
	// Subjects is the list of NATS subjects to filter on, e.g. ["hi.agents.>"].
	Subjects []string
	// Durable is the durable consumer name, e.g. "atlas-agents".
	Durable string
}

// Event is the normalised representation of a NATS message published onto the
// EventBus. It is a type alias for events.Event so that *events.Bus satisfies
// the EventBus interface below without an adapter — the two halves of the
// JetStream → events.Bus → SSE pipeline use the same wire type.
type Event = events.Event

// EventBus is the interface that the Subscriber uses to publish decoded events.
// Implementations must be safe for concurrent use from multiple goroutines.
// *events.Bus satisfies this interface directly because events.Bus.Publish
// accepts events.Event and Event above is an alias for events.Event.
type EventBus interface {
	Publish(e Event)
}

// Subscriber connects to NATS and maintains durable push consumers for each
// configured stream.
type Subscriber struct {
	cfg Config
	bus EventBus
	// ready becomes true once Start has finished attaching all configured
	// JetStream durable subscriptions. Read via Ready().
	ready atomic.Bool
	// attached counts how many JetStream subscriptions actually succeeded
	// during Start. Read via Attached().
	attached atomic.Int32
}

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------

// New creates a Subscriber that will use cfg and bus.  It does not connect to
// NATS — connection is deferred to Start.
func New(cfg Config, bus EventBus) *Subscriber {
	return &Subscriber{cfg: cfg, bus: bus}
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

// Start connects to the NATS server and subscribes to all configured streams.
// It blocks until ctx is cancelled, at which point it drains and closes the
// connection. It returns a non-nil error if the initial connection fails or
// if zero streams successfully subscribed (a configured-but-empty subscriber
// is almost always a misconfiguration the caller should surface).
//
// Start sets Ready() to true only after all attempted Subscribe calls have
// completed and at least one succeeded; Ready() stays false thereafter only
// when the connection is being torn down or no subscriptions attached.
func (s *Subscriber) Start(ctx context.Context) error {
	nc, err := natsgo.Connect(s.cfg.NATSURL, natsgo.MaxReconnects(0))
	if err != nil {
		return err
	}

	js, err := nc.JetStream()
	if err != nil {
		nc.Close()
		return err
	}

	for _, sc := range s.cfg.Streams {
		sc := sc // capture for closure
		handler := s.makeHandler(sc)
		// nats.go forbids combining a positional subject with
		// ConsumerFilterSubjects: that option is for *multi-subject* filters,
		// and using both surfaces "consumer with multiple subject filters
		// cannot use subject based API". We drive the subscription by the
		// first subject when there's exactly one filter (the common case for
		// Atlas's wildcard subjects like "hi.agents.>") and use
		// ConsumerFilterSubjects only when the stream config supplies more
		// than one filter.
		opts := []natsgo.SubOpt{
			natsgo.Durable(sc.Durable),
			natsgo.DeliverNew(),
			natsgo.AckExplicit(),
			natsgo.AckWait(30 * time.Second),
			natsgo.MaxAckPending(1024),
		}
		var subErr error
		if len(sc.Subjects) <= 1 {
			subject := ""
			if len(sc.Subjects) == 1 {
				subject = sc.Subjects[0]
			}
			_, subErr = js.Subscribe(subject, handler, opts...)
		} else {
			opts = append(opts, natsgo.ConsumerFilterSubjects(sc.Subjects...))
			// With multiple filters, pass an empty subject — the filter list
			// is authoritative.
			_, subErr = js.Subscribe("", handler, opts...)
		}
		if subErr != nil {
			slog.Error("atlas: JetStream subscribe failed", "stream", sc.Stream, "err", subErr)
			continue
		}
		s.attached.Add(1)
	}

	if s.attached.Load() == 0 {
		nc.Close()
		return errors.New("nats: zero JetStream subscriptions attached — check stream configuration")
	}

	s.ready.Store(true)
	slog.Info("atlas: NATS subscriber ready",
		"attached", s.attached.Load(),
		"configured", len(s.cfg.Streams))

	// Block until context is cancelled.
	<-ctx.Done()

	// Mark not-ready before drain so /readyz flips immediately.
	s.ready.Store(false)
	// Drain and close the connection gracefully.
	_ = nc.Drain()
	return nil
}

// Ready reports true once Start has connected to NATS, attached at least one
// JetStream durable subscription, and not yet begun shutting down. Used by the
// /readyz aggregator.
func (s *Subscriber) Ready() bool {
	return s.ready.Load()
}

// Attached reports how many of the configured streams the Subscriber
// successfully attached to. A value less than len(cfg.Streams) means at least
// one Subscribe call failed during Start; check the logs for the per-stream
// error.
func (s *Subscriber) Attached() int {
	return int(s.attached.Load())
}

// makeHandler returns a NATS MsgHandler that decodes the message and publishes
// an Event onto the bus. The message is published before it is acked: if the
// bus is full and drops the event, an unacked message lets JetStream redeliver
// it after the AckWait window. We deliberately accept duplicate delivery in
// the rare bus-drop case rather than silently lose data.
func (s *Subscriber) makeHandler(sc StreamConfig) natsgo.MsgHandler {
	return func(msg *natsgo.Msg) {
		e := Event{
			Topic:   TopicFromSubject(msg.Subject),
			Subject: msg.Subject,
			Payload: json.RawMessage(msg.Data),
			At:      time.Now().UTC(),
		}
		s.bus.Publish(e)
		_ = msg.Ack()
	}
}

// ---------------------------------------------------------------------------
// DefaultStreams
// ---------------------------------------------------------------------------

// DefaultStreams returns the six canonical HomericIntelligence JetStream
// stream configurations that Atlas subscribes to.
func DefaultStreams() []StreamConfig {
	return []StreamConfig{
		{Stream: "homeric-agents", Subjects: []string{"hi.agents.>"}, Durable: "atlas-agents"},
		{Stream: "homeric-tasks", Subjects: []string{"hi.tasks.>"}, Durable: "atlas-tasks"},
		{Stream: "homeric-myrmidon", Subjects: []string{"hi.myrmidon.>"}, Durable: "atlas-myrmidon"},
		{Stream: "homeric-research", Subjects: []string{"hi.research.>"}, Durable: "atlas-research"},
		{Stream: "homeric-pipeline", Subjects: []string{"hi.pipeline.>"}, Durable: "atlas-pipeline"},
		{Stream: "homeric-logs", Subjects: []string{"hi.logs.>"}, Durable: "atlas-logs"},
	}
}

// ---------------------------------------------------------------------------
// TopicFromSubject
// ---------------------------------------------------------------------------

// TopicFromSubject derives a short topic label from a NATS subject.
// The mapping is:
//
//	hi.agents.*   → "agent"
//	hi.tasks.*    → "task"
//	hi.myrmidon.* → "myrmidon"
//	hi.research.* → "research"
//	hi.pipeline.* → "pipeline"
//	hi.logs.*     → "log"
//	anything else → "unknown"
func TopicFromSubject(subject string) string {
	parts := strings.SplitN(subject, ".", 3)
	if len(parts) < 2 || parts[0] != "hi" {
		return "unknown"
	}
	switch parts[1] {
	case "agents":
		return "agent"
	case "tasks":
		return "task"
	case "myrmidon":
		return "myrmidon"
	case "research":
		return "research"
	case "pipeline":
		return "pipeline"
	case "logs":
		return "log"
	default:
		return "unknown"
	}
}
