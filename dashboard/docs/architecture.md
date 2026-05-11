# Atlas Architecture

## Component Overview

Atlas (the dashboard binary) consists of five subsystems:

```
NATS (JetStream)
    └── nats.Subscriber  →  events.Bus  →  handlers.SSE  →  SSE clients
                                        └── (ring buffer, topic filter)

REST pollers (5 s interval)
    ├── poller.AgamemnonPoller  ─┐
    ├── poller.NATSPoller       ─┤→  store.Cache  →  handlers.HostsHandler  →  htmx pages
    └── poller.TailscalePoller  ─┘

Tailscale source
    └── tailscale.Source  →  store.Cache  →  /hosts, /api/hosts

Grafana embedding
    └── grafana.KnownPanels  →  handlers.GrafanaPage  →  iframe matrix

Mnemosyne browser
    └── mnemosyne.Reader (5-min TTL cache)  →  handlers.MnemosynePage + partials
```

## Concurrency model

- **Cache**: `sync.RWMutex`-protected; writers are poller goroutines, readers are HTTP handlers.
  Every getter returns a defensive copy so map/slice mutation cannot leak across the lock
  (`internal/store/cache.go`)
- **Event Bus**: `sync.RWMutex`-protected subscriber map (`internal/events/bus.go`). Subscribers
  receive `chan events.Event` (the typed event payload, not raw bytes); the per-subscriber buffer
  size is the caller-provided argument to `Bus.Subscribe(buf)` — the SSE handler uses `1000`
  (`internal/handlers/sse.go`)
- **Ring Buffer**: a single `sync.Mutex`-guarded ring of `events.Event` values across **all**
  topics — not one buffer per topic. Capacity is set at process startup via `events.NewBus(256)`
  in `cmd/argus-dashboard/main.go` and replayed on SSE connect (clamped to the buffer length)
- **Slow consumers**: `Bus.Publish` performs non-blocking fan-out
  (`select { case ch <- e: default: atomic.AddInt64(&drops, 1) }`); a slow SSE client drops
  events rather than blocking the bus
- **Metrics**: an isolated `prometheus.Registry` per process (`internal/server/metrics.go`);
  counters and gauges from `prometheus/client_golang` are inherently goroutine-safe

## Resource bounds

The values below are hardcoded at v0.2.0 — operators sizing memory should account for these:

| Bound | Value | Source |
| --- | --- | --- |
| Event-bus ring capacity | 256 | `cmd/argus-dashboard/main.go` (`events.NewBus(256)`) |
| Per-SSE-subscriber channel buffer | 1000 | `internal/handlers/sse.go` (`bus.Subscribe(1000)`) |
| Per-agent event history | 50 | `internal/store/cache.go` (`maxAgentEvents`) |
| SSE heartbeat interval | 15 s | `internal/handlers/sse.go` |
| Service prober worker pool | 16 | `internal/catalog/probe.go` (`workerCount`) |
| NATS subscriber `MaxAckPending` | 1024 | `internal/nats/subscriber.go` |
| NATS subscriber `AckWait` | 30 s | `internal/nats/subscriber.go` |
| HTTP read timeout | 10 s | `internal/server/server.go` |
| HTTP idle timeout | 60 s | `internal/server/server.go` |
| HTTP write timeout | 0 (unbounded; required for SSE) | `internal/server/server.go` |

## Tailscale device discovery

The `internal/tailscale` package provides four interchangeable `Source` implementations
(selected via `ATLAS_TAILSCALE_SOURCE`):

- **`static`** — devices come from `ATLAS_WORKER_HOST_IP` and `ATLAS_CONTROL_HOST_IP`. No network
  calls. Smallest blast radius; use when running off-tailnet (CI, dev, single-host).
- **`cli`** — shells out to `tailscale status --json` via `exec.CommandContext` with a fixed
  argv. Requires the `tailscale` binary on `PATH` and a logged-in tailnet daemon.
- **`api`** — calls the Tailscale Central API at
  `api.tailscale.com/api/v2/tailnet/{name}/devices` using `ATLAS_TAILSCALE_API_KEY`. Requires
  `ATLAS_TAILNET_NAME`. Rate-limited by Tailscale.
- **`auto`** — tries `cli` first, falls back to `api` on error. Useful when Atlas is sometimes
  on the tailnet and sometimes not.

The active `Source` is wrapped in a `Refresher` (`internal/tailscale/refresher.go`) that polls
every 30 s and writes results into `store.Cache` via a local `DeviceStore` interface — this
avoids a tailscale↔store import cycle.

## Security

- All HTML responses include `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`,
  `Referrer-Policy: strict-origin-when-cross-origin`
- CSP `frame-src` is built statically at startup from `ATLAS_GRAFANA_URL`, `ATLAS_LOKI_URL`, and
  optionally `ATLAS_NATS_DASHBOARD_URL`. Each URL is validated by `config.Validate` (rejects
  whitespace, semicolons, quotes, non-`http(s)` schemes) before being interpolated into the
  header — no user input reaches CSP at runtime
- Only `/livez` and its alias `/healthz` are unauthenticated. **`/readyz` and `/metrics` ARE
  auth-gated** (see `internal/server/routes.go` — both endpoints sit inside the `r.Group` that
  applies the configured auth middleware) so component error strings and internal counters
  cannot be read anonymously. Configure your scrape targets accordingly.
- Auth modes are `none` / `basic` / `bearer`; the default since v0.2.0 is `bearer`. Bearer
  tokens are compared with `subtle.ConstantTimeCompare`; an empty configured token rejects all
  requests
- SSE/EventSource bearer fallback via `?token=` allows browsers' built-in `EventSource` (which
  cannot set custom headers) to authenticate
- Mnemosyne markdown rendering uses goldmark with `Unsafe=false` (the default) — raw HTML and
  `javascript:` URLs in skill files are stripped. The `render.go` source carries a comment
  forbidding `goldmark.WithUnsafe()`
- Mnemosyne reader resolves the requested path with `filepath.EvalSymlinks` and verifies it
  stays under the configured skills root — defends against symlink-escape path traversal
- Grafana `from`/`to` query params validated against `^(now(-[0-9]+(s|m|h|d|w|y))?|[0-9]{13})$`
  before embedding in iframe URLs
- iframe sandbox: `allow-scripts allow-popups` only — never `allow-same-origin` alongside
  `allow-scripts`
