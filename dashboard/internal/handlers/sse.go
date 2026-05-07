package handlers

import (
	"context"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/HomericIntelligence/atlas/internal/events"
)

// heartbeatNanos stores the heartbeat interval in nanoseconds.
// Access via HeartbeatInterval / SetHeartbeatInterval for race safety.
//
// The composition root populates this from cfg.SSEHeartbeatInterval
// (ATLAS_SSE_HEARTBEAT_INTERVAL, default 15s) at startup. Tests use the
// SetHeartbeatInterval helper to override the value temporarily.
var heartbeatNanos atomic.Int64

// subscriberBufferAtomic stores the per-SSE-client channel buffer size.
// The composition root populates this from cfg.SSESubscriberBuffer
// (ATLAS_SSE_SUBSCRIBER_BUFFER, default 1000) at startup.
var subscriberBufferAtomic atomic.Int64

func init() {
	heartbeatNanos.Store(int64(15 * time.Second))
	subscriberBufferAtomic.Store(1000)
}

// HeartbeatInterval returns the current heartbeat interval.
func HeartbeatInterval() time.Duration {
	return time.Duration(heartbeatNanos.Load())
}

// SetHeartbeatInterval sets the heartbeat interval. Safe for concurrent use;
// called once at startup from the composition root with cfg.SSEHeartbeatInterval
// and otherwise used by tests that need a faster cadence.
func SetHeartbeatInterval(d time.Duration) {
	heartbeatNanos.Store(int64(d))
}

// SubscriberBuffer returns the current per-client channel buffer size.
func SubscriberBuffer() int {
	return int(subscriberBufferAtomic.Load())
}

// SetSubscriberBuffer sets the per-client channel buffer. Safe for concurrent
// use; called once at startup from the composition root with
// cfg.SSESubscriberBuffer. Values <= 0 are ignored so a typo does not produce
// an unbuffered channel.
func SetSubscriberBuffer(n int) {
	if n <= 0 {
		return
	}
	subscriberBufferAtomic.Store(int64(n))
}

// SSEMetrics is the metric-recording surface the SSE handler uses. The server
// passes its AtlasMetrics in via SetMetrics. The default no-op preserves
// behaviour for tests that construct an SSE handler without a Server.
type SSEMetrics interface {
	SetSSEConnectedClients(n int64)
}

type noopSSEMetrics struct{}

func (noopSSEMetrics) SetSSEConnectedClients(int64) {}

// SSE is the Server-Sent Events handler. It streams events from the bus to
// connected HTTP clients using the text/event-stream protocol.
//
// The connected client count is exposed via atlas_sse_connected_clients when
// SetMetrics has been called with a real SSEMetrics implementation.
type SSE struct {
	bus       *events.Bus
	connected atomic.Int64
	metrics   atomic.Pointer[SSEMetrics]
}

// NewSSE constructs an SSE handler backed by the given bus.
func NewSSE(bus *events.Bus) *SSE {
	s := &SSE{bus: bus}
	var m SSEMetrics = noopSSEMetrics{}
	s.metrics.Store(&m)
	return s
}

// SetMetrics installs the metric sink used to publish atlas_sse_connected_clients.
func (h *SSE) SetMetrics(m SSEMetrics) {
	h.metrics.Store(&m)
}

// Connected returns the current count of connected SSE clients.
func (h *SSE) Connected() int64 {
	return h.connected.Load()
}

// ServeHTTP implements http.Handler. Each connected client gets its own
// subscriber channel on the bus and receives all matching events until the
// client disconnects or the request context is cancelled.
func (h *SSE) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	flusher, ok := prepareSSEResponse(w)
	if !ok {
		return
	}

	topicFilter := parseTopicFilter(r.URL.Query().Get("topics"))

	// Subscribe FIRST so that any event published from this point onward is
	// captured on the live channel. We then take a snapshot AFTER the
	// subscription is registered: events published in the small window
	// between Subscribe and Snapshot will appear in BOTH the snapshot and
	// the live channel, which is fine because we de-duplicate by Event.ID
	// while draining the replay window. Doing it the other way round
	// (Snapshot then Subscribe) loses any event published between the two
	// calls — see audit finding "SSE replay race vs new publishes".
	ch := h.bus.Subscribe(SubscriberBuffer())
	defer h.bus.Unsubscribe(ch)

	// Track this connection in the gauge so /metrics reflects live load.
	cur := h.connected.Add(1)
	(*h.metrics.Load()).SetSSEConnectedClients(cur)
	defer func() {
		cur := h.connected.Add(-1)
		(*h.metrics.Load()).SetSSEConnectedClients(cur)
	}()

	// maxReplayedID is the highest Event.ID written to the wire during the
	// replay phase. While maxReplayedID > 0 we are still inside the replay
	// window and skip any live-channel event whose ID is <= maxReplayedID
	// (it was already delivered as part of the snapshot). Once we receive a
	// live event with ID > maxReplayedID the de-dup logic disengages.
	maxReplayedID, ok := h.replaySnapshot(w, flusher, r.URL.Query().Get("replay"), topicFilter)
	if !ok {
		return
	}

	h.streamLoop(r.Context(), w, flusher, ch, topicFilter, maxReplayedID)
}

// prepareSSEResponse writes the SSE response headers and returns the flusher,
// or false if the writer does not support flushing (in which case an error
// response has already been written).
func prepareSSEResponse(w http.ResponseWriter) (http.Flusher, bool) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return nil, false
	}

	w.WriteHeader(http.StatusOK)
	flusher.Flush()
	return flusher, true
}

// parseTopicFilter parses the comma-separated topic list from the "topics"
// query parameter and returns a set of allowed topics. An empty result means
// "no filter — accept all topics."
func parseTopicFilter(raw string) map[string]struct{} {
	topicFilter := make(map[string]struct{})
	if raw == "" {
		return topicFilter
	}
	for _, t := range strings.Split(raw, ",") {
		t = strings.TrimSpace(t)
		if allowedTopic(t) {
			topicFilter[t] = struct{}{}
		}
	}
	return topicFilter
}

// replaySnapshot drains a bus snapshot of size derived from replayStr,
// applying the topic filter. It returns the highest Event.ID written to the
// wire (0 if no replay happened) and false if the client connection broke
// during replay (caller should return).
func (h *SSE) replaySnapshot(w http.ResponseWriter, flusher http.Flusher, replayStr string, topicFilter map[string]struct{}) (uint64, bool) {
	if replayStr == "" {
		return 0, true
	}
	n, err := strconv.Atoi(replayStr)
	if err != nil || n <= 0 {
		return 0, true
	}
	var maxReplayedID uint64
	for _, e := range h.bus.Snapshot(n) {
		if !topicAllowed(topicFilter, e.Topic) {
			continue
		}
		if err := writeEvent(w, e); err != nil {
			return 0, false
		}
		flusher.Flush()
		if e.ID > maxReplayedID {
			maxReplayedID = e.ID
		}
	}
	return maxReplayedID, true
}

// streamLoop is the main per-client event loop: it forwards live bus events
// to the SSE wire, emits periodic heartbeats, and de-duplicates events whose
// IDs were already delivered during replay.
func (h *SSE) streamLoop(
	ctx context.Context,
	w http.ResponseWriter,
	flusher http.Flusher,
	ch <-chan events.Event,
	topicFilter map[string]struct{},
	maxReplayedID uint64,
) {
	dedupActive := maxReplayedID > 0

	ticker := time.NewTicker(HeartbeatInterval())
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return

		case <-ticker.C:
			// SSE comment frame — discarded by the parser, keeps the TCP connection alive.
			if _, err := fmt.Fprint(w, ": heartbeat\n\n"); err != nil {
				return
			}
			flusher.Flush()

		case e, ok := <-ch:
			if !ok {
				// Channel was closed (unsubscribed).
				return
			}
			// Skip events that were already delivered during the replay
			// phase. Once we see an ID strictly greater than the highest
			// replayed ID we are past the dedup window for good.
			if dedupActive {
				if e.ID <= maxReplayedID {
					continue
				}
				dedupActive = false
			}
			if !topicAllowed(topicFilter, e.Topic) {
				continue
			}
			if err := writeEvent(w, e); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

// topicAllowed reports whether topic passes the (possibly empty) filter set.
// An empty filter means "accept all topics."
func topicAllowed(filter map[string]struct{}, topic string) bool {
	if len(filter) == 0 {
		return true
	}
	_, pass := filter[topic]
	return pass
}

// allowedTopics is the server-side whitelist for SSE topic subscriptions.
var allowedTopics = map[string]struct{}{
	"agent":    {},
	"task":     {},
	"nats":     {},
	"host":     {},
	"log":      {},
	"research": {},
	"pipeline": {},
	"myrmidon": {},
}

// allowedTopic returns true if t is a recognised bus topic.
func allowedTopic(t string) bool {
	_, ok := allowedTopics[t]
	return ok
}

// writeEvent writes a single SSE frame for the given event.
// Format:
//
//	event: {topic}\n
//	data: {payload}\n
//	\n
func writeEvent(w http.ResponseWriter, e events.Event) error {
	payload := []byte(e.Payload)
	if len(payload) == 0 {
		payload = []byte("{}")
	}
	_, err := fmt.Fprintf(w, "event: %s\ndata: %s\n\n", e.Topic, payload)
	return err
}
