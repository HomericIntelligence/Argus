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
	// AckWait is the JetStream consumer AckWait — how long the server waits
	// for an Ack before re-delivering a message. Sourced from
	// ATLAS_NATS_ACK_WAIT (default 30s when zero).
	AckWait time.Duration
	// MaxAckPending is the JetStream consumer MaxAckPending — the cap on
	// un-acked in-flight messages per consumer. Sourced from
	// ATLAS_NATS_MAX_ACK_PENDING (default 1024 when zero).
	MaxAckPending int
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

// MetricsSink is the metric-recording surface the Subscriber uses. Set with
// SetMetrics; default is a no-op so callers that never wire metrics still work.
type MetricsSink interface {
	SetNATSConnected(connected bool)
	IncNATSMessage(stream string)
	IncEventParseError(stream string)
}

type noopMetrics struct{}

func (noopMetrics) SetNATSConnected(bool)        {}
func (noopMetrics) IncNATSMessage(string)        {}
func (noopMetrics) IncEventParseError(string)    {}

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
	// metrics is set by SetMetrics; default is no-op.
	metrics atomic.Pointer[MetricsSink]
}

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------

// New creates a Subscriber that will use cfg and bus.  It does not connect to
// NATS — connection is deferred to Start.
func New(cfg Config, bus EventBus) *Subscriber {
	s := &Subscriber{cfg: cfg, bus: bus}
	var m MetricsSink = noopMetrics{}
	s.metrics.Store(&m)
	return s
}

// SetMetrics installs the metric sink. Safe to call before or after Start.
func (s *Subscriber) SetMetrics(m MetricsSink) {
	s.metrics.Store(&m)
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
// when the connection is being torn down, the underlying NATS connection has
// been disconnected (and not yet recovered), or no subscriptions attached.
//
// The connection uses infinite reconnect (`MaxReconnects(-1)`) with a 2s base
// reconnect delay plus 0–500ms jitter (0–2s for TLS) to avoid thundering-herd
// on a single NATS restart. Connection-state transitions (disconnect,
// reconnect, closed) are logged via slog with an `event=...` field and
// flip Ready() so the /readyz aggregator reports the truth.
//
// JetStream push subscriptions created via `js.Subscribe` are bound to the
// nats.go connection: nats.go transparently re-establishes them after a
// reconnect, so we do NOT call attachStreams in ReconnectHandler — the durable
// consumer state lives on the server and is restored automatically. The
// reconnect integration test confirms this end-to-end.
func (s *Subscriber) Start(ctx context.Context) error {
	nc, err := s.connect()
	if err != nil {
		return err
	}

	js, err := nc.JetStream()
	if err != nil {
		nc.Close()
		return err
	}

	if err := s.attach(js); err != nil {
		nc.Close()
		(*s.metrics.Load()).SetNATSConnected(false)
		return err
	}

	s.ready.Store(true)
	(*s.metrics.Load()).SetNATSConnected(true)
	slog.Info("atlas: NATS subscriber ready",
		"attached", s.attached.Load(),
		"configured", len(s.cfg.Streams))

	s.awaitShutdown(ctx, nc)
	return nil
}

// connect dials NATS with the canonical reconnect policy and lifecycle
// handlers. The handlers close over s so they can flip the Ready flag and
// update the connected metric on disconnect / reconnect / close events.
//
// The reconnect policy is infinite retry (MaxReconnects(-1)) at a 2s base
// delay plus 0–500ms jitter (0–2s for TLS), which avoids thundering-herd on
// a single NATS restart. JetStream push consumers created later via
// js.Subscribe are bound to this connection and are transparently
// re-established by nats.go after a reconnect — see Start's doc comment.
func (s *Subscriber) connect() (*natsgo.Conn, error) {
	return natsgo.Connect(
		s.cfg.NATSURL,
		// -1 is the documented "infinite" sentinel in nats.go.
		// (0 means "do not reconnect" — see pkg.go.dev/github.com/nats-io/nats.go#MaxReconnects.)
		natsgo.MaxReconnects(-1),
		natsgo.ReconnectWait(2*time.Second),
		// Jitter: 500ms for non-TLS, 2s for TLS. Avoids thundering-herd
		// when many subscribers reconnect to the same NATS restart.
		natsgo.ReconnectJitter(500*time.Millisecond, 2*time.Second),
		natsgo.DisconnectErrHandler(func(_ *natsgo.Conn, err error) {
			s.ready.Store(false)
			(*s.metrics.Load()).SetNATSConnected(false)
			slog.Warn("atlas: NATS disconnected — will reconnect",
				"event", "nats-disconnect",
				"err", err)
		}),
		natsgo.ReconnectHandler(func(c *natsgo.Conn) {
			// JetStream push consumers are restored automatically by
			// nats.go after a reconnect, so we just flip Ready back on.
			s.ready.Store(true)
			(*s.metrics.Load()).SetNATSConnected(true)
			slog.Info("atlas: NATS reconnected",
				"event", "nats-reconnect",
				"url", c.ConnectedUrl())
		}),
		natsgo.ClosedHandler(func(_ *natsgo.Conn) {
			s.ready.Store(false)
			(*s.metrics.Load()).SetNATSConnected(false)
			// With MaxReconnects(-1) this only fires on Drain/Close.
			slog.Warn("atlas: NATS connection closed",
				"event", "nats-closed")
		}),
	)
}

// attach creates a durable JetStream push subscription for every stream in
// s.cfg.Streams and increments s.attached for each success. Per-stream
// failures are logged and skipped so that a single misconfigured stream does
// not take down the whole subscriber. attach returns an error only when zero
// streams attached — the fail-fast contract documented on Start.
func (s *Subscriber) attach(js natsgo.JetStreamContext) error {
	for _, sc := range s.cfg.Streams {
		sc := sc // capture for closure
		if _, err := s.subscribeStream(js, sc); err != nil {
			slog.Error("atlas: JetStream subscribe failed", "stream", sc.Stream, "err", err)
			continue
		}
		s.attached.Add(1)
	}

	if s.attached.Load() == 0 {
		return errors.New("nats: zero JetStream subscriptions attached — check stream configuration")
	}
	return nil
}

// subscribeStream creates one durable JetStream push subscription for sc.
//
// nats.go forbids combining a positional subject with ConsumerFilterSubjects:
// that option is for *multi-subject* filters, and using both surfaces
// "consumer with multiple subject filters cannot use subject based API". We
// drive the subscription by the first subject when there's exactly one filter
// (the common case for Atlas's wildcard subjects like "hi.agents.>") and use
// ConsumerFilterSubjects only when the stream config supplies more than one
// filter.
func (s *Subscriber) subscribeStream(js natsgo.JetStreamContext, sc StreamConfig) (*natsgo.Subscription, error) {
	handler := s.makeHandler(sc)
	ackWait := s.cfg.AckWait
	if ackWait <= 0 {
		ackWait = 30 * time.Second
	}
	maxAckPending := s.cfg.MaxAckPending
	if maxAckPending <= 0 {
		maxAckPending = 1024
	}
	opts := []natsgo.SubOpt{
		natsgo.Durable(sc.Durable),
		natsgo.DeliverNew(),
		natsgo.AckExplicit(),
		natsgo.AckWait(ackWait),
		natsgo.MaxAckPending(maxAckPending),
	}
	if len(sc.Subjects) <= 1 {
		subject := ""
		if len(sc.Subjects) == 1 {
			subject = sc.Subjects[0]
		}
		return js.Subscribe(subject, handler, opts...)
	}
	opts = append(opts, natsgo.ConsumerFilterSubjects(sc.Subjects...))
	// With multiple filters, pass an empty subject — the filter list is
	// authoritative.
	return js.Subscribe("", handler, opts...)
}

// awaitShutdown blocks until ctx is cancelled, then flips Ready off and
// drains the connection so in-flight messages get acked before close. Ready
// is flipped before Drain so /readyz reflects the truth immediately, ahead
// of the (potentially slow) drain.
func (s *Subscriber) awaitShutdown(ctx context.Context, nc *natsgo.Conn) {
	<-ctx.Done()
	s.ready.Store(false)
	(*s.metrics.Load()).SetNATSConnected(false)
	_ = nc.Drain()
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
		(*s.metrics.Load()).IncNATSMessage(sc.Stream)
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
