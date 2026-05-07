package server

import (
	"encoding/json"
	"net/http"
	"sort"
	"sync"
	"time"
)

// ReadyComponent describes the live readiness state of one Atlas subsystem
// (NATS subscriber, Agamemnon poller, NATS poller, Tailscale refresher,
// service prober, …). The /readyz handler renders one of these per registered
// component.
//
// "Ready" means: loaded, enabled, and reporting valid results without error.
// A component that is intentionally disabled (e.g. Tailscale API mode when no
// API key is set) reports OK=true and Note="disabled" rather than failing.
type ReadyComponent struct {
	Name        string `json:"name"`
	OK          bool   `json:"ok"`
	Error       string `json:"error,omitempty"`
	LastSuccess string `json:"last_success,omitempty"`
	Note        string `json:"note,omitempty"`
}

// ReadyCheck is a single readiness probe — invoked synchronously when /readyz
// is requested. Implementations must be cheap (microseconds) and must not
// perform network IO. They should read from in-process state (atomics, mutex
// snapshots) only.
type ReadyCheck func() ReadyComponent

// ReadyRegistry holds the set of ReadyChecks /readyz aggregates over.
// It is safe for concurrent use; checks are added at startup before any HTTP
// traffic arrives.
type ReadyRegistry struct {
	mu     sync.RWMutex
	checks []ReadyCheck
}

// Register adds a check to the registry. Order is preserved in the JSON
// response so operators can scan a familiar layout.
func (r *ReadyRegistry) Register(c ReadyCheck) {
	r.mu.Lock()
	r.checks = append(r.checks, c)
	r.mu.Unlock()
}

// Snapshot evaluates every registered check and returns the per-component
// results. The returned slice is sorted by component name for stable output.
func (r *ReadyRegistry) Snapshot() []ReadyComponent {
	r.mu.RLock()
	checks := append([]ReadyCheck(nil), r.checks...)
	r.mu.RUnlock()

	out := make([]ReadyComponent, 0, len(checks))
	for _, c := range checks {
		out = append(out, c())
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// AllOK reports whether every component in the registry returned OK=true.
// An empty registry is considered NOT ready: a server with no readiness
// checks is by definition not yet wired and should fail probes.
func (r *ReadyRegistry) AllOK() bool {
	snap := r.Snapshot()
	if len(snap) == 0 {
		return false
	}
	for _, c := range snap {
		if !c.OK {
			return false
		}
	}
	return true
}

// pollerLike is the subset of *poller.* base methods we read in the readyz
// hook. Defined locally so server avoids a hard import on poller (which would
// be a layering inversion).
type pollerLike interface {
	Name() string
	LastSuccess() time.Time
	LastError() error
}

// PollerCheck builds a ReadyCheck for a poller. The component is OK when:
//   - the most recent fetch attempt did not return an error, AND
//   - LastSuccess is non-zero AND within maxAge.
//
// Use 2*pollInterval as a sensible maxAge so a single dropped cycle does not
// flap readiness.
func PollerCheck(p pollerLike, maxAge time.Duration) ReadyCheck {
	return func() ReadyComponent {
		c := ReadyComponent{Name: p.Name()}
		last := p.LastSuccess()
		if err := p.LastError(); err != nil {
			c.Error = err.Error()
			if !last.IsZero() {
				c.LastSuccess = last.UTC().Format(time.RFC3339)
			}
			return c
		}
		if last.IsZero() {
			c.Error = "no successful poll yet"
			return c
		}
		age := time.Since(last)
		c.LastSuccess = last.UTC().Format(time.RFC3339)
		if age > maxAge {
			c.Error = "stale: last success was " + age.Round(time.Second).String() + " ago"
			return c
		}
		c.OK = true
		return c
	}
}

// natsReadyLike is the subset of *nats.Subscriber methods we read.
type natsReadyLike interface {
	Ready() bool
	Attached() int
}

// NATSCheck builds a ReadyCheck for the JetStream subscriber. The component
// is OK when Ready() returns true (Start has connected to NATS, attached at
// least one durable subscription, and not begun shutdown).
func NATSCheck(name string, s natsReadyLike, configured int) ReadyCheck {
	return func() ReadyComponent {
		c := ReadyComponent{Name: name}
		if !s.Ready() {
			c.Error = "not connected"
			return c
		}
		c.OK = true
		attached := s.Attached()
		if attached < configured {
			c.Note = "partial: " + itoa(attached) + "/" + itoa(configured) + " streams attached"
		}
		return c
	}
}

// itoa is a small int-to-string helper to avoid pulling strconv into the
// hot-path readyz allocation. Inlined.
func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	var buf [20]byte
	pos := len(buf)
	neg := i < 0
	if neg {
		i = -i
	}
	for i > 0 {
		pos--
		buf[pos] = byte('0' + i%10)
		i /= 10
	}
	if neg {
		pos--
		buf[pos] = '-'
	}
	return string(buf[pos:])
}

// MakeReadyzHandler returns the /readyz HTTP handler bound to the given
// registry. 200 with a per-component JSON body if every check is OK; 503 with
// the same body shape if any check fails.
func MakeReadyzHandler(reg *ReadyRegistry) http.HandlerFunc {
	return func(w http.ResponseWriter, _ *http.Request) {
		snap := reg.Snapshot()
		body := map[string]any{
			"ok":         true,
			"components": snap,
		}
		status := http.StatusOK
		for _, c := range snap {
			if !c.OK {
				body["ok"] = false
				status = http.StatusServiceUnavailable
				break
			}
		}
		if len(snap) == 0 {
			body["ok"] = false
			status = http.StatusServiceUnavailable
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(body)
	}
}
