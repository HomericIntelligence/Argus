package config

import (
	"errors"
	"fmt"
	"log/slog"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	ListenAddr         string
	LogLevel           slog.Level
	NATSURL            string
	NATSMonURL         string
	NATSDashboardURL   string // ATLAS_NATS_DASHBOARD_URL, default ""
	NATSTopURL         string // ATLAS_NATS_TOP_URL, default ""
	AgamemnonURL       string
	NestorURL          string
	HermesURL          string
	PrometheusURL      string
	GrafanaURL         string
	LokiURL            string
	ExporterURL        string
	MnemosyneSkillsDir string
	TailscaleSource    string
	TailscaleAPIKey    string
	TailnetName        string
	TailscaleSocket    string
	AuthMode           string
	AuthUser           string
	AuthPass           string
	AuthBearerToken    string
	// PollAgamemnon is the interval between Agamemnon poller cycles. Sourced
	// from ATLAS_POLL_AGAMEMNON_MS (named with the "Ms" suffix in the env
	// var to communicate the unit; the field is a time.Duration so the
	// suffix is dropped on the Go side per staticcheck ST1011).
	PollAgamemnon      time.Duration

	// RateLimitPerMin is the per-IP request budget for every route except
	// the dedicated liveness probes. Sourced from ATLAS_RATE_LIMIT_PER_MIN
	// (default 30). Setting this to 0 disables rate limiting for non-livez
	// routes — provided as an escape hatch for operators who hit a false
	// positive; not recommended for production.
	RateLimitPerMin int

	// LivezRateLimitPerMin is the per-IP request budget for /livez and its
	// /healthz alias. Sourced from ATLAS_LIVEZ_RATE_LIMIT_PER_MIN (default
	// 240) — high enough that a 5-second k8s liveness probe (12 req/min)
	// with a sidecar that also probes (24 req/min) plus restart-policy
	// retries comfortably fits. Setting this to 0 disables the limit on
	// the liveness routes specifically.
	LivezRateLimitPerMin int
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// Load reads ATLAS_* environment variables into a Config. It does not validate
// the result — call Validate before use.
//
// Defaults of note:
//   - AuthMode defaults to "bearer" (fail-secure). An explicit ATLAS_AUTH_MODE=none
//     is required to disable auth, and Validate logs a warning when that happens.
//   - PollAgamemnon defaults to 2000ms (env: ATLAS_POLL_AGAMEMNON_MS).
func Load() *Config {
	logLevelStr := getenv("ATLAS_LOG_LEVEL", "info")
	var logLevel slog.Level
	if err := logLevel.UnmarshalText([]byte(logLevelStr)); err != nil {
		logLevel = slog.LevelInfo
	}

	pollMs, err := strconv.Atoi(getenv("ATLAS_POLL_AGAMEMNON_MS", "2000"))
	if err != nil || pollMs <= 0 {
		pollMs = 2000
	}

	// Rate-limit budgets. Negative or non-numeric values fall back to the
	// default; an explicit 0 means "disabled" and is preserved (escape hatch).
	rate, err := strconv.Atoi(getenv("ATLAS_RATE_LIMIT_PER_MIN", "30"))
	if err != nil || rate < 0 {
		rate = 30
	}
	livezRate, err := strconv.Atoi(getenv("ATLAS_LIVEZ_RATE_LIMIT_PER_MIN", "240"))
	if err != nil || livezRate < 0 {
		livezRate = 240
	}

	return &Config{
		ListenAddr:         getenv("ATLAS_LISTEN_ADDR", ":3002"),
		LogLevel:           logLevel,
		NATSURL:            getenv("ATLAS_NATS_URL", "nats://nats:4222"),
		NATSMonURL:         getenv("ATLAS_NATS_MON_URL", "http://nats:8222"),
		NATSDashboardURL:   getenv("ATLAS_NATS_DASHBOARD_URL", ""),
		NATSTopURL:         getenv("ATLAS_NATS_TOP_URL", ""),
		AgamemnonURL:       getenv("ATLAS_AGAMEMNON_URL", "http://agamemnon:8080"),
		NestorURL:          getenv("ATLAS_NESTOR_URL", "http://nestor:8081"),
		HermesURL:          getenv("ATLAS_HERMES_URL", "http://hermes:8080"),
		PrometheusURL:      getenv("ATLAS_PROMETHEUS_URL", "http://prometheus:9090"),
		GrafanaURL:         getenv("ATLAS_GRAFANA_URL", "http://grafana:3000"),
		LokiURL:            getenv("ATLAS_LOKI_URL", "http://loki:3100"),
		ExporterURL:        getenv("ATLAS_EXPORTER_URL", "http://argus-exporter:9100"),
		MnemosyneSkillsDir: getenv("ATLAS_MNEMOSYNE_SKILLS_DIR", "/mnt/mnemosyne/skills"),
		TailscaleSource:    getenv("ATLAS_TAILSCALE_SOURCE", "static"),
		TailscaleAPIKey:    getenv("ATLAS_TAILSCALE_API_KEY", ""),
		TailnetName:        getenv("ATLAS_TAILNET_NAME", ""),
		TailscaleSocket:    getenv("ATLAS_TAILSCALE_SOCKET", "/var/run/tailscale/tailscaled.sock"),
		AuthMode:           getenv("ATLAS_AUTH_MODE", "bearer"),
		AuthUser:           getenv("ATLAS_AUTH_USER", ""),
		AuthPass:           getenv("ATLAS_AUTH_PASS", ""),
		AuthBearerToken:    getenv("ATLAS_AUTH_BEARER_TOKEN", ""),
		PollAgamemnon:        time.Duration(pollMs) * time.Millisecond,
		RateLimitPerMin:      rate,
		LivezRateLimitPerMin: livezRate,
	}
}

// Validate checks for misconfiguration that should prevent startup. It returns
// a non-nil error for fail-stop conditions (auth mode requires a credential but
// none is set; an iframe-target URL is malformed). It logs warnings via the
// passed logger for soft issues like AuthMode=none.
//
// Iframe-target URLs (Grafana, Loki, NATS dashboard) are interpolated into the
// CSP frame-src directive in server/middleware.go. Validating them here at
// startup guarantees we never inject CSP-breaking strings (semicolons,
// whitespace, unsupported schemes) into response headers.
func (c *Config) Validate(logger *slog.Logger) error {
	var errs []error

	// Normalize the auth mode in place so the runtime middleware (which compares
	// against the typed constants in server.AuthMode exactly) always sees a
	// canonical lowercase value. Without this, ATLAS_AUTH_MODE=Bearer would
	// pass Validate (because the local switch lowercased a copy) but the
	// middleware would fall through its switch and previously fail-open. See
	// also server.Middleware which now rejects unknown modes outright.
	c.AuthMode = strings.ToLower(strings.TrimSpace(c.AuthMode))

	switch c.AuthMode {
	case "bearer":
		if c.AuthBearerToken == "" {
			errs = append(errs, errors.New("ATLAS_AUTH_MODE=bearer requires ATLAS_AUTH_BEARER_TOKEN to be set"))
		}
	case "basic":
		if c.AuthUser == "" || c.AuthPass == "" {
			errs = append(errs, errors.New("ATLAS_AUTH_MODE=basic requires ATLAS_AUTH_USER and ATLAS_AUTH_PASS to be set"))
		}
	case "none":
		logger.Warn("auth disabled — set ATLAS_AUTH_MODE=bearer for production", "auth_mode", "none")
	default:
		errs = append(errs, fmt.Errorf("ATLAS_AUTH_MODE=%q is not one of: none, basic, bearer", c.AuthMode))
	}

	for _, f := range []struct {
		name  string
		value string
	}{
		{"ATLAS_GRAFANA_URL", c.GrafanaURL},
		{"ATLAS_LOKI_URL", c.LokiURL},
		{"ATLAS_NATS_DASHBOARD_URL", c.NATSDashboardURL},
	} {
		if f.value == "" {
			continue
		}
		if err := validateIframeURL(f.value); err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", f.name, err))
		}
	}

	return errors.Join(errs...)
}

// validateIframeURL rejects values that would break the CSP frame-src directive
// or be unsafe to render in an iframe src attribute. We require a well-formed
// http(s) URL with a host and no characters that could split CSP directives.
func validateIframeURL(raw string) error {
	if strings.ContainsAny(raw, " \t\r\n;'\"") {
		return fmt.Errorf("contains disallowed character (whitespace, semicolon, quote)")
	}
	u, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("not a valid URL: %w", err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("scheme %q not allowed (must be http or https)", u.Scheme)
	}
	if u.Host == "" {
		return fmt.Errorf("missing host")
	}
	return nil
}
