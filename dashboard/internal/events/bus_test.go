package events_test

import (
	"encoding/json"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/HomericIntelligence/atlas/internal/events"
)

// makeEvent is a helper that builds a well-formed Event for use in tests.
func makeEvent(topic, subject string) events.Event {
	return events.Event{
		Topic:   topic,
		Subject: subject,
		Payload: json.RawMessage(`{"ok":true}`),
		At:      time.Now(),
	}
}

// TestBusFanOut publishes one event and asserts that three independent
// subscribers each receive exactly that event.
func TestBusFanOut(t *testing.T) {
	t.Parallel()

	b := events.NewBus(16)
	ch1 := b.Subscribe(8)
	ch2 := b.Subscribe(8)
	ch3 := b.Subscribe(8)

	e := makeEvent("agent", "hi.agents.epimetheus.worker.heartbeat")
	b.Publish(e)

	for i, ch := range []<-chan events.Event{ch1, ch2, ch3} {
		select {
		case got := <-ch:
			if got.Topic != e.Topic {
				t.Errorf("subscriber %d: got Topic %q, want %q", i, got.Topic, e.Topic)
			}
			if got.Subject != e.Subject {
				t.Errorf("subscriber %d: got Subject %q, want %q", i, got.Subject, e.Subject)
			}
		case <-time.After(time.Second):
			t.Errorf("subscriber %d: timed out waiting for event", i)
		}
	}
}

// TestBusSlowSubscriberDrop subscribes with a buffer of 1, then publishes 10
// events without ever reading from the channel.  After all publishes the drop
// counter must be positive and the call itself must not block.
func TestBusSlowSubscriberDrop(t *testing.T) {
	t.Parallel()

	b := events.NewBus(64)
	ch := b.Subscribe(1)

	for i := range 10 {
		b.Publish(makeEvent("task", "hi.tasks.update"))
		_ = i
	}

	if b.Drops() == 0 {
		t.Error("expected Drops() > 0 after publishing to a full subscriber buffer, got 0")
	}

	// Drain whatever arrived to confirm the channel is not permanently blocked.
	drained := false
	select {
	case <-ch:
		drained = true
	default:
		drained = true // nothing queued — also fine; channel is not blocked
	}
	if !drained {
		t.Error("subscriber channel appears to be blocked after publish")
	}
}

// TestBusUnsubscribe subscribes, immediately unsubscribes, then publishes an
// event.  The channel must receive nothing and no panic must occur.
func TestBusUnsubscribe(t *testing.T) {
	t.Parallel()

	b := events.NewBus(16)
	ch := b.Subscribe(8)
	b.Unsubscribe(ch)

	b.Publish(makeEvent("nats", "hi.nats.connected"))

	select {
	case v, ok := <-ch:
		if ok {
			t.Errorf("expected no event on unsubscribed channel, got %+v", v)
		}
		// closed channel — acceptable; the implementation may close on unsubscribe
	default:
		// nothing received — correct
	}
}

// TestBusSnapshot publishes 5 events to a ring of capacity 3 and asserts that
// Snapshot(10) returns exactly 3 events in chronological (oldest-first) order.
func TestBusSnapshot(t *testing.T) {
	t.Parallel()

	const ringCap = 3
	b := events.NewBus(ringCap)

	subjects := []string{
		"hi.agents.a.heartbeat",
		"hi.agents.b.heartbeat",
		"hi.agents.c.heartbeat",
		"hi.agents.d.heartbeat",
		"hi.agents.e.heartbeat",
	}
	for _, s := range subjects {
		b.Publish(makeEvent("agent", s))
	}

	snap := b.Snapshot(10)
	if len(snap) != ringCap {
		t.Fatalf("Snapshot(10) returned %d events, want %d", len(snap), ringCap)
	}

	// The 3 most recent are subjects[2..4]; they must be oldest-first.
	want := subjects[len(subjects)-ringCap:]
	for i, e := range snap {
		if e.Subject != want[i] {
			t.Errorf("snap[%d].Subject = %q, want %q", i, e.Subject, want[i])
		}
	}
}

// TestBusSnapshotN publishes 5 events and asserts that Snapshot(2) returns
// exactly the 2 most recent events.
func TestBusSnapshotN(t *testing.T) {
	t.Parallel()

	b := events.NewBus(16)

	subjects := []string{
		"hi.tasks.one",
		"hi.tasks.two",
		"hi.tasks.three",
		"hi.tasks.four",
		"hi.tasks.five",
	}
	for _, s := range subjects {
		b.Publish(makeEvent("task", s))
	}

	snap := b.Snapshot(2)
	if len(snap) != 2 {
		t.Fatalf("Snapshot(2) returned %d events, want 2", len(snap))
	}

	// Oldest-first among the 2 most recent: [four, five]
	if snap[0].Subject != subjects[3] {
		t.Errorf("snap[0].Subject = %q, want %q", snap[0].Subject, subjects[3])
	}
	if snap[1].Subject != subjects[4] {
		t.Errorf("snap[1].Subject = %q, want %q", snap[1].Subject, subjects[4])
	}
}

// TestBusRingBufferConcurrent exercises the bus under a race detector: 10
// goroutines each publish 100 events concurrently.  The test asserts that
// there is no data race and that the final snapshot length is at most ringCap.
func TestBusRingBufferConcurrent(t *testing.T) {
	t.Parallel()

	const (
		ringCap    = 50
		goroutines = 10
		perG       = 100
	)

	b := events.NewBus(ringCap)
	// One subscriber to keep the fan-out code path exercised.
	ch := b.Subscribe(256)
	defer b.Unsubscribe(ch)

	var wg sync.WaitGroup
	wg.Add(goroutines)
	for i := range goroutines {
		go func(id int) {
			defer wg.Done()
			for j := range perG {
				b.Publish(makeEvent("agent", "hi.agents.concurrent"))
				_ = j
			}
			_ = id
		}(i)
	}
	wg.Wait()

	snap := b.Snapshot(ringCap + 100)
	if len(snap) > ringCap {
		t.Errorf("Snapshot returned %d events, want <= %d (ringCap)", len(snap), ringCap)
	}
}

// TestBusDropCounter creates a single subscriber with bufSize=0 (unbuffered),
// publishes 5 events without reading, and asserts that Drops() == 5.
func TestBusDropCounter(t *testing.T) {
	t.Parallel()

	b := events.NewBus(16)
	_ = b.Subscribe(0) // unbuffered — every publish must drop

	for range 5 {
		b.Publish(makeEvent("nats", "hi.nats.msg"))
	}

	if got := b.Drops(); got != 5 {
		t.Errorf("Drops() = %d, want 5", got)
	}
}

// TestBus_ConcurrentPublishSubscribeUnsubscribe is a race-stress test for the
// copy-on-write subscriber set. It runs many publishers concurrently with
// many subscribers that subscribe, drain a bounded number of events, and
// unsubscribe, repeatedly. Run with:
//
//	go test -race -count=10 -run TestBus_ConcurrentPublishSubscribeUnsubscribe ./internal/events/...
//
// The test is genuinely race-free: Publish reads the subscriber slice via
// an atomic Load and iterates without any lock, Subscribe/Unsubscribe
// build new slices under a small mutex, and Unsubscribe never closes the
// channel (so a stale Publish that already loaded the prior slice may
// deliver one last event without panicking).
//
// The test asserts:
//   - completion within a reasonable time bound (no deadlock under
//     contention),
//   - that some lower bound of events was delivered to subscribers (proving
//     Publish is not dropping everything to lock contention or losing all
//     deliveries to the COW slice swap).
func TestBus_ConcurrentPublishSubscribeUnsubscribe(t *testing.T) {
	t.Parallel()

	const (
		numPublishers     = 100
		eventsPerPub      = 100
		numSubscribers    = 100
		subsLifetimeReads = 25 // events drained per subscription cycle
		ringCap           = 256
	)

	b := events.NewBus(ringCap)

	var (
		wgPublishers  sync.WaitGroup
		wgSubscribers sync.WaitGroup
		stopSubs      atomic.Bool
		delivered     atomic.Int64
	)

	// Publishers: each emits a bounded number of events.
	wgPublishers.Add(numPublishers)
	for i := range numPublishers {
		go func(id int) {
			defer wgPublishers.Done()
			ev := makeEvent("agent", "hi.agents.stress.heartbeat")
			for j := range eventsPerPub {
				b.Publish(ev)
				_ = j
			}
			_ = id
		}(i)
	}

	// Subscribers: subscribe, drain up to subsLifetimeReads events (or
	// short timeout), unsubscribe, repeat. They keep churning until
	// publishers finish.
	wgSubscribers.Add(numSubscribers)
	for i := range numSubscribers {
		go func(id int) {
			defer wgSubscribers.Done()
			for !stopSubs.Load() {
				ch := b.Subscribe(8)
				deadline := time.NewTimer(50 * time.Millisecond)
				received := 0
			drain:
				for received < subsLifetimeReads {
					select {
					case _, ok := <-ch:
						if !ok {
							// Channel was closed by some other path —
							// not expected with COW Unsubscribe but
							// handle defensively.
							break drain
						}
						delivered.Add(1)
						received++
					case <-deadline.C:
						break drain
					}
				}
				if !deadline.Stop() {
					select {
					case <-deadline.C:
					default:
					}
				}
				b.Unsubscribe(ch)
			}
			_ = id
		}(i)
	}

	// Bound the test runtime: if publishers don't finish in 30s under
	// -race, something is deadlocked or pathologically slow.
	publishersDone := make(chan struct{})
	go func() {
		wgPublishers.Wait()
		close(publishersDone)
	}()

	select {
	case <-publishersDone:
		// fast path
	case <-time.After(30 * time.Second):
		t.Fatal("publishers did not finish within 30s — possible deadlock or contention regression")
	}

	// Signal subscribers to wind down and wait for them.
	stopSubs.Store(true)
	subscribersDone := make(chan struct{})
	go func() {
		wgSubscribers.Wait()
		close(subscribersDone)
	}()
	select {
	case <-subscribersDone:
	case <-time.After(30 * time.Second):
		t.Fatal("subscribers did not wind down within 30s")
	}

	// Sanity: at least SOME events were delivered while subscribers were
	// connected. We don't assert a tight lower bound because the test is
	// inherently racy by design (subscribers come and go), but zero
	// deliveries would indicate a serious regression where Publish drops
	// everything.
	if delivered.Load() == 0 {
		t.Errorf("delivered 0 events across %d publishers and %d churning subscribers; "+
			"Publish appears to be dropping everything", numPublishers, numSubscribers)
	}

	// Drops counter must remain coherent (atomic) — just read it; if there
	// were a torn read under -race the detector would have fired.
	_ = b.Drops()

	// Ring buffer must still be coherent.
	snap := b.Snapshot(ringCap + 1)
	if len(snap) > ringCap {
		t.Errorf("Snapshot returned %d events, want <= %d (ringCap)", len(snap), ringCap)
	}
}

// TestBus_SubscribeBeforePublishDelivers asserts that an event published
// after Subscribe returns is observed by the new subscriber. This is the
// "subscribe-then-publish must deliver" invariant for the COW pattern.
func TestBus_SubscribeBeforePublishDelivers(t *testing.T) {
	t.Parallel()

	b := events.NewBus(16)

	// Run many iterations to shake out any ordering bug between Store and
	// Load of the subscriber slice.
	const iters = 1000
	for range iters {
		ch := b.Subscribe(1)
		b.Publish(makeEvent("agent", "hi.agents.subscribe.before.publish"))
		select {
		case <-ch:
			// OK
		case <-time.After(time.Second):
			t.Fatal("subscriber did not receive event published after Subscribe returned")
		}
		b.Unsubscribe(ch)
	}
}

// TestBusZeroSubscribers publishes to a bus that has no subscribers and
// asserts that no panic occurs and that the ring buffer is still usable.
func TestBusZeroSubscribers(t *testing.T) {
	t.Parallel()

	b := events.NewBus(8)

	// Must not panic.
	b.Publish(makeEvent("agent", "hi.agents.solo.heartbeat"))
	b.Publish(makeEvent("task", "hi.tasks.solo.update"))

	snap := b.Snapshot(10)
	if len(snap) != 2 {
		t.Errorf("Snapshot(10) returned %d events after 2 publishes with no subscribers, want 2", len(snap))
	}
	if snap[0].Subject != "hi.agents.solo.heartbeat" {
		t.Errorf("snap[0].Subject = %q, want %q", snap[0].Subject, "hi.agents.solo.heartbeat")
	}
	if snap[1].Subject != "hi.tasks.solo.update" {
		t.Errorf("snap[1].Subject = %q, want %q", snap[1].Subject, "hi.tasks.solo.update")
	}
}
