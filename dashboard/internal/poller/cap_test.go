package poller

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// TestGetJSON_BoundedByMaxResponseBytes asserts that an upstream which
// streams more than maxResponseBytes does NOT cause Atlas to allocate
// unbounded memory. This is the regression for the §8 audit finding
// "no request-body size limit on JSON decoders" — without this cap a
// hostile or runaway Agamemnon/Nestor/NATS-mon endpoint could OOM Atlas
// (compose memory limit is 128 MiB) by streaming a multi-GB response.
//
// We swap maxResponseBytes down to a few hundred bytes so the test stays
// fast and deterministic; the production value (16 MiB) is enforced by
// the same code path.
func TestGetJSON_BoundedByMaxResponseBytes(t *testing.T) {
	prev := maxResponseBytes
	maxResponseBytes = 256
	t.Cleanup(func() { maxResponseBytes = prev })

	// Server streams a JSON array of stringified zeros that is much
	// larger than the cap (~10 KiB > 256 B).
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`[`))
		_, _ = w.Write([]byte(`"`))
		_, _ = w.Write([]byte(strings.Repeat("0", 10000)))
		_, _ = w.Write([]byte(`"]`))
	}))
	t.Cleanup(srv.Close)

	b := newBase("test-source", &http.Client{Timeout: 2 * time.Second})

	var dst []string
	err := b.getJSON(context.Background(), srv.URL, &dst)
	if err == nil {
		t.Fatalf("getJSON must error when upstream exceeds cap; got nil and dst=%v", dst)
	}
	if !strings.Contains(err.Error(), "decode") {
		t.Fatalf("error must come from the decode path; got: %v", err)
	}
	// The dst slice must NOT have been populated with the giant string.
	// (json.Decoder may have partially populated it on truncated input —
	// the safety property is that we did not allocate the entire 10KiB
	// payload, which is implicit in io.LimitReader returning EOF early.
	// We assert the surface property: the decoder errored out.)
}

// TestGetJSON_AcceptsPayloadsUnderCap is the positive case — a normal-sized
// JSON response decodes fine. Locks in that the LimitReader does not
// regress correct behaviour on realistic payloads.
func TestGetJSON_AcceptsPayloadsUnderCap(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`["alpha","beta","gamma"]`))
	}))
	t.Cleanup(srv.Close)

	b := newBase("test-source", &http.Client{Timeout: 2 * time.Second})

	var dst []string
	if err := b.getJSON(context.Background(), srv.URL, &dst); err != nil {
		t.Fatalf("getJSON on small payload must succeed; got %v", err)
	}
	if len(dst) != 3 || dst[0] != "alpha" {
		t.Fatalf("unexpected decoded value: %#v", dst)
	}
}
