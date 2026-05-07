// Package store provides the in-memory cache that backs the Atlas dashboard's
// API responses, populated by the various pollers and the NATS subscriber.
package store

import (
	"sync"
	"time"

	"github.com/HomericIntelligence/atlas/internal/catalog"
	"github.com/HomericIntelligence/atlas/internal/tailscale"
)

// maxAgentEvents is the maximum number of raw events retained per agent.
const maxAgentEvents = 50

// Cache is a thread-safe in-memory store for dashboard state.
// It is extended with new fields as Atlas milestones add data sources.
type Cache struct {
	mu           sync.RWMutex
	devices      []tailscale.Device    // added in #158
	probes       []catalog.ProbeResult
	probesAt     time.Time
	// probes extended in #159
	agents       []AgentRecord         // added in #161
	tasks        []TaskRecord          // added in #161
	natsStats    NATSStats             // added in #161
	agentEvents  map[string][]RawEvent // added in #163: per-agent event history
	natsStreams  []NATSStreamInfo      // added in #165: JetStream stream list
	natsConsumers []NATSConsumerInfo   // added in #165: JetStream consumer list
	natsConns    []NATSConnInfo        // added in #165: NATS connections
}

// NewCache returns an empty Cache.
func NewCache() *Cache { return &Cache{} }

// setSlice writes a defensive copy of src to *dst under mu's write lock.
// All slice-typed Cache setters delegate here; the indirection keeps
// mutex/copy discipline in one place so a future accessor can't drift.
func setSlice[T any](mu *sync.RWMutex, dst *[]T, src []T) {
	cp := make([]T, len(src))
	copy(cp, src)
	mu.Lock()
	*dst = cp
	mu.Unlock()
}

// getSlice returns a defensive copy of *src under mu's read lock.
// Returns nil (not an empty non-nil slice) when *src is empty, matching
// the pre-refactor zero-value behaviour exercised by TestNewCache_EmptyState.
func getSlice[T any](mu *sync.RWMutex, src *[]T) []T {
	mu.RLock()
	defer mu.RUnlock()
	if len(*src) == 0 {
		return nil
	}
	cp := make([]T, len(*src))
	copy(cp, *src)
	return cp
}

// SetProbes replaces the stored probe results and records the timestamp.
// Not routed through setSlice because it has the additional probesAt
// side effect (and historically does not defensive-copy on input).
func (c *Cache) SetProbes(p []catalog.ProbeResult) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.probes = p
	c.probesAt = time.Now()
}

// GetProbes returns a copy of the stored probe results.
func (c *Cache) GetProbes() []catalog.ProbeResult {
	c.mu.RLock()
	defer c.mu.RUnlock()
	out := make([]catalog.ProbeResult, len(c.probes))
	copy(out, c.probes)
	return out
}

// ProbesAge returns the time elapsed since the last SetProbes call.
// Returns a zero duration if SetProbes has never been called.
func (c *Cache) ProbesAge() time.Duration {
	c.mu.RLock()
	defer c.mu.RUnlock()
	if c.probesAt.IsZero() {
		return 0
	}
	return time.Since(c.probesAt)
}

// SetDevices replaces the cached Tailscale device list.
func (c *Cache) SetDevices(d []tailscale.Device) { setSlice(&c.mu, &c.devices, d) }

// GetDevices returns a snapshot of the cached Tailscale device list.
// The returned slice is a copy; mutations do not affect the cache.
func (c *Cache) GetDevices() []tailscale.Device { return getSlice(&c.mu, &c.devices) }

// SetAgents replaces the cached Agamemnon agent list.
func (c *Cache) SetAgents(agents []AgentRecord) { setSlice(&c.mu, &c.agents, agents) }

// GetAgents returns a snapshot of the cached agent list.
// The returned slice is a copy; mutations do not affect the cache.
func (c *Cache) GetAgents() []AgentRecord { return getSlice(&c.mu, &c.agents) }

// SetTasks replaces the cached Agamemnon task list.
func (c *Cache) SetTasks(tasks []TaskRecord) { setSlice(&c.mu, &c.tasks, tasks) }

// GetTasks returns a snapshot of the cached task list.
// The returned slice is a copy; mutations do not affect the cache.
func (c *Cache) GetTasks() []TaskRecord { return getSlice(&c.mu, &c.tasks) }

// SetNATSStats replaces the cached NATS statistics.
func (c *Cache) SetNATSStats(s NATSStats) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.natsStats = s
}

// GetNATSStats returns the cached NATS statistics.
func (c *Cache) GetNATSStats() NATSStats {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.natsStats
}

// SetAgentEvents replaces the full event history slice for agentID.
// Not routed through setSlice because the slice lives inside a map keyed
// by agentID rather than as a top-level field.
func (c *Cache) SetAgentEvents(agentID string, events []RawEvent) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.agentEvents == nil {
		c.agentEvents = make(map[string][]RawEvent)
	}
	cp := make([]RawEvent, len(events))
	copy(cp, events)
	c.agentEvents[agentID] = cp
}

// GetAgentEvents returns a copy of the event history for agentID.
// Returns nil if no events have been recorded for the agent.
func (c *Cache) GetAgentEvents(agentID string) []RawEvent {
	c.mu.RLock()
	defer c.mu.RUnlock()
	evts := c.agentEvents[agentID]
	if len(evts) == 0 {
		return nil
	}
	cp := make([]RawEvent, len(evts))
	copy(cp, evts)
	return cp
}

// AppendAgentEvent appends e to the event history for agentID, capping at maxAgentEvents.
func (c *Cache) AppendAgentEvent(agentID string, e RawEvent) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.agentEvents == nil {
		c.agentEvents = make(map[string][]RawEvent)
	}
	evts := c.agentEvents[agentID]
	evts = append(evts, e)
	if len(evts) > maxAgentEvents {
		evts = evts[len(evts)-maxAgentEvents:]
	}
	c.agentEvents[agentID] = evts
}

// SetNATSStreams replaces the cached JetStream stream list.
func (c *Cache) SetNATSStreams(streams []NATSStreamInfo) { setSlice(&c.mu, &c.natsStreams, streams) }

// GetNATSStreams returns a snapshot of the cached JetStream stream list.
// The returned slice is a copy; mutations do not affect the cache.
func (c *Cache) GetNATSStreams() []NATSStreamInfo { return getSlice(&c.mu, &c.natsStreams) }

// SetNATSConsumers replaces the cached JetStream consumer list.
func (c *Cache) SetNATSConsumers(consumers []NATSConsumerInfo) {
	setSlice(&c.mu, &c.natsConsumers, consumers)
}

// GetNATSConsumers returns a snapshot of the cached JetStream consumer list.
// The returned slice is a copy; mutations do not affect the cache.
func (c *Cache) GetNATSConsumers() []NATSConsumerInfo { return getSlice(&c.mu, &c.natsConsumers) }

// SetNATSConns replaces the cached NATS connection list.
func (c *Cache) SetNATSConns(conns []NATSConnInfo) { setSlice(&c.mu, &c.natsConns, conns) }

// GetNATSConns returns a snapshot of the cached NATS connection list.
// The returned slice is a copy; mutations do not affect the cache.
func (c *Cache) GetNATSConns() []NATSConnInfo { return getSlice(&c.mu, &c.natsConns) }
