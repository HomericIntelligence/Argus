package store

import (
	"encoding/json"
	"sync"
	"testing"
	"time"

	"github.com/HomericIntelligence/atlas/internal/catalog"
	"github.com/HomericIntelligence/atlas/internal/tailscale"
)

// TestNewCache_EmptyState verifies a fresh cache returns nil/zero values for
// every accessor — operators must be able to call /api/hosts, /agents, etc.
// before the first poll cycle without Atlas crashing.
func TestNewCache_EmptyState(t *testing.T) {
	c := NewCache()
	if got := c.GetDevices(); got != nil {
		t.Errorf("GetDevices on empty cache: got %+v, want nil", got)
	}
	if got := c.GetAgents(); got != nil {
		t.Errorf("GetAgents on empty cache: got %+v, want nil", got)
	}
	if got := c.GetTasks(); got != nil {
		t.Errorf("GetTasks on empty cache: got %+v, want nil", got)
	}
	if got := c.GetProbes(); len(got) != 0 {
		t.Errorf("GetProbes on empty cache: got %d, want 0", len(got))
	}
	if got := c.ProbesAge(); got != 0 {
		t.Errorf("ProbesAge on empty cache: got %v, want 0", got)
	}
	if got := c.GetNATSStreams(); got != nil {
		t.Errorf("GetNATSStreams on empty cache: got %+v, want nil", got)
	}
	if got := c.GetNATSConsumers(); got != nil {
		t.Errorf("GetNATSConsumers on empty cache: got %+v, want nil", got)
	}
	if got := c.GetNATSConns(); got != nil {
		t.Errorf("GetNATSConns on empty cache: got %+v, want nil", got)
	}
	if got := c.GetAgentEvents("nonexistent"); got != nil {
		t.Errorf("GetAgentEvents on empty cache: got %+v, want nil", got)
	}
}

// TestSetAndGet_Devices verifies Set/Get round-trip and that the returned
// slice is a defensive copy (mutations to the result do not corrupt the
// cache).
func TestSetAndGet_Devices(t *testing.T) {
	c := NewCache()
	in := []tailscale.Device{
		{Hostname: "alpha", TailscaleIP: "100.64.0.1", Online: true},
		{Hostname: "beta", TailscaleIP: "100.64.0.2", Online: false},
	}
	c.SetDevices(in)

	got := c.GetDevices()
	if len(got) != 2 {
		t.Fatalf("GetDevices: got %d, want 2", len(got))
	}
	if got[0].Hostname != "alpha" || got[1].Hostname != "beta" {
		t.Errorf("GetDevices: order or content wrong; got %+v", got)
	}

	// Mutate the returned copy; cache must be unaffected.
	got[0].Hostname = "MUTATED"
	again := c.GetDevices()
	if again[0].Hostname != "alpha" {
		t.Errorf("cache not defensively copied: GetDevices()[0].Hostname = %q after external mutation", again[0].Hostname)
	}
}

func TestSetAndGet_Agents(t *testing.T) {
	c := NewCache()
	in := []AgentRecord{
		{ID: "a1", Name: "agent-1", Host: "alpha", Status: "online", UpdatedAt: time.Now()},
		{ID: "a2", Name: "agent-2", Host: "beta", Status: "offline", UpdatedAt: time.Now()},
	}
	c.SetAgents(in)
	got := c.GetAgents()
	if len(got) != 2 {
		t.Fatalf("GetAgents: got %d, want 2", len(got))
	}
	got[0].Name = "MUTATED"
	if c.GetAgents()[0].Name != "agent-1" {
		t.Error("cache not defensively copied for agents")
	}
}

func TestSetAndGet_Tasks(t *testing.T) {
	c := NewCache()
	in := []TaskRecord{
		{ID: "t1", TeamID: "team-1", Subject: "review", Status: "in_progress"},
	}
	c.SetTasks(in)
	got := c.GetTasks()
	if len(got) != 1 || got[0].Subject != "review" {
		t.Errorf("GetTasks: got %+v, want one task with subject 'review'", got)
	}
	got[0].Subject = "MUTATED"
	if c.GetTasks()[0].Subject != "review" {
		t.Error("cache not defensively copied for tasks")
	}
}

func TestSetAndGet_NATSStats(t *testing.T) {
	c := NewCache()
	c.SetNATSStats(NATSStats{Connections: 5, Streams: 6, InMsgs: 100, OutMsgs: 200})
	got := c.GetNATSStats()
	if got.Connections != 5 || got.Streams != 6 || got.InMsgs != 100 || got.OutMsgs != 200 {
		t.Errorf("GetNATSStats: got %+v, want {5,6,100,200}", got)
	}
}

func TestSetAndGet_Probes_UpdatesProbesAge(t *testing.T) {
	c := NewCache()
	c.SetProbes([]catalog.ProbeResult{
		{Host: "alpha", ServiceDef: catalog.ServiceDef{Name: "agamemnon"}, URL: "http://alpha:8080/v1/health", OK: true},
	})
	got := c.GetProbes()
	if len(got) != 1 || got[0].Host != "alpha" {
		t.Errorf("GetProbes: got %+v, want one alpha probe", got)
	}
	age := c.ProbesAge()
	if age <= 0 || age > time.Second {
		t.Errorf("ProbesAge after fresh SetProbes: got %v, want >0 and <1s", age)
	}
}

func TestAppendAgentEvent_CapsAtMax(t *testing.T) {
	c := NewCache()
	for i := 0; i < maxAgentEvents+10; i++ {
		c.AppendAgentEvent("a1", RawEvent{Topic: "agent", Subject: "hi.agents.alpha.heartbeat", Payload: json.RawMessage(`{}`), ReceivedAt: time.Now()})
	}
	got := c.GetAgentEvents("a1")
	if len(got) != maxAgentEvents {
		t.Errorf("GetAgentEvents after %d appends: got %d, want %d (cap)", maxAgentEvents+10, len(got), maxAgentEvents)
	}
}

func TestSetAgentEvents_DefensiveCopy(t *testing.T) {
	c := NewCache()
	in := []RawEvent{
		{Topic: "agent", Subject: "hi.agents.alpha.heartbeat", Payload: json.RawMessage(`{}`), ReceivedAt: time.Now()},
	}
	c.SetAgentEvents("a1", in)
	got := c.GetAgentEvents("a1")
	if len(got) != 1 {
		t.Fatalf("GetAgentEvents: got %d, want 1", len(got))
	}
	got[0].Subject = "MUTATED"
	if c.GetAgentEvents("a1")[0].Subject == "MUTATED" {
		t.Error("cache not defensively copied for agent events")
	}
}

func TestSetAndGet_NATSStreams(t *testing.T) {
	c := NewCache()
	in := []NATSStreamInfo{
		{Name: "homeric-agents", Subjects: []string{"hi.agents.>"}, Messages: 42, Consumers: 1},
	}
	c.SetNATSStreams(in)
	got := c.GetNATSStreams()
	if len(got) != 1 || got[0].Name != "homeric-agents" {
		t.Errorf("GetNATSStreams: got %+v, want one homeric-agents stream", got)
	}
}

func TestSetAndGet_NATSConns(t *testing.T) {
	c := NewCache()
	in := []NATSConnInfo{
		{Name: "atlas-cli", IP: "10.0.0.1", Subscriptions: 6, Uptime: "2h"},
	}
	c.SetNATSConns(in)
	got := c.GetNATSConns()
	if len(got) != 1 || got[0].Name != "atlas-cli" {
		t.Errorf("GetNATSConns: got %+v, want one atlas-cli conn", got)
	}
}

// TestConcurrentSetGet_NoRaces validates the mutex discipline by running
// many concurrent writers against the cache while concurrent readers
// snapshot every accessor. The race detector (go test -race) is the
// primary assertion; this test passes if no race is reported.
func TestConcurrentSetGet_NoRaces(_ *testing.T) {
	c := NewCache()
	var wg sync.WaitGroup

	// Seed once so readers get non-empty results.
	c.SetDevices([]tailscale.Device{{Hostname: "alpha", TailscaleIP: "100.64.0.1", Online: true}})
	c.SetAgents([]AgentRecord{{ID: "a1", Name: "agent-1"}})
	c.SetTasks([]TaskRecord{{ID: "t1"}})
	c.SetProbes([]catalog.ProbeResult{{Host: "alpha", ServiceDef: catalog.ServiceDef{Name: "agamemnon"}, OK: true}})
	c.SetNATSStats(NATSStats{Connections: 1})

	const writers = 8
	const readers = 8
	const iters = 200

	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			for j := 0; j < iters; j++ {
				c.SetDevices([]tailscale.Device{{Hostname: "alpha"}, {Hostname: "beta"}})
				c.SetAgents([]AgentRecord{{ID: "a1"}, {ID: "a2"}})
				c.SetTasks([]TaskRecord{{ID: "t1"}, {ID: "t2"}})
				c.SetProbes([]catalog.ProbeResult{{Host: "alpha", OK: true}})
				c.SetNATSStats(NATSStats{Connections: id})
				c.AppendAgentEvent("a1", RawEvent{Topic: "agent"})
			}
		}(i)
	}
	for i := 0; i < readers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < iters; j++ {
				_ = c.GetDevices()
				_ = c.GetAgents()
				_ = c.GetTasks()
				_ = c.GetProbes()
				_ = c.ProbesAge()
				_ = c.GetNATSStats()
				_ = c.GetAgentEvents("a1")
			}
		}()
	}
	wg.Wait()
}
