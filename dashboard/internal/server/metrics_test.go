package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestMetricsHandler_StatusOK(t *testing.T) {
	m := newAtlasMetrics()
	handler := m.Handler()

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rr.Code)
	}
}

func TestMetricsHandler_ContentType(t *testing.T) {
	m := newAtlasMetrics()
	handler := m.Handler()

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	ct := rr.Header().Get("Content-Type")
	// The promhttp handler may append metadata params (e.g. ";escaping=underscores")
	// to the base Prometheus content-type. Both forms are valid and parse the
	// same way. Assert the prefix.
	const wantPrefix = "text/plain; version=0.0.4"
	if !strings.HasPrefix(ct, wantPrefix) {
		t.Errorf("Content-Type: got %q, want prefix %q", ct, wantPrefix)
	}
}

func TestMetricsHandler_ContainsBuildInfo(t *testing.T) {
	m := newAtlasMetrics()
	handler := m.Handler()

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	body := rr.Body.String()
	if !strings.Contains(body, "atlas_build_info") {
		t.Errorf("body does not contain atlas_build_info:\n%s", body)
	}
}

func TestMetricsHandler_ContainsNATSConnected(t *testing.T) {
	m := newAtlasMetrics()
	handler := m.Handler()

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	body := rr.Body.String()
	if !strings.Contains(body, "atlas_nats_connected") {
		t.Errorf("body does not contain atlas_nats_connected:\n%s", body)
	}
}

func TestMetricsHandler_NoRegistrationPanic_FirstInstance(t *testing.T) {
	// Calling newAtlasMetrics() must not panic.
	m := newAtlasMetrics()
	if m == nil {
		t.Fatal("newAtlasMetrics() returned nil")
	}
}

func TestMetricsHandler_NoRegistrationPanic_SecondInstance(t *testing.T) {
	// Calling newAtlasMetrics() a second time (e.g. in a separate test) must not panic.
	m := newAtlasMetrics()
	if m == nil {
		t.Fatal("newAtlasMetrics() returned nil")
	}
}

// TestMetricsHandler_AllPreregisteredSources verifies that all pre-registered
// poll sources appear in the histogram output.
func TestMetricsHandler_AllPreregisteredSources(t *testing.T) {
	m := newAtlasMetrics()
	handler := m.Handler()

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	body := rr.Body.String()
	for _, src := range pollSources {
		want := `source="` + src + `"`
		if !strings.Contains(body, want) {
			t.Errorf("body missing histogram for source %q", src)
		}
	}
}

// TestMetricsHandler_MetricsServerMethod verifies that MetricsHandler() on a
// Server delegates to the embedded metrics correctly.
func TestMetricsHandler_MetricsServerMethod(t *testing.T) {
	s := &Server{metrics: newAtlasMetrics()}
	handler := s.MetricsHandler()

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200 from MetricsHandler(), got %d", rr.Code)
	}
}
