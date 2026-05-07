package catalog

// ServiceDef describes a single backing service Atlas knows how to poll.
// It captures the wire-level coordinates (port, protocol) plus the path used
// for liveness checks and the optional UI path advertised on the dashboard.
type ServiceDef struct {
	Name       string
	Port       int
	HealthPath string
	UIPath     string
	Proto      string
}

// KnownServices is the canonical, hard-coded service catalog used when no
// dynamic discovery source is configured.
var KnownServices = []ServiceDef{
	{Name: "nats", Port: 4222, HealthPath: "/varz", Proto: "http"},
	{Name: "nats-mon", Port: 8222, HealthPath: "/varz", Proto: "http"},
	{Name: "agamemnon", Port: 8080, HealthPath: "/v1/health", Proto: "http"},
	{Name: "nestor", Port: 8081, HealthPath: "/v1/health", Proto: "http"},
	{Name: "hermes", Port: 8085, HealthPath: "/health", Proto: "http"},
	{Name: "prometheus", Port: 9090, HealthPath: "/-/healthy", Proto: "http"},
	{Name: "grafana", Port: 3000, HealthPath: "/api/health", Proto: "http"},
	{Name: "loki", Port: 3100, HealthPath: "/ready", Proto: "http"},
	{Name: "loki-push", Port: 3101, HealthPath: "/ready", Proto: "http"},
	{Name: "nomad", Port: 4646, HealthPath: "/v1/status/leader", Proto: "http"},
	{Name: "argus-exporter", Port: 9100, HealthPath: "/metrics", Proto: "http"},
	{Name: "atlas", Port: 3002, HealthPath: "/healthz", Proto: "http"},
}
