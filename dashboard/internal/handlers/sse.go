package handlers

import (
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
var heartbeatNanos atomic.Int64

func init() {
	heartbeatNanos.Store(int64(15 * time.Second))
}

// HeartbeatInterval returns the current heartbeat interval.
func HeartbeatInterval() time.Duration {
	return time.Duration(heartbeatNanos.Load())
}

// SetHeartbeatInterval sets the heartbeat interval. Safe for concurrent use; intended for tests.
func SetHeartbeatInterval(d time.Duration) {
	heartbeatNanos.Store(int64(d))
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
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	ctx := r.Context()

	topicFilter := make(map[string]struct{})
	if raw := r.URL.Query().Get("topics"); raw != "" {
		for _, t := range strings.Split(raw, ",") {
			t = strings.TrimSpace(t)
			if allowedTopic(t) {
				topicFilter[t] = struct{}{}
			}
		}
	}

	if replayStr := r.URL.Query().Get("replay"); replayStr != "" {
		if n, err := strconv.Atoi(replayStr); err == nil && n > 0 {
			for _, e := range h.bus.Snapshot(n) {
				if err := writeEvent(w, e); err != nil {
					return
				}
				flusher.Flush()
			}
		}
	}

	ch := h.bus.Subscribe(1000)
	defer h.bus.Unsubscribe(ch)

	// Track this connection in the gauge so /metrics reflects live load.
	cur := h.connected.Add(1)
	(*h.metrics.Load()).SetSSEConnectedClients(cur)
	defer func() {
		cur := h.connected.Add(-1)
		(*h.metrics.Load()).SetSSEConnectedClients(cur)
	}()

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
			// Apply topic filter.
			if len(topicFilter) > 0 {
				if _, pass := topicFilter[e.Topic]; !pass {
					continue
				}
			}
			if err := writeEvent(w, e); err != nil {
				return
			}
			flusher.Flush()
		}
	}
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
