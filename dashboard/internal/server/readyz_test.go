package server

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// fakePoller satisfies pollerLike for tests without importing internal/poller.
type fakePoller struct {
	name string
	last time.Time
	err  error
}

func (f *fakePoller) Name() string             { return f.name }
func (f *fakePoller) LastSuccess() time.Time   { return f.last }
func (f *fakePoller) LastError() error         { return f.err }

// fakeNATS satisfies natsReadyLike for tests.
type fakeNATS struct {
	ready    bool
	attached int
}

func (f *fakeNATS) Ready() bool   { return f.ready }
func (f *fakeNATS) Attached() int { return f.attached }

func TestPollerCheck_NeverSucceeded(t *testing.T) {
	p := &fakePoller{name: "agamemnon"}
	got := PollerCheck(p, 5*time.Second)()
	if got.OK {
		t.Errorf("want OK=false on never-succeeded poller, got %+v", got)
	}
	if got.Error == "" {
		t.Errorf("want non-empty Error on never-succeeded poller, got %+v", got)
	}
	if got.Name != "agamemnon" {
		t.Errorf("Name: got %q, want %q", got.Name, "agamemnon")
	}
}

func TestPollerCheck_RecentSuccess(t *testing.T) {
	p := &fakePoller{name: "nats", last: time.Now()}
	got := PollerCheck(p, 5*time.Second)()
	if !got.OK {
		t.Errorf("want OK=true on recent-success poller, got %+v", got)
	}
	if got.Error != "" {
		t.Errorf("want empty Error on healthy poller, got %q", got.Error)
	}
	if got.LastSuccess == "" {
		t.Errorf("want non-empty LastSuccess timestamp, got empty")
	}
}

func TestPollerCheck_StaleSuccess(t *testing.T) {
	old := time.Now().Add(-1 * time.Hour)
	p := &fakePoller{name: "nats", last: old}
	got := PollerCheck(p, 5*time.Second)()
	if got.OK {
		t.Errorf("want OK=false on stale poller, got %+v", got)
	}
	if got.Error == "" || got.Error[:5] != "stale" {
		t.Errorf("want stale-message Error, got %q", got.Error)
	}
}

func TestPollerCheck_LastError(t *testing.T) {
	p := &fakePoller{name: "nats", last: time.Now(), err: errors.New("boom")}
	got := PollerCheck(p, 5*time.Second)()
	if got.OK {
		t.Errorf("want OK=false when LastError is set, got %+v", got)
	}
	if got.Error != "boom" {
		t.Errorf("Error: got %q, want %q", got.Error, "boom")
	}
}

func TestNATSCheck_NotReady(t *testing.T) {
	got := NATSCheck("nats-subscriber", &fakeNATS{ready: false}, 6)()
	if got.OK {
		t.Errorf("want OK=false on not-ready subscriber, got %+v", got)
	}
}

func TestNATSCheck_FullyAttached(t *testing.T) {
	got := NATSCheck("nats-subscriber", &fakeNATS{ready: true, attached: 6}, 6)()
	if !got.OK {
		t.Errorf("want OK=true on fully-attached subscriber, got %+v", got)
	}
	if got.Note != "" {
		t.Errorf("want empty Note on fully-attached, got %q", got.Note)
	}
}

func TestNATSCheck_Partial(t *testing.T) {
	got := NATSCheck("nats-subscriber", &fakeNATS{ready: true, attached: 4}, 6)()
	if !got.OK {
		t.Errorf("want OK=true on partial subscriber (still serving 4 streams), got %+v", got)
	}
	if got.Note == "" {
		t.Errorf("want Note describing partial attach, got empty")
	}
}

func TestRegistry_AllOK_EmptyIsNotReady(t *testing.T) {
	reg := &ReadyRegistry{}
	if reg.AllOK() {
		t.Error("empty registry must report AllOK=false")
	}
}

func TestRegistry_AllOK(t *testing.T) {
	reg := &ReadyRegistry{}
	reg.Register(func() ReadyComponent { return ReadyComponent{Name: "a", OK: true} })
	reg.Register(func() ReadyComponent { return ReadyComponent{Name: "b", OK: true} })
	if !reg.AllOK() {
		t.Error("two-OK registry must report AllOK=true")
	}
}

func TestRegistry_OneFails(t *testing.T) {
	reg := &ReadyRegistry{}
	reg.Register(func() ReadyComponent { return ReadyComponent{Name: "a", OK: true} })
	reg.Register(func() ReadyComponent { return ReadyComponent{Name: "b", OK: false, Error: "down"} })
	if reg.AllOK() {
		t.Error("registry with one failing component must report AllOK=false")
	}
}

func TestReadyzHandler_AllOK_Returns200(t *testing.T) {
	reg := &ReadyRegistry{}
	reg.Register(func() ReadyComponent { return ReadyComponent{Name: "a", OK: true} })
	reg.Register(func() ReadyComponent { return ReadyComponent{Name: "b", OK: true} })

	rr := httptest.NewRecorder()
	MakeReadyzHandler(reg)(rr, httptest.NewRequest(http.MethodGet, "/readyz", nil))

	if rr.Code != http.StatusOK {
		t.Errorf("status: got %d, want 200", rr.Code)
	}

	var body struct {
		OK         bool             `json:"ok"`
		Components []ReadyComponent `json:"components"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &body); err != nil {
		t.Fatalf("unmarshal body: %v", err)
	}
	if !body.OK {
		t.Errorf("body.ok: want true, got false")
	}
	if len(body.Components) != 2 {
		t.Errorf("components: got %d, want 2", len(body.Components))
	}
}

func TestReadyzHandler_OneFails_Returns503(t *testing.T) {
	reg := &ReadyRegistry{}
	reg.Register(func() ReadyComponent { return ReadyComponent{Name: "agamemnon", OK: true} })
	reg.Register(func() ReadyComponent { return ReadyComponent{Name: "nats", OK: false, Error: "unreachable"} })

	rr := httptest.NewRecorder()
	MakeReadyzHandler(reg)(rr, httptest.NewRequest(http.MethodGet, "/readyz", nil))

	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("status: got %d, want 503", rr.Code)
	}

	var body struct {
		OK         bool             `json:"ok"`
		Components []ReadyComponent `json:"components"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &body); err != nil {
		t.Fatalf("unmarshal body: %v", err)
	}
	if body.OK {
		t.Errorf("body.ok: want false, got true")
	}
	// Components are sorted by Name; nats comes after agamemnon.
	if len(body.Components) != 2 {
		t.Fatalf("components: got %d, want 2", len(body.Components))
	}
	var natsComp *ReadyComponent
	for i := range body.Components {
		if body.Components[i].Name == "nats" {
			natsComp = &body.Components[i]
			break
		}
	}
	if natsComp == nil {
		t.Fatal("nats component missing from response")
	}
	if natsComp.OK {
		t.Error("nats component must report OK=false")
	}
	if natsComp.Error != "unreachable" {
		t.Errorf("nats Error: got %q, want %q", natsComp.Error, "unreachable")
	}
}

func TestReadyzHandler_EmptyRegistry_Returns503(t *testing.T) {
	rr := httptest.NewRecorder()
	MakeReadyzHandler(&ReadyRegistry{})(rr, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("empty registry status: got %d, want 503", rr.Code)
	}
}
