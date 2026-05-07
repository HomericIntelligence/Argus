package tailscale

import (
	"context"
	"log/slog"

	"github.com/HomericIntelligence/atlas/internal/config"
)

// AutoSource tries sources in priority order: CLI -> API -> Static.
// It returns the result of the first source that succeeds.
// The static source is used as the final fallback and never returns an error.
//
// Cfg may be nil; in that case the API source is never attempted.
type AutoSource struct {
	Cfg *config.Config
}

// Devices attempts CLI, then API (if configured), then static in order.
func (a AutoSource) Devices(ctx context.Context) ([]Device, error) {
	// 1. Try CLI source.
	cli := CLISource{}
	if a.Cfg != nil {
		cli.Timeout = a.Cfg.TailscaleCLITimeout
	}
	if devices, err := cli.Devices(ctx); err == nil {
		return devices, nil
	} else {
		slog.Debug("tailscale auto: CLI source failed, trying next", "err", err)
	}

	// 2. Try API source if credentials are available.
	if a.Cfg != nil && a.Cfg.TailscaleAPIKey != "" && a.Cfg.TailnetName != "" {
		api := APISource{
			APIKey:  a.Cfg.TailscaleAPIKey,
			Tailnet: a.Cfg.TailnetName,
			Timeout: a.Cfg.TailscaleAPITimeout,
		}
		if devices, err := api.Devices(ctx); err == nil {
			return devices, nil
		} else {
			slog.Debug("tailscale auto: API source failed, falling back to static", "err", err)
		}
	}

	// 3. Fall back to static -- never errors.
	return StaticSource{}.Devices(ctx)
}
