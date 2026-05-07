package events

import (
	"sync"
	"sync/atomic"
)

// ringBuffer is a fixed-capacity circular buffer of Events.
// When full, Push overwrites the oldest entry.
type ringBuffer struct {
	buf  []Event
	cap  int
	head int // index of the oldest entry (valid when size > 0)
	tail int // index where the next entry will be written
	size int
}

func newRingBuffer(cap int) *ringBuffer {
	if cap <= 0 {
		cap = 1
	}
	return &ringBuffer{
		buf: make([]Event, cap),
		cap: cap,
	}
}

// Push appends e to the ring, overwriting the oldest entry when full.
func (r *ringBuffer) Push(e Event) {
	r.buf[r.tail] = e
	r.tail = (r.tail + 1) % r.cap
	if r.size < r.cap {
		r.size++
	} else {
		// Overwrite: advance head past the overwritten slot.
		r.head = (r.head + 1) % r.cap
	}
}

// Snapshot returns up to n most-recent events in chronological (oldest-first)
// order. The returned slice is a copy.
func (r *ringBuffer) Snapshot(n int) []Event {
	if r.size == 0 {
		return nil
	}
	count := r.size
	if n < count {
		count = n
	}
	out := make([]Event, count)
	// The ring contains r.size events; the oldest is at r.head.
	// We want the last `count` of them, so start at offset (r.size - count)
	// from the head.
	start := (r.head + (r.size - count)) % r.cap
	for i := range count {
		out[i] = r.buf[(start+i)%r.cap]
	}
	return out
}

// Bus is an in-process pub/sub event bus with a ring-buffer history and
// non-blocking fan-out to registered subscribers.
//
// The subscriber set is stored as an immutable slice behind an
// atomic.Pointer (copy-on-write). Publish reads the slice with a single
// atomic load and iterates it lock-free; only the ring-buffer push uses a
// narrow mutex. Subscribe and Unsubscribe take a small mutex (subsMu),
// build a NEW slice, and Store it. This eliminates the contention
// bottleneck where N concurrent publishers would otherwise serialize on a
// single write-lock during fan-out.
//
// Concurrency invariants:
//
//   - Subscribe stores the new slice before returning, so any Publish that
//     starts after Subscribe returns will observe the new subscriber.
//   - Unsubscribe atomically swaps in a new slice without the channel; any
//     Publish whose Load() preceded the Store may still send one final event
//     to the channel. Unsubscribe does NOT close the channel — closing while
//     a concurrent Publish is mid-send would race. The caller is expected to
//     drop the channel reference; the channel will be GC'd once no goroutine
//     holds it. (The existing SSE handler exits its read loop before
//     Unsubscribe runs, so this is the natural pattern.)
//   - The drops counter is updated atomically and not protected by any lock.
type Bus struct {
	// subs is an *atomic.Pointer to an immutable []chan Event. Reads in
	// Publish are lock-free.
	subs atomic.Pointer[[]chan Event]
	// subsMu serializes Subscribe / Unsubscribe writers building the new
	// slice. It is never held during fan-out.
	subsMu sync.Mutex

	// ringMu protects the ring buffer; held only during Push/Snapshot.
	ringMu sync.Mutex
	ring   *ringBuffer

	// drops counts events not delivered due to a full subscriber channel.
	drops int64
	// seq is a monotonically-increasing counter used to stamp Event.ID inside
	// Publish so that consumers (e.g. the SSE handler) can de-duplicate events
	// that appear in BOTH a ring-buffer snapshot and a live subscriber channel.
	seq uint64
}

// NewBus returns a new Bus whose ring buffer holds at most ringCap events.
func NewBus(ringCap int) *Bus {
	b := &Bus{
		ring: newRingBuffer(ringCap),
	}
	empty := make([]chan Event, 0)
	b.subs.Store(&empty)
	return b
}

// Subscribe returns a new channel that will receive published events.
// bufSize is the channel's buffer depth; use 0 for an unbuffered channel.
//
// Any Publish call that begins after Subscribe returns is guaranteed to see
// the new subscriber.
func (b *Bus) Subscribe(bufSize int) <-chan Event {
	ch := make(chan Event, bufSize)
	b.subsMu.Lock()
	cur := b.subs.Load()
	next := make([]chan Event, len(*cur)+1)
	copy(next, *cur)
	next[len(*cur)] = ch
	b.subs.Store(&next)
	b.subsMu.Unlock()
	return ch
}

// Unsubscribe removes ch from the subscriber set.
//
// Note: Unsubscribe does NOT close the channel. Closing while a concurrent
// Publish (whose atomic Load preceded our Store) is mid-send would race and
// could panic with "send on closed channel". Instead, after Unsubscribe
// returns, any in-flight Publish that already loaded the previous slice may
// deliver one final event; subsequent Publishes will not. Once the
// caller's read loop exits and no goroutine retains the channel, GC
// reclaims it.
func (b *Bus) Unsubscribe(ch <-chan Event) {
	b.subsMu.Lock()
	cur := b.subs.Load()
	idx := -1
	for i, c := range *cur {
		if c == ch {
			idx = i
			break
		}
	}
	if idx < 0 {
		b.subsMu.Unlock()
		return
	}
	next := make([]chan Event, 0, len(*cur)-1)
	next = append(next, (*cur)[:idx]...)
	next = append(next, (*cur)[idx+1:]...)
	b.subs.Store(&next)
	b.subsMu.Unlock()
}

// Publish records e in the ring buffer and attempts a non-blocking send to
// every registered subscriber. Events dropped due to a full channel increment
// the drop counter atomically.
//
// Publish stamps e.ID with a monotonically-increasing per-Bus sequence number
// BEFORE pushing into the ring or fanning out to subscribers, overwriting any
// value the caller provided. The ID stamped on the event in the ring buffer
// matches the ID delivered to subscribers, allowing consumers (e.g. the SSE
// handler) to de-duplicate events that appear in both a snapshot and the live
// channel.
//
// Publish takes a narrow lock for the ring-buffer push only; the fan-out loop
// reads the subscriber slice via a single atomic Load and runs lock-free, so
// concurrent Publishes do not serialize on the subscriber set.
func (b *Bus) Publish(e Event) {
	e.ID = atomic.AddUint64(&b.seq, 1)
	// Ring buffer push is a separate concern with its own narrow lock.
	b.ringMu.Lock()
	b.ring.Push(e)
	b.ringMu.Unlock()

	// Fan-out: load the immutable subscriber slice and iterate without any
	// lock. Each send is non-blocking; full channels increment drops.
	subs := b.subs.Load()
	for _, ch := range *subs {
		select {
		case ch <- e:
		default:
			atomic.AddInt64(&b.drops, 1)
		}
	}
}

// Snapshot returns up to n of the most-recent events in chronological
// (oldest-first) order.
func (b *Bus) Snapshot(n int) []Event {
	b.ringMu.Lock()
	defer b.ringMu.Unlock()
	return b.ring.Snapshot(n)
}

// Drops returns the total number of events that were dropped because a
// subscriber's channel buffer was full at the time of publish.
func (b *Bus) Drops() int64 {
	return atomic.LoadInt64(&b.drops)
}
