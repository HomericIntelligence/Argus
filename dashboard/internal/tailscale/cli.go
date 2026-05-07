package tailscale

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os/exec"
	"time"
)

// cliStatus mirrors the JSON output of `tailscale status --json`.
type cliStatus struct {
	Self  cliPeer            `json:"Self"`
	Peers map[string]cliPeer `json:"Peer"`
}

type cliPeer struct {
	HostName     string   `json:"HostName"`
	TailscaleIPs []string `json:"TailscaleIPs"`
	Online       bool     `json:"Online"`
	LastSeen     string   `json:"LastSeen"` // RFC3339
}

// CLISource invokes `tailscale status --json` to enumerate devices.
// If the binary is not found or the socket is not present, Devices returns
// an error immediately. Timeout is the subprocess wall-clock cap (default 5s
// when zero), sourced from ATLAS_TAILSCALE_CLI_TIMEOUT.
type CLISource struct {
	Timeout time.Duration
}

// Devices runs `tailscale status --json` with the configured subprocess
// timeout (default 5s when c.Timeout is zero or negative).
func (c CLISource) Devices(ctx context.Context) ([]Device, error) {
	timeout := c.Timeout
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, "tailscale", "status", "--json")
	// We deliberately do NOT use cmd.Output() — it reads the subprocess
	// stdout into an unbounded buffer, which would let a hostile or
	// runaway tailscale binary stream gigabytes into Atlas (compose memory
	// limit is 128 MiB). StdoutPipe + io.LimitReader caps the read at
	// maxResponseBytes. See the constant in api.go for rationale.
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("tailscale cli: stdout pipe: %w", err)
	}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("tailscale cli: start: %w", err)
	}
	out, readErr := io.ReadAll(io.LimitReader(stdout, maxResponseBytes))
	// Always Wait so we reap the subprocess regardless of read outcome.
	waitErr := cmd.Wait()
	if readErr != nil {
		return nil, fmt.Errorf("tailscale cli: read stdout (cap %d bytes): %w", maxResponseBytes, readErr)
	}
	if waitErr != nil {
		return nil, fmt.Errorf("tailscale cli: exec failed: %w", waitErr)
	}

	var status cliStatus
	if err := json.Unmarshal(out, &status); err != nil {
		return nil, fmt.Errorf("tailscale cli: parse JSON (cap %d bytes): %w", maxResponseBytes, err)
	}

	devices := make([]Device, 0, 1+len(status.Peers))
	devices = append(devices, peerToDevice(status.Self))
	for _, peer := range status.Peers {
		devices = append(devices, peerToDevice(peer))
	}
	return devices, nil
}

func peerToDevice(p cliPeer) Device {
	d := Device{
		Hostname: p.HostName,
		Online:   p.Online,
	}
	if len(p.TailscaleIPs) > 0 {
		d.TailscaleIP = p.TailscaleIPs[0]
	}
	if p.LastSeen != "" {
		if t, err := time.Parse(time.RFC3339, p.LastSeen); err == nil {
			d.LastSeen = t
		}
	}
	return d
}
