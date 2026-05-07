package tailscale

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// maxResponseBytes caps how much we will read from any external Tailscale
// source (HTTP API or `tailscale status --json` subprocess) before giving
// up. Defends Atlas against an attacker-controlled or compromised
// Tailscale plane streaming a multi-GB response and exhausting memory
// (compose limit is 128 MiB).
//
// 16 MiB is roughly two orders of magnitude above any realistic tailnet
// device list (a 100-device tailnet API response is ~50 KiB) while
// staying well below the OOM threshold. Matches the equivalent cap in
// internal/poller for upstream JSON consistency.
//
// Declared as a var (not a const) so tests can lower it without staging
// a 16 MiB fake response. Production paths must NEVER mutate this.
var maxResponseBytes int64 = 16 << 20 // 16 MiB

// apiResponse mirrors the Tailscale v2 API response for device listing.
type apiResponse struct {
	Devices []apiDevice `json:"devices"`
}

type apiDevice struct {
	Hostname  string   `json:"hostname"`
	Addresses []string `json:"addresses"`
	Online    bool     `json:"online"`
	LastSeen  string   `json:"lastSeen"` // RFC3339
}

// APISource fetches devices from the Tailscale HTTP API.
// The HTTPClient field allows injection of a test server client; if nil, a
// default client with a 10-second timeout is used.
type APISource struct {
	APIKey     string
	Tailnet    string
	HTTPClient *http.Client
}

// Devices calls GET https://api.tailscale.com/api/v2/tailnet/{tailnet}/devices
// with Bearer authentication and returns the parsed device list.
func (a APISource) Devices(ctx context.Context) ([]Device, error) {
	client := a.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 10 * time.Second}
	}

	url := fmt.Sprintf("https://api.tailscale.com/api/v2/tailnet/%s/devices", a.Tailnet)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("tailscale api: build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+a.APIKey)

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("tailscale api: HTTP GET: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("tailscale api: unexpected status %d", resp.StatusCode)
	}

	var result apiResponse
	if err := json.NewDecoder(io.LimitReader(resp.Body, maxResponseBytes)).Decode(&result); err != nil {
		return nil, fmt.Errorf("tailscale api: parse JSON (cap %d bytes): %w", maxResponseBytes, err)
	}

	devices := make([]Device, 0, len(result.Devices))
	for _, d := range result.Devices {
		dev := Device{
			Hostname: d.Hostname,
			Online:   d.Online,
		}
		if len(d.Addresses) > 0 {
			dev.TailscaleIP = d.Addresses[0]
		}
		if d.LastSeen != "" {
			if t, err := time.Parse(time.RFC3339, d.LastSeen); err == nil {
				dev.LastSeen = t
			}
		}
		devices = append(devices, dev)
	}
	return devices, nil
}
