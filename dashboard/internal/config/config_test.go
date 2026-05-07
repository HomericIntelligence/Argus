package config

import (
	"io"
	"log/slog"
	"strings"
	"testing"
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
