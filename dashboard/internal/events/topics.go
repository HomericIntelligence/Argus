package events

import (
	"encoding/json"
	"time"
)

// Event is a single observable occurrence within the Atlas dashboard.
// Topic identifies the broad category (e.g. "agent", "task", "nats") and
// Subject is the fully-qualified NATS-style subject string.
//
// ID is a monotonically-increasing per-Bus identity stamped by Bus.Publish.
// Callers MUST NOT set ID — any value supplied by the caller is overwritten
// inside Publish. The ID is used by SSE replay to de-duplicate events that
// appear in BOTH the ring-buffer snapshot and the live subscriber channel
// during the subscribe-then-snapshot replay window.
type Event struct {
	ID      uint64
	Topic   string
	Subject string
	Payload json.RawMessage
	At      time.Time
}
