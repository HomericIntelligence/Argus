# Atlas — HomericIntelligence Dashboard

Atlas is the unified observability dashboard for the HomericIntelligence distributed agent mesh.
It provides a real-time overview of agents, tasks, NATS streams, and hosts via a lightweight
Go/Chi HTTP server with a dark-themed UI built on htmx and SSE.

## Quick Start

The default `ATLAS_AUTH_MODE=bearer` requires `ATLAS_AUTH_BEARER_TOKEN`. The simplest way to
get going locally is to set both, or to switch off auth explicitly with `ATLAS_AUTH_MODE=none`
(strictly local — Atlas will log a warning).

### Hot-reload dev loop

Once you've run `./scripts/setup.sh && pixi shell` and have
[`templ`](https://templ.guide/) and [`air`](https://github.com/air-verse/air) on your
`$PATH`, a single command runs the whole edit/build/reload cycle:

```bash
just dev
```

This starts `templ generate --watch` and `air` in parallel (output is prefixed `[templ]`
and `[air]`). Edit a `.templ` or `.go` file and the dashboard rebuilds and restarts
automatically; Ctrl-C tears both watchers down.

If the install hints flag a missing tool:

```bash
go install github.com/a-h/templ/cmd/templ@v0.3.1001
go install github.com/air-verse/air@latest
```

`air` is intentionally not pinned in `pixi.toml` or `scripts/setup.sh` — it's an opt-in
dev tool. The non-watch flow below still works unchanged.

```bash
# Local dev, auth off (logs a warning)
ATLAS_AUTH_MODE=none go run ./cmd/argus-dashboard

# Liveness probe is unauthenticated and always works
curl -fsS http://localhost:3002/livez

# Local dev, bearer auth (production-shaped)
export ATLAS_AUTH_BEARER_TOKEN="$(openssl rand -hex 32)"
go run ./cmd/argus-dashboard

# Now /readyz, /metrics and the UI all require the token:
curl -fsS -H "Authorization: Bearer ${ATLAS_AUTH_BEARER_TOKEN}" http://localhost:3002/readyz

# Custom listen address
ATLAS_LISTEN_ADDR=:8090 go run ./cmd/argus-dashboard
```

### Run Atlas standalone (without the full Argus stack)

You don't need Prometheus, Grafana, Loki, Agamemnon, or Nestor running to bring Atlas up —
the pollers and NATS subscriber will just report "not connected" via `/readyz`. The minimum
moving parts are Atlas itself plus optionally a local NATS server (with JetStream) for the SSE
spine to wire up live.

```bash
# Terminal 1 — single-process NATS with JetStream
nats-server -js

# Terminal 2 — Atlas pointed at it, no upstream services configured
ATLAS_AUTH_MODE=none \
ATLAS_NATS_URL=nats://127.0.0.1:4222 \
ATLAS_NATS_MON_URL=http://127.0.0.1:8222 \
go run ./cmd/argus-dashboard
```

`/readyz` will report Agamemnon poller failing (no upstream is configured). The NATS
subscriber attempts to attach to six JetStream streams (`homeric-{agents,tasks,myrmidon,research,pipeline,logs}`).
A fresh `nats-server -js` has no streams provisioned, so `attached=0` and the subscriber
reports not-ready until you create them. To bring up just one for a smoke test:

```bash
nats stream add homeric-agents --subjects 'hi.agents.>' --storage memory \
  --retention limits --discard old --max-msgs=1000 --defaults
nats pub hi.agents.demo '{"id":"demo","status":"ok"}'
```

The `event: agent` frame should appear immediately on `/events`.

For full-stack development against the rest of the Argus services, see the parent
[`Argus` README](../README.md) and `just start`.

## Configuration

All configuration is via environment variables with the `ATLAS_` prefix:

| Variable | Default | Description |
| --- | --- | --- |
| `ATLAS_LISTEN_ADDR` | `:3002` | HTTP listen address |
| `ATLAS_LOG_LEVEL` | `info` | Log level (debug/info/warn/error) |
| `ATLAS_NATS_URL` | `nats://nats:4222` | NATS server URL |
| `ATLAS_NATS_MON_URL` | `http://nats:8222` | NATS monitoring URL |
| `ATLAS_AGAMEMNON_URL` | `http://agamemnon:8080` | Agamemnon API URL |
| `ATLAS_NESTOR_URL` | `http://nestor:8081` | Nestor API URL |
| `ATLAS_HERMES_URL` | `http://hermes:8080` | Hermes event bridge URL |
| `ATLAS_PROMETHEUS_URL` | `http://prometheus:9090` | Prometheus URL |
| `ATLAS_GRAFANA_URL` | `http://grafana:3000` | Grafana URL |
| `ATLAS_AUTH_MODE` | `bearer` | Auth mode (none/basic/bearer) — default since v0.2.0 |
| `ATLAS_AUTH_BEARER_TOKEN` | `` | Bearer token (required when `ATLAS_AUTH_MODE=bearer`) |
| `ATLAS_AUTH_USER` | `` | Basic auth username (required when `ATLAS_AUTH_MODE=basic`) |
| `ATLAS_AUTH_PASS` | `` | Basic auth password (required when `ATLAS_AUTH_MODE=basic`) |
| `ATLAS_TAILSCALE_SOURCE` | `static` | Device discovery: `static`, `cli`, `api`, `auto` |
| `ATLAS_WORKER_HOST_IP` | `127.0.0.1` | Static source: IP of worker host |
| `ATLAS_CONTROL_HOST_IP` | `127.0.0.1` | Static source: IP of control host |
| `ATLAS_TAILSCALE_API_KEY` | `` | API source: Tailscale API key |
| `ATLAS_TAILNET_NAME` | `` | API source: Tailnet name (e.g. `example.com`) |
| `ATLAS_POLL_AGAMEMNON_MS` | `5000` | Poll interval for Agamemnon in ms |
| `ATLAS_RATE_LIMIT_PER_MIN` | `30` | Per-IP request budget (per minute) for every route except `/livez` and `/healthz`. `0` disables. |
| `ATLAS_LIVEZ_RATE_LIMIT_PER_MIN` | `240` | Per-IP request budget (per minute) for `/livez` and `/healthz` only — high enough to accommodate 5-second k8s probes plus sidecars. `0` disables. |
| `ATLAS_NATS_DASHBOARD_URL` | `` | Optional: URL of external nats-dashboard (linked on /nats page) |
| `ATLAS_NATS_TOP_URL` | `` | Optional: ttyd URL serving nats-top (embedded as iframe on /nats page) |
| `ATLAS_MNEMOSYNE_SKILLS_DIR` | `/mnt/mnemosyne/skills` | Path to Mnemosyne skills directory (read by /mnemosyne page) |
| `ATLAS_LOKI_URL` | `http://loki:3100` | Loki URL (included in CSP frame-src) |
| `ATLAS_EXPORTER_URL` | `http://argus-exporter:9100` | Homeric exporter URL |
| `ATLAS_HTTP_READ_TIMEOUT` | `10s` | HTTP server read timeout (`time.Duration` syntax: `750ms`, `2s`, `1h30m`). Bad/zero/negative values fall back to default. |
| `ATLAS_HTTP_IDLE_TIMEOUT` | `60s` | HTTP server idle keep-alive timeout. |
| `ATLAS_UPSTREAM_TIMEOUT` | `3s` | Per-request HTTP client timeout for the JSON-poll pollers (Agamemnon + NATS monitoring). |
| `ATLAS_TAILSCALE_API_TIMEOUT` | `10s` | HTTP client timeout for the Tailscale REST API. |
| `ATLAS_TAILSCALE_CLI_TIMEOUT` | `5s` | Wall-clock cap for the `tailscale status --json` subprocess. |
| `ATLAS_PROBE_INTERVAL` | `10s` | Cadence at which the service prober re-checks each Tailscale device. |
| `ATLAS_NATS_POLL_INTERVAL` | `5s` | Cadence of the NATS monitoring poller (/varz, /jsz, /connz). |
| `ATLAS_TAILSCALE_REFRESH_INTERVAL` | `30s` | Cadence of the Tailscale device refresher loop. |
| `ATLAS_SSE_HEARTBEAT_INTERVAL` | `15s` | Cadence of the keep-alive comment frame on the SSE event stream. |
| `ATLAS_SSE_SUBSCRIBER_BUFFER` | `1000` | Per-SSE-client channel buffer size; slow clients drop events when this fills rather than back-pressuring the bus. |
| `ATLAS_BUS_RING_CAPACITY` | `256` | events.Bus ring-buffer capacity used for the `replay=` SSE replay window. |
| `ATLAS_NATS_ACK_WAIT` | `30s` | JetStream consumer AckWait — re-delivery window for un-acked messages. |
| `ATLAS_NATS_MAX_ACK_PENDING` | `1024` | JetStream consumer MaxAckPending — cap on un-acked in-flight messages per consumer. |

## SSE Event Stream

Atlas exposes a real-time event stream at `/events` using Server-Sent Events.

```
GET /events?topics=agent,task&replay=20
```

| Parameter | Description |
| --- | --- |
| `topics` | Comma-separated topic filter. Omit to receive all topics. |
| `replay` | Number of buffered events to replay on connect (ring buffer, max 256). |

**Topics**: the SSE handler's server-side allowlist accepts the eight topics below
(`internal/handlers/sse.go`). Six are derived from NATS subjects; two (`nats`, `host`)
are bus-only — published by Atlas itself when its REST pollers refresh state.

| Topic | Source | NATS stream / origin | Subject pattern |
| --- | --- | --- | --- |
| `agent` | NATS subscriber | `homeric-agents` | `hi.agents.>` |
| `task` | NATS subscriber | `homeric-tasks` | `hi.tasks.>` |
| `myrmidon` | NATS subscriber | `homeric-myrmidon` | `hi.myrmidon.>` |
| `research` | NATS subscriber | `homeric-research` | `hi.research.>` |
| `pipeline` | NATS subscriber | `homeric-pipeline` | `hi.pipeline.>` |
| `log` | NATS subscriber | `homeric-logs` | `hi.logs.>` |
| `nats` | Internal bus | NATS poller (`varz`/`jsz`) | n/a |
| `host` | Internal bus | Tailscale refresher / service prober | n/a |

**Wire format** (per event):

```
event: {topic}
data: {json payload}

```

Keepalive comment frames are sent every 15 seconds:

```
: heartbeat

```

## HTTP Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Overview page |
| `GET` | `/hosts` | Tailscale host grid — cards refresh every 5 s via htmx |
| `GET` | `/livez` | Liveness probe — always 200 if the process is up. **Unauthenticated.** |
| `GET` | `/healthz` | Alias of `/livez` retained for backwards compatibility. **Unauthenticated.** |
| `GET` | `/readyz` | Readiness probe — JSON aggregator over Agamemnon poller, NATS poller, NATS subscriber. 200 only when all components OK; 503 with the offending component named otherwise. **Auth-gated.** |
| `GET` | `/metrics` | Prometheus metrics exposition. **Auth-gated** (since v0.2.0) — Prometheus scrape configs must send the bearer token. |
| `GET` | `/events` | SSE event stream (see below) |
| `GET` | `/api/hosts` | JSON array of hosts with per-service probe results |
| `GET` | `/partials/host/{name}` | htmx fragment — single host card (used by 5 s poll) |
| `GET` | `/agents` | Agents list page with filter bar and live SSE row-swap |
| `GET` | `/partials/agents/table` | htmx fragment — filtered agents tbody |
| `GET` | `/agents/{id}` | Agent detail page with live 50-event tail |
| `GET` | `/tasks/{id}` | Task detail page with live event tail |
| `GET` | `/grafana` | Grafana iframe panel matrix (8 panels, time-range selector) |
| `GET` | `/nats` | NATS monitoring page — JetStream streams, connections, external links |
| `GET` | `/partials/nats/streams` | htmx fragment — JetStream streams table (5 s poll) |
| `GET` | `/partials/nats/connections` | htmx fragment — NATS connections table (5 s poll) |
| `GET` | `/mnemosyne` | Mnemosyne skill registry browser with live search |
| `GET` | `/partials/mnemosyne/search` | htmx fragment — filtered skill list |
| `GET` | `/partials/mnemosyne/skill/{name}` | htmx fragment — rendered markdown body of a skill |
| `GET` | `/static/*` | Static assets (CSS, JS) |

## Authentication

Set `ATLAS_AUTH_MODE` to configure the auth gate:

| Mode | Behaviour |
| ------ | ----------- |
| `bearer` (default since v0.2.0) | `Authorization: Bearer <token>` header required; SSE endpoints also accept `?token=<token>`. Atlas refuses to start if the token is unset. |
| `basic` | `Authorization: Basic <base64(user:pass)>` required. Atlas refuses to start if user or pass is unset. |
| `none` | No authentication required. Logs a startup warning. **Do not use in production.** |

Set `ATLAS_AUTH_BEARER_TOKEN`, `ATLAS_AUTH_USER`, `ATLAS_AUTH_PASS` accordingly.

**Auth-gated routes** (require the configured mode): `/`, `/hosts`, `/agents`, `/tasks/{id}`,
`/grafana`, `/nats`, `/mnemosyne`, all `/api/*` and `/partials/*`, `/events`, **`/readyz`**, **`/metrics`**.

**Unauthenticated routes**: only `/livez` and `/healthz` (its alias). These return a static
200 if the process is up and never reveal component or counter state.

> **Operator note:** `/metrics` and `/readyz` were unauthenticated in pre-v0.2.0 builds.
> If you are upgrading, update Prometheus scrape configs and Kubernetes readiness probes
> to send the bearer token (Prometheus: `bearer_token_file:`; k8s: `httpGet.httpHeaders[]`).

## Metrics

Atlas exposes Prometheus metrics at `/metrics`. The endpoint is **auth-gated** since v0.2.0
(see [Authentication](#authentication)) — your scrape job must present the configured credentials.

| Metric | Type | Description |
| -------- | ------ | ------------- |
| `atlas_build_info` | gauge | Build info (version, goversion labels) |
| `atlas_nats_connected` | gauge | 1 if NATS is connected, 0 otherwise |
| `atlas_sse_connected_clients` | gauge | Active SSE client connections |
| `atlas_poll_errors_total{source}` | counter | REST poller errors by source |
| `atlas_poll_duration_seconds{source}` | histogram | REST poll latency |
| `atlas_sse_dropped_total{subscriber}` | counter | SSE events dropped for slow clients |
| `atlas_event_parse_errors_total{stream}` | counter | NATS event parse errors |
| `atlas_nats_messages_processed_total{stream}` | counter | NATS messages processed |

## Building

```bash
go build -ldflags "-X github.com/HomericIntelligence/atlas/internal/version.Version=$(git describe --tags --always)" ./cmd/argus-dashboard
```

## Template Generation

Templates use [templ](https://templ.guide/). Generated `*_templ.go` files are committed.
To regenerate (or use `just dev` for hot-reload):

```bash
templ generate ./...
```

The `templ-generate` pre-commit hook (see the repo's
[`CONTRIBUTING.md`](../CONTRIBUTING.md#pre-commit-hooks)) runs this for you on every commit
and fails if the committed `*_templ.go` files drift from the `.templ` sources — but the
manual command above is still the fastest way to refresh during development.

## Operating Atlas

- [`docs/architecture.md`](docs/architecture.md) — component composition, concurrency model, resource bounds, security posture.
- [`docs/runbook.md`](docs/runbook.md) — diagnosis and recovery procedures (NATS subscriber, pollers, SSE, auth, restart/rollback).
- [`docs/runbook/rollback.md`](docs/runbook/rollback.md) — emergency rollback procedure for a bad release.
- [`docs/review-charter.md`](docs/review-charter.md) — the 6-dimension review-wave gate every Atlas PR is dispatched against.
