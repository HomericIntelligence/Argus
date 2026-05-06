package server

import (
	"net/http"
	"runtime"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/HomericIntelligence/atlas/internal/version"
)

// pollSources are the pre-registered poller source labels for histograms.
// Keeping the list explicit guarantees every source appears in /metrics output
// even before the first poll, so rules/atlas-alerts.yml can match labels
// reliably from the moment Atlas starts.
var pollSources = []string{"agamemnon", "nestor", "hermes", "nats"}

// histogramBuckets are the upper bounds (in seconds) for
// atlas_poll_duration_seconds. The set covers sub-second polls (typical
// Agamemnon/Nestor) up to 5s near the 3s HTTP client timeout the pollers use.
var histogramBuckets = []float64{0.1, 0.5, 1, 2, 5}

// AtlasMetrics is the Prometheus metric set for the Atlas dashboard. The
// public method names match the legacy hand-rolled implementation so callers
// (pollers, SSE handler, NATS subscriber) need not change. The metric NAMES
// and label NAMES are also preserved so rules/atlas-alerts.yml continues to
// match without edits.
type AtlasMetrics struct {
	registry *prometheus.Registry

	buildInfo           prometheus.Gauge
	natsConnected       prometheus.Gauge
	sseConnectedClients prometheus.Gauge
	pollErrors          *prometheus.CounterVec
	sseDropped          *prometheus.CounterVec
	eventParseErrors    *prometheus.CounterVec
	natsMessages        *prometheus.CounterVec
	pollDuration        *prometheus.HistogramVec
}

// newAtlasMetrics constructs an AtlasMetrics with a fresh, isolated
// prometheus.Registry (no use of prometheus.DefaultRegisterer). Each Server
// instance therefore owns its own metrics: tests can construct multiple
// servers in parallel without "duplicate metrics collector registration"
// panics, which the legacy hand-rolled implementation also avoided.
func newAtlasMetrics() *AtlasMetrics {
	reg := prometheus.NewRegistry()

	m := &AtlasMetrics{
		registry: reg,

		buildInfo: prometheus.NewGauge(prometheus.GaugeOpts{
			Name:        "atlas_build_info",
			Help:        "Build information about the Atlas dashboard.",
			ConstLabels: prometheus.Labels{"version": version.Version, "goversion": runtime.Version()},
		}),
		natsConnected: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "atlas_nats_connected",
			Help: "1 if the NATS connection is healthy, 0 otherwise.",
		}),
		sseConnectedClients: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "atlas_sse_connected_clients",
			Help: "Number of currently connected SSE clients.",
		}),
		pollErrors: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "atlas_poll_errors_total",
			Help: "Total number of poller errors by source.",
		}, []string{"source"}),
		sseDropped: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "atlas_sse_dropped_total",
			Help: "Total number of SSE events dropped for slow subscribers.",
		}, []string{"subscriber"}),
		eventParseErrors: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "atlas_event_parse_errors_total",
			Help: "Total number of event parse errors by stream.",
		}, []string{"stream"}),
		natsMessages: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "atlas_nats_messages_processed_total",
			Help: "Total number of NATS messages processed by stream.",
		}, []string{"stream"}),
		pollDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "atlas_poll_duration_seconds",
			Help:    "Duration of poll requests in seconds.",
			Buckets: histogramBuckets,
		}, []string{"source"}),
	}

	reg.MustRegister(
		m.buildInfo,
		m.natsConnected,
		m.sseConnectedClients,
		m.pollErrors,
		m.sseDropped,
		m.eventParseErrors,
		m.natsMessages,
		m.pollDuration,
	)
	m.buildInfo.Set(1)

	// Pre-instantiate the per-source label combinations so they appear in
	// /metrics output with value 0 from the moment the server starts. This
	// keeps alert rules with hard-coded label sets (e.g.
	// {source="agamemnon"}) from going NoData while the pollers warm up.
	for _, src := range pollSources {
		m.pollErrors.WithLabelValues(src)
		m.pollDuration.WithLabelValues(src)
	}

	return m
}

// SetNATSConnected sets atlas_nats_connected (1 = connected, 0 = disconnected).
func (m *AtlasMetrics) SetNATSConnected(connected bool) {
	if connected {
		m.natsConnected.Set(1)
	} else {
		m.natsConnected.Set(0)
	}
}

// SetSSEConnectedClients updates atlas_sse_connected_clients to n.
func (m *AtlasMetrics) SetSSEConnectedClients(n int64) {
	m.sseConnectedClients.Set(float64(n))
}

// IncPollError increments atlas_poll_errors_total for the given source label.
func (m *AtlasMetrics) IncPollError(source string) {
	m.pollErrors.WithLabelValues(source).Inc()
}

// IncSSEDropped increments atlas_sse_dropped_total for the given subscriber label.
func (m *AtlasMetrics) IncSSEDropped(subscriber string) {
	m.sseDropped.WithLabelValues(subscriber).Inc()
}

// IncEventParseError increments atlas_event_parse_errors_total for the given stream label.
func (m *AtlasMetrics) IncEventParseError(stream string) {
	m.eventParseErrors.WithLabelValues(stream).Inc()
}

// IncNATSMessage increments atlas_nats_messages_processed_total for the given stream label.
func (m *AtlasMetrics) IncNATSMessage(stream string) {
	m.natsMessages.WithLabelValues(stream).Inc()
}

// ObservePollDuration records a poll duration (in seconds) into atlas_poll_duration_seconds.
func (m *AtlasMetrics) ObservePollDuration(source string, seconds float64) {
	m.pollDuration.WithLabelValues(source).Observe(seconds)
}

// Handler returns an http.Handler that serves the Prometheus text-format
// exposition for this metric set.
func (m *AtlasMetrics) Handler() http.HandlerFunc {
	h := promhttp.HandlerFor(m.registry, promhttp.HandlerOpts{
		Registry:      m.registry,
		EnableOpenMetrics: false, // keep legacy text/plain; version=0.0.4 content-type
	})
	return h.ServeHTTP
}
