package tailscale

import (
	"github.com/HomericIntelligence/atlas/internal/config"
)

// NewSource returns the Source implementation selected by cfg.TailscaleSource.
//
// Supported values:
//   - "cli"    → CLISource (runs `tailscale status --json`)
//   - "api"    → APISource (calls the Tailscale HTTP API)
//   - "static" → StaticSource (returns devices from env vars)
//   - "auto"   → AutoSource (CLI → API → static fallback chain)
//   - ""       → StaticSource (default)
//
// Any unrecognised value falls back to StaticSource.
func NewSource(cfg *config.Config) Source {
	switch cfg.TailscaleSource {
	case "cli":
		return CLISource{Timeout: cfg.TailscaleCLITimeout}
	case "api":
		return APISource{
			APIKey:  cfg.TailscaleAPIKey,
			Tailnet: cfg.TailnetName,
			Timeout: cfg.TailscaleAPITimeout,
		}
	case "static":
		return StaticSource{}
	case "auto":
		return AutoSource{Cfg: cfg}
	default:
		return StaticSource{}
	}
}
