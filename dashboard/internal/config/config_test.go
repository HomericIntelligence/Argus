package config

import (
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"
)

func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func TestValidate_BearerWithoutToken(t *testing.T) {
	c := &Config{AuthMode: "bearer", AuthBearerToken: ""}
	err := c.Validate(discardLogger())
	if err == nil || !strings.Contains(err.Error(), "ATLAS_AUTH_BEARER_TOKEN") {
		t.Fatalf("want error mentioning bearer token, got %v", err)
	}
}

func TestValidate_BearerWithToken(t *testing.T) {
	c := &Config{AuthMode: "bearer", AuthBearerToken: "secret"}
	if err := c.Validate(discardLogger()); err != nil {
		t.Fatalf("want nil error, got %v", err)
	}
}

func TestValidate_BasicMissingCreds(t *testing.T) {
	for _, tc := range []struct {
		name string
		c    *Config
	}{
		{"no user", &Config{AuthMode: "basic", AuthPass: "p"}},
		{"no pass", &Config{AuthMode: "basic", AuthUser: "u"}},
		{"neither", &Config{AuthMode: "basic"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.c.Validate(discardLogger())
			if err == nil || !strings.Contains(err.Error(), "ATLAS_AUTH_USER") {
				t.Fatalf("want validation error, got %v", err)
			}
		})
	}
}

func TestValidate_NoneIsAllowedButWarns(t *testing.T) {
	c := &Config{AuthMode: "none"}
	if err := c.Validate(discardLogger()); err != nil {
		t.Fatalf("AuthMode=none must be allowed (with warning): %v", err)
	}
}

func TestValidate_UnknownAuthMode(t *testing.T) {
	c := &Config{AuthMode: "magic"}
	err := c.Validate(discardLogger())
	if err == nil || !strings.Contains(err.Error(), "magic") {
		t.Fatalf("want error naming the bad mode, got %v", err)
	}
}

func TestValidate_RejectsCSPInjectingURLs(t *testing.T) {
	for _, tc := range []struct {
		name  string
		field func(*Config, string)
		bad   string
	}{
		{"grafana semicolon", func(c *Config, v string) { c.GrafanaURL = v }, "http://grafana:3000; script-src evil"},
		{"loki whitespace", func(c *Config, v string) { c.LokiURL = v }, "http://loki:3100 evil"},
		{"nats dashboard quote", func(c *Config, v string) { c.NATSDashboardURL = v }, "http://x:8080\"onload=evil"},
		{"javascript scheme", func(c *Config, v string) { c.GrafanaURL = v }, "javascript:alert(1)"},
		{"missing host", func(c *Config, v string) { c.GrafanaURL = v }, "http://"},
		{"unparsable", func(c *Config, v string) { c.GrafanaURL = v }, "://"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := &Config{AuthMode: "none"}
			tc.field(c, tc.bad)
			err := c.Validate(discardLogger())
			if err == nil {
				t.Fatalf("want validation error for %q, got nil", tc.bad)
			}
		})
	}
}

func TestValidate_AcceptsCleanURLs(t *testing.T) {
	c := &Config{
		AuthMode:         "none",
		GrafanaURL:       "http://grafana:3000",
		LokiURL:          "https://loki.example.com:3100",
		NATSDashboardURL: "http://nats-dashboard.local",
	}
	if err := c.Validate(discardLogger()); err != nil {
		t.Fatalf("clean URLs must validate: %v", err)
	}
}

func TestValidate_EmptyOptionalURL(t *testing.T) {
	c := &Config{
		AuthMode:         "none",
		GrafanaURL:       "http://grafana:3000",
		LokiURL:          "http://loki:3100",
		NATSDashboardURL: "", // empty optional URL is fine
	}
	if err := c.Validate(discardLogger()); err != nil {
		t.Fatalf("empty optional URL must validate: %v", err)
	}
}

// TestValidate_NormalizesAuthMode is a regression test for a fail-open auth
// bypass that shipped in v0.2.0: Validate's switch lowercased a *local* copy
// of c.AuthMode, leaving c.AuthMode itself untouched. The middleware then
// compared the un-normalized field against typed constants ("bearer",
// "basic", "none") exactly, hit its default branch on anything mixed-case,
// and previously fell through allowing all requests. The fix in Validate is
// to assign the normalized value back to c.AuthMode so every downstream
// consumer (including the middleware) sees a canonical value.
//
// Defense-in-depth: server.Middleware now also fails closed on unknown modes
// (see TestMiddleware_UnknownModeFailsClosed). Either fix alone closes the
// bypass; both together make the bug structurally hard to reintroduce.
// TestLoadDuration_FallsBackToDefault exercises the parse-or-fallback contract
// for every ATLAS_* time.Duration env var promoted in the Bucket-4 §4 audit.
// Operators will mistype; we'd rather degrade silently to the documented
// default than refuse to start.
func TestLoadDuration_FallsBackToDefault(t *testing.T) {
	cases := []struct {
		key  string
		def  time.Duration
		read func(*Config) time.Duration
	}{
		{"ATLAS_HTTP_READ_TIMEOUT", 10 * time.Second, func(c *Config) time.Duration { return c.HTTPReadTimeout }},
		{"ATLAS_HTTP_IDLE_TIMEOUT", 60 * time.Second, func(c *Config) time.Duration { return c.HTTPIdleTimeout }},
		{"ATLAS_UPSTREAM_TIMEOUT", 3 * time.Second, func(c *Config) time.Duration { return c.UpstreamTimeout }},
		{"ATLAS_TAILSCALE_API_TIMEOUT", 10 * time.Second, func(c *Config) time.Duration { return c.TailscaleAPITimeout }},
		{"ATLAS_TAILSCALE_CLI_TIMEOUT", 5 * time.Second, func(c *Config) time.Duration { return c.TailscaleCLITimeout }},
		{"ATLAS_PROBE_INTERVAL", 10 * time.Second, func(c *Config) time.Duration { return c.ProbeInterval }},
		{"ATLAS_NATS_POLL_INTERVAL", 5 * time.Second, func(c *Config) time.Duration { return c.NATSPollInterval }},
		{"ATLAS_TAILSCALE_REFRESH_INTERVAL", 30 * time.Second, func(c *Config) time.Duration { return c.TailscaleRefreshInterval }},
		{"ATLAS_SSE_HEARTBEAT_INTERVAL", 15 * time.Second, func(c *Config) time.Duration { return c.SSEHeartbeatInterval }},
		{"ATLAS_NATS_ACK_WAIT", 30 * time.Second, func(c *Config) time.Duration { return c.NATSAckWait }},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.key+"/positive_750ms", func(t *testing.T) {
			t.Setenv(tc.key, "750ms")
			c := Load()
			if got := tc.read(c); got != 750*time.Millisecond {
				t.Fatalf("%s=750ms parsed as %v; want 750ms", tc.key, got)
			}
		})
		t.Run(tc.key+"/garbage_falls_back", func(t *testing.T) {
			t.Setenv(tc.key, "not-a-duration")
			c := Load()
			if got := tc.read(c); got != tc.def {
				t.Fatalf("%s=garbage parsed as %v; want default %v", tc.key, got, tc.def)
			}
		})
		t.Run(tc.key+"/negative_falls_back", func(t *testing.T) {
			t.Setenv(tc.key, "-5s")
			c := Load()
			if got := tc.read(c); got != tc.def {
				t.Fatalf("%s=-5s parsed as %v; want default %v", tc.key, got, tc.def)
			}
		})
		t.Run(tc.key+"/zero_falls_back", func(t *testing.T) {
			t.Setenv(tc.key, "0s")
			c := Load()
			if got := tc.read(c); got != tc.def {
				t.Fatalf("%s=0s parsed as %v; want default %v", tc.key, got, tc.def)
			}
		})
	}
}

// TestLoadDuration_DefaultsWhenUnset asserts that with no env var set the
// documented default lands in the Config field.
func TestLoadDuration_DefaultsWhenUnset(t *testing.T) {
	c := Load()
	cases := []struct {
		name string
		got  time.Duration
		want time.Duration
	}{
		{"HTTPReadTimeout", c.HTTPReadTimeout, 10 * time.Second},
		{"HTTPIdleTimeout", c.HTTPIdleTimeout, 60 * time.Second},
		{"UpstreamTimeout", c.UpstreamTimeout, 3 * time.Second},
		{"TailscaleAPITimeout", c.TailscaleAPITimeout, 10 * time.Second},
		{"TailscaleCLITimeout", c.TailscaleCLITimeout, 5 * time.Second},
		{"ProbeInterval", c.ProbeInterval, 10 * time.Second},
		{"NATSPollInterval", c.NATSPollInterval, 5 * time.Second},
		{"TailscaleRefreshInterval", c.TailscaleRefreshInterval, 30 * time.Second},
		{"SSEHeartbeatInterval", c.SSEHeartbeatInterval, 15 * time.Second},
		{"NATSAckWait", c.NATSAckWait, 30 * time.Second},
	}
	for _, tc := range cases {
		if tc.got != tc.want {
			t.Errorf("%s default = %v; want %v", tc.name, tc.got, tc.want)
		}
	}
}

// TestLoadInt_FallsBackToDefault covers the int-typed promotions
// (ATLAS_SSE_SUBSCRIBER_BUFFER, ATLAS_BUS_RING_CAPACITY, ATLAS_NATS_MAX_ACK_PENDING).
func TestLoadInt_FallsBackToDefault(t *testing.T) {
	cases := []struct {
		key  string
		def  int
		read func(*Config) int
	}{
		{"ATLAS_SSE_SUBSCRIBER_BUFFER", 1000, func(c *Config) int { return c.SSESubscriberBuffer }},
		{"ATLAS_BUS_RING_CAPACITY", 256, func(c *Config) int { return c.BusRingCapacity }},
		{"ATLAS_NATS_MAX_ACK_PENDING", 1024, func(c *Config) int { return c.NATSMaxAckPending }},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.key+"/positive", func(t *testing.T) {
			t.Setenv(tc.key, "42")
			c := Load()
			if got := tc.read(c); got != 42 {
				t.Fatalf("%s=42 parsed as %d; want 42", tc.key, got)
			}
		})
		t.Run(tc.key+"/garbage_falls_back", func(t *testing.T) {
			t.Setenv(tc.key, "not-an-int")
			c := Load()
			if got := tc.read(c); got != tc.def {
				t.Fatalf("%s=garbage parsed as %d; want default %d", tc.key, got, tc.def)
			}
		})
		t.Run(tc.key+"/zero_falls_back", func(t *testing.T) {
			t.Setenv(tc.key, "0")
			c := Load()
			if got := tc.read(c); got != tc.def {
				t.Fatalf("%s=0 parsed as %d; want default %d", tc.key, got, tc.def)
			}
		})
		t.Run(tc.key+"/negative_falls_back", func(t *testing.T) {
			t.Setenv(tc.key, "-7")
			c := Load()
			if got := tc.read(c); got != tc.def {
				t.Fatalf("%s=-7 parsed as %d; want default %d", tc.key, got, tc.def)
			}
		})
	}
}

func TestLoadInt_DefaultsWhenUnset(t *testing.T) {
	c := Load()
	if c.SSESubscriberBuffer != 1000 {
		t.Errorf("SSESubscriberBuffer default = %d; want 1000", c.SSESubscriberBuffer)
	}
	if c.BusRingCapacity != 256 {
		t.Errorf("BusRingCapacity default = %d; want 256", c.BusRingCapacity)
	}
	if c.NATSMaxAckPending != 1024 {
		t.Errorf("NATSMaxAckPending default = %d; want 1024", c.NATSMaxAckPending)
	}
}

func TestValidate_NormalizesAuthMode(t *testing.T) {
	for _, tc := range []struct {
		name string
		in   string
		want string
	}{
		{"mixed case bearer", "Bearer", "bearer"},
		{"upper case bearer", "BEARER", "bearer"},
		{"silly case bearer", "BeArEr", "bearer"},
		{"surrounding whitespace", "  bearer\t", "bearer"},
		{"upper case none", "NONE", "none"},
		{"upper case basic", "BASIC", "basic"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := &Config{
				AuthMode:        tc.in,
				AuthBearerToken: "secret",
				AuthUser:        "u",
				AuthPass:        "p",
			}
			if err := c.Validate(discardLogger()); err != nil {
				t.Fatalf("Validate must accept normalized auth mode %q, got error: %v", tc.in, err)
			}
			if c.AuthMode != tc.want {
				t.Fatalf("Validate must normalize c.AuthMode to %q, got %q", tc.want, c.AuthMode)
			}
		})
	}
}
