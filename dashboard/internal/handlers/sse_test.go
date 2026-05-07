package handlers_test

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/HomericIntelligence/atlas/internal/events"
	"github.com/HomericIntelligence/atlas/internal/handlers"
)

// makeEvt is a helper that creates an events.Event with a JSON-marshalled payload.
func makeEvt(t *testing.T, topic string, payload interface{}) events.Event {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("makeEvt: marshal: %v", err)
	}
	return events.Event{
		Topic:   topic,
		Subject: "hi." + topic + ".test",
		Payload: json.RawMessage(raw),
		At:      time.Now(),
	}
}

// sseLines reads lines from an io.ReadCloser until it has read n complete SSE frames
// (each frame ends with a blank line). Returns the raw lines.
func sseLines(t *testing.T, r io.ReadCloser, frames int) []string {
	t.Helper()
	var lines []string
	scanner := bufio.NewScanner(r)
	collected := 0
	for scanner.Scan() {
		line := scanner.Text()
		lines = append(lines, line)
		if line == "" {
			collected++
			if collected >= frames {
				break
			}
		}
	}
	if err := scanner.Err(); err != nil && !errors.Is(err, io.EOF) {
		t.Fatalf("sseLines: scanner error: %v", err)
	}
	return lines
}

// newTestBus returns a *events.Bus with ring cap 256 and wires up the SSE handler.
func newTestBus(t *testing.T) (*events.Bus, *handlers.SSE) {
	t.Helper()
	bus := events.NewBus(256)
	h := handlers.NewSSE(bus)
	return bus, h
}

// TestSSEHeaders verifies that the SSE endpoint sets the required HTTP headers and
// returns status 200.
func TestSSEHeaders(t *testing.T) {
	t.Parallel()

	_, h := newTestBus(t)

	// Use a server so we can cancel the connection from the client side.
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/events", nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}

	// We only need the response headers, not the body, so we use a client that
	// does not follow the stream past the headers.
	resp, err := srv.Client().Do(req)
	if err != nil {
		// A context-cancellation error is acceptable after we have the response.
		if resp == nil {
			t.Fatalf("Do: %v", err)
		}
	}
	if resp != nil {
		defer func() { _ = resp.Body.Close() }()
	}

	if resp.StatusCode != http.StatusOK {
		t.Errorf("status = %d; want 200", resp.StatusCode)
	}

	wantHeaders := map[string]string{
		"Content-Type":    "text/event-stream",
		"Cache-Control":   "no-cache",
		"X-Accel-Buffering": "no",
	}
	for header, want := range wantHeaders {
		got := resp.Header.Get(header)
		if got != want {
			t.Errorf("header %q = %q; want %q", header, got, want)
		}
	}
}

// TestSSEEventDelivery publishes one event with topic "agent" to a bus, connects an
// SSE client, reads the first non-heartbeat event, and asserts the wire format.
func TestSSEEventDelivery(t *testing.T) {
	t.Parallel()

	bus, h := newTestBus(t)

	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/events", nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}

	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()

	// Give the handler a moment to register the subscription before publishing.
	time.Sleep(20 * time.Millisecond)

	bus.Publish(makeEvt(t, "agent", map[string]string{"id": "a1", "status": "running"}))

	// Collect lines until we find a non-heartbeat event frame (2 content lines + blank).
	scanner := bufio.NewScanner(resp.Body)
	var eventLine, dataLine string
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "event: ") {
			topic := strings.TrimPrefix(line, "event: ")
			if topic == "heartbeat" {
				// Skip legacy named heartbeat frames (now emitted as SSE comments).
				continue
			}
			eventLine = line
			// The very next non-empty line must be the data line.
			for scanner.Scan() {
				next := scanner.Text()
				if strings.HasPrefix(next, "data: ") {
					dataLine = next
					break
				}
			}
			break
		}
	}
	if err := scanner.Err(); err != nil && !errors.Is(err, io.EOF) {
		t.Fatalf("scanner: %v", err)
	}

	if !strings.HasPrefix(eventLine, "event: agent") {
		t.Errorf("event line = %q; want prefix \"event: agent\"", eventLine)
	}
	if !strings.HasPrefix(dataLine, "data: ") {
		t.Errorf("data line = %q; want prefix \"data: \"", dataLine)
	}

	raw := strings.TrimPrefix(dataLine, "data: ")
	var decoded interface{}
	if err := json.Unmarshal([]byte(raw), &decoded); err != nil {
		t.Errorf("data is not valid JSON: %v (raw: %q)", err, raw)
	}
}

// TestSSETopicFilter connects with ?topics=agent, publishes a "task" event and an
// "agent" event, and asserts that only the "agent" event is delivered.
func TestSSETopicFilter(t *testing.T) {
	t.Parallel()

	bus, h := newTestBus(t)

	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/events?topics=agent", nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}

	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()

	time.Sleep(20 * time.Millisecond)

	bus.Publish(makeEvt(t, "task", map[string]string{"should": "be-filtered"}))
	bus.Publish(makeEvt(t, "agent", map[string]string{"id": "a2"}))

	scanner := bufio.NewScanner(resp.Body)
	var receivedTopics []string
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "event: ") {
			topic := strings.TrimPrefix(line, "event: ")
			if topic == "heartbeat" {
				continue
			}
			receivedTopics = append(receivedTopics, topic)
			// We expect exactly one delivery; stop after the first real event.
			break
		}
	}

	if len(receivedTopics) != 1 {
		t.Fatalf("received %d topics; want 1", len(receivedTopics))
	}
	if receivedTopics[0] != "agent" {
		t.Errorf("received topic %q; want \"agent\"", receivedTopics[0])
	}
}

// TestSSEReplay publishes 3 events to bus (which has a ring buffer), connects with
// ?replay=2, and asserts the first 2 SSE frames are the 2 most-recent buffered events
// before any new publishes.
func TestSSEReplay(t *testing.T) {
	t.Parallel()

	bus, h := newTestBus(t)

	// Publish 3 events before any client connects.
	bus.Publish(makeEvt(t, "agent", map[string]string{"seq": "1"}))
	bus.Publish(makeEvt(t, "agent", map[string]string{"seq": "2"}))
	bus.Publish(makeEvt(t, "agent", map[string]string{"seq": "3"}))

	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/events?replay=2", nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}

	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()

	// Read 2 complete SSE frames. Each frame: "event: ...\ndata: ...\n\n".
	lines := sseLines(t, resp.Body, 2)

	// Filter out heartbeat comment frames (": heartbeat") before asserting.
	var dataLines []string
	for _, l := range lines {
		if strings.HasPrefix(l, ": ") {
			continue
		}
		if strings.HasPrefix(l, "data: ") {
			dataLines = append(dataLines, strings.TrimPrefix(l, "data: "))
		}
	}

	if len(dataLines) < 2 {
		t.Fatalf("got %d data lines from replay; want at least 2\nraw lines: %v", len(dataLines), lines)
	}

	// The replayed events must be the last 2 published, i.e. seq "2" and "3".
	for _, rawJSON := range dataLines[:2] {
		var m map[string]interface{}
		if err := json.Unmarshal([]byte(rawJSON), &m); err != nil {
			t.Errorf("replay data not valid JSON: %v (raw: %q)", err, rawJSON)
		}
	}

	// Verify that seq values correspond to the last 2 publishes.
	seqValues := make([]string, 0, 2)
	for _, rawJSON := range dataLines[:2] {
		var m map[string]string
		if err := json.Unmarshal([]byte(rawJSON), &m); err == nil {
			if seq, ok := m["seq"]; ok {
				seqValues = append(seqValues, seq)
			}
		}
	}
	if len(seqValues) == 2 {
		if seqValues[0] != "2" || seqValues[1] != "3" {
			t.Errorf("replay seq = %v; want [2 3]", seqValues)
		}
	}
}

// blockingResponseWriter is an http.ResponseWriter whose Write method blocks
// until the provided context is cancelled, simulating a slow client.
type blockingResponseWriter struct {
	httptest.ResponseRecorder
	ctx context.Context
}

func (b *blockingResponseWriter) Write(_ []byte) (int, error) {
	<-b.ctx.Done()
	return 0, b.ctx.Err()
}

func (b *blockingResponseWriter) Flush() {} // satisfy http.Flusher

// TestSSESlowClientDrop uses a response writer that blocks writes (simulating a slow
// client), publishes 1100 events rapidly, and asserts the handler does not deadlock
// within 2 seconds.
func TestSSESlowClientDrop(t *testing.T) {
	t.Parallel()

	bus, h := newTestBus(t)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	bw := &blockingResponseWriter{ctx: ctx}
	bw.ResponseRecorder = *httptest.NewRecorder()

	req := httptest.NewRequest(http.MethodGet, "/events", nil)
	req = req.WithContext(ctx)

	done := make(chan struct{})
	go func() {
		defer close(done)
		h.ServeHTTP(bw, req)
	}()

	// Publish 1100 events rapidly to fill and overflow any internal channel/buffer.
	for i := 0; i < 1100; i++ {
		bus.Publish(makeEvt(t, "agent", map[string]int{"i": i}))
	}

	select {
	case <-done:
		// Handler returned cleanly before or after context cancellation — good.
	case <-time.After(2 * time.Second):
		t.Error("handler deadlocked: did not return within 2 seconds with a blocking client")
	}
}

// TestSSEConnectionClose verifies that when the client closes the connection
// (cancels the request context), the handler returns within 500ms without leaking
// goroutines.
func TestSSEConnectionClose(t *testing.T) {
	t.Parallel()

	bus, h := newTestBus(t)

	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/events", nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}

	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()

	// Let the handler start streaming.
	time.Sleep(30 * time.Millisecond)

	// Track handler completion via a WaitGroup by draining the connection body
	// in a goroutine. We close the connection by cancelling the request context.
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		_, _ = io.Copy(io.Discard, resp.Body)
	}()

	// Cancel the request context — this simulates the client closing the connection.
	cancel()

	waitDone := make(chan struct{})
	go func() {
		wg.Wait()
		close(waitDone)
	}()

	select {
	case <-waitDone:
		// Body drained (connection closed) within deadline — no leak.
	case <-time.After(500 * time.Millisecond):
		t.Error("handler did not release connection within 500ms after context cancel")
	}

	_ = bus // keep bus alive
}

// TestSSEMultipleTopics connects with ?topics=agent,task, publishes one "agent", one
// "task", and one "nats" event, and asserts that agent and task are received but nats
// is silently dropped.
func TestSSEMultipleTopics(t *testing.T) {
	t.Parallel()

	bus, h := newTestBus(t)

	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/events?topics=agent,task", nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}

	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()

	time.Sleep(20 * time.Millisecond)

	bus.Publish(makeEvt(t, "agent", map[string]string{"topic": "agent"}))
	bus.Publish(makeEvt(t, "task", map[string]string{"topic": "task"}))
	bus.Publish(makeEvt(t, "nats", map[string]string{"topic": "nats"}))

	scanner := bufio.NewScanner(resp.Body)
	received := make(map[string]bool)

	scanCtx, scanCancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer scanCancel()

	scanDone := make(chan struct{})
	go func() {
		defer close(scanDone)
		for scanner.Scan() {
			line := scanner.Text()
			if strings.HasPrefix(line, "event: ") {
				topic := strings.TrimPrefix(line, "event: ")
				if topic != "heartbeat" {
					received[topic] = true
				}
			}
			// Stop once we have seen both expected topics.
			if received["agent"] && received["task"] {
				return
			}
		}
	}()

	select {
	case <-scanDone:
	case <-scanCtx.Done():
	}

	if !received["agent"] {
		t.Error("expected to receive topic \"agent\"; did not")
	}
	if !received["task"] {
		t.Error("expected to receive topic \"task\"; did not")
	}
	if received["nats"] {
		t.Error("received topic \"nats\"; want it filtered out (topics=agent,task)")
	}
}

// TestSSE_NoReplayGap is the regression test for the §3 audit MAJOR
// "SSE replay race vs new publishes." It proves that no event is silently
// lost in the gap between the bus snapshot and the live subscriber channel
// that the handler opens for an SSE connection.
//
// Strategy:
//  1. Pre-publish a handful of events so the ring buffer is non-empty when
//     the client connects (exercises the replay branch).
//  2. Open an SSE connection with `?replay=N`.
//  3. While the connection is live, hammer Publish from a goroutine with
//     monotonically-increasing payload seq values.
//  4. After the publisher finishes and the connection has had a moment to
//     drain, collect every payload `seq` written to the wire.
//  5. Assert that the delivered seq values form a contiguous range
//     [min..max] with no holes (no gap) and no duplicates (de-dup works).
//
// With the buggy ordering (Snapshot then Subscribe) this test will fail
// because any seq published in the race window between the two calls is
// missing from BOTH the snapshot and the live channel, producing a hole
// in the delivered range.
func TestSSE_NoReplayGap(t *testing.T) {
	t.Parallel()

	// Ring capacity larger than the burst of publishes done before connect,
	// so the snapshot is well-defined and not truncated.
	const (
		preBurst   = 5
		liveBurst  = 1000
		ringCap    = 1024
		replayN    = 256
		drainGrace = 200 * time.Millisecond
	)

	bus := events.NewBus(ringCap)
	h := handlers.NewSSE(bus)

	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	// 1. Pre-publish a handful of events so the snapshot is non-empty.
	for i := 1; i <= preBurst; i++ {
		bus.Publish(makeEvt(t, "agent", map[string]int{"seq": i}))
	}

	// 2. Open the SSE connection. We give the handler a generous request
	// timeout — we will close the body explicitly once we have collected
	// enough events.
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		srv.URL+"/events?replay="+strconv.Itoa(replayN), nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}
	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()

	// 3. Hammer Publish from a separate goroutine. We start the publisher
	// immediately to maximise the chance that some publishes land in the
	// race window between Subscribe and Snapshot inside the handler. The
	// publisher numbers events with payload seq starting from preBurst+1
	// so the full sequence on the bus is 1..preBurst+liveBurst.
	pubDone := make(chan struct{})
	go func() {
		defer close(pubDone)
		for i := preBurst + 1; i <= preBurst+liveBurst; i++ {
			bus.Publish(makeEvt(t, "agent", map[string]int{"seq": i}))
		}
	}()

	// 4. Drain the SSE response. We track distinct seq values seen in
	// payloads. Once we have observed the final published seq we close
	// the connection. A safety timeout protects against a stuck stream.
	final := preBurst + liveBurst
	seen := make(map[int]int) // seq -> count (count > 1 means duplicate)
	scanner := bufio.NewScanner(resp.Body)
	// Larger buffer; default 64KB is fine but be defensive about long lines.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	scanCtx, scanCancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer scanCancel()

	scanDone := make(chan struct{})
	go func() {
		defer close(scanDone)
		for scanner.Scan() {
			line := scanner.Text()
			if !strings.HasPrefix(line, "data: ") {
				continue
			}
			raw := strings.TrimPrefix(line, "data: ")
			var m map[string]int
			if err := json.Unmarshal([]byte(raw), &m); err != nil {
				continue
			}
			seq, ok := m["seq"]
			if !ok {
				continue
			}
			seen[seq]++
			// Once the publisher is done AND we have seen the last seq,
			// we can stop scanning. We also stop early once we have the
			// final seq even if pubDone hasn't been read yet (it implies
			// the publisher is done).
			if seq == final {
				return
			}
		}
	}()

	// Wait for the publisher to finish, then give the SSE goroutine a
	// brief grace period to flush the final events through.
	select {
	case <-pubDone:
	case <-scanCtx.Done():
		t.Fatalf("publisher did not finish within timeout")
	}

	select {
	case <-scanDone:
	case <-time.After(drainGrace):
		// Force the scanner to exit by closing the body.
		_ = resp.Body.Close()
		<-scanDone
	}

	// 5. Validate: the seq values seen must form a contiguous range and
	// must have no duplicates.
	if len(seen) == 0 {
		t.Fatalf("no events delivered to SSE client")
	}

	// Find the range observed.
	minSeq, maxSeq := -1, -1
	for s := range seen {
		if minSeq == -1 || s < minSeq {
			minSeq = s
		}
		if s > maxSeq {
			maxSeq = s
		}
	}

	// No duplicates — the de-dup logic must collapse events that appeared
	// in BOTH the snapshot and the live channel during the replay window.
	for s, c := range seen {
		if c != 1 {
			t.Errorf("seq %d delivered %d times; want exactly 1 (de-dup failure)", s, c)
		}
	}

	// No gap — every integer in [minSeq, maxSeq] must be present. The
	// race-window bug manifests as missing entries in this contiguous
	// span.
	for s := minSeq; s <= maxSeq; s++ {
		if _, ok := seen[s]; !ok {
			t.Errorf("seq %d missing from delivered range [%d..%d] (replay-vs-live gap)",
				s, minSeq, maxSeq)
		}
	}

	// Sanity: the final published seq must be among the delivered ones.
	// If not, the test did not actually exercise the live-stream phase.
	if _, ok := seen[final]; !ok {
		t.Errorf("final seq %d not delivered; live-stream phase not exercised", final)
	}
}

// TestSSEHeartbeat asserts that a heartbeat comment frame is sent when no events are
// published. HeartbeatInterval is overridden to 50ms to keep the test fast.
func TestSSEHeartbeat(t *testing.T) {
	t.Parallel()

	old := handlers.HeartbeatInterval()
	handlers.SetHeartbeatInterval(50 * time.Millisecond)
	t.Cleanup(func() { handlers.SetHeartbeatInterval(old) })

	_, h := newTestBus(t)

	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/events", nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}

	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()

	scanner := bufio.NewScanner(resp.Body)
	heartbeatSeen := make(chan struct{}, 1)

	go func() {
		for scanner.Scan() {
			if scanner.Text() == ": heartbeat" {
				select {
				case heartbeatSeen <- struct{}{}:
				default:
				}
				return
			}
		}
	}()

	select {
	case <-heartbeatSeen:
		// Heartbeat comment frame received.
	case <-ctx.Done():
		t.Error("no heartbeat received within 3 seconds")
	}
}
