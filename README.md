# ProjectArgus

Observability stack for the HomericIntelligence mesh. ProjectArgus provides
centralized metrics collection, log aggregation, and dashboards for all
components of the HomericIntelligence ecosystem.

## What Gets Monitored

- **ProjectAgamemnon**: Agent health (`/v1/health`), agent list (`/v1/agents`)
- **ProjectNestor**: Research pipeline health (`/v1/health`)
- **NATS**: Message rates, JetStream storage, subject counts (port 8222)
- **Nomad**: Job and allocation metrics (port 4646)
- **Containers**: All Podman/Docker container logs via Promtail

## Stack

| Component  | Role                         | Port |
|------------|------------------------------|------|
| Prometheus | Metrics scraping and storage | 9090 |
| Loki       | Log aggregation              | 3100 |
| Promtail   | Log shipping to Loki         | —    |
| Grafana    | Dashboards and visualization | 3000 |

## Quick Start

```bash
./scripts/setup.sh && just start
```

`scripts/setup.sh` checks prerequisites, installs the pixi environment, and
generates a `.env` (with a fresh bearer token and Grafana admin password)
on first run. It is idempotent — safe to re-run.

Then access Grafana at <http://localhost:3000> (credentials: admin / the password set in `.env`).

## Dashboards

- **HomericIntelligence - Agent Health**: Agent count, active/hibernated agents, uptime
- **NATS Event Bus**: Message rate, JetStream storage, subject counts
- **Task Throughput**: Tasks created/completed/failed per hour, dispatch latency
- **Argus Stack Health**: Prometheus scrape-target count (`up`), Homeric Exporter health, total targets. Stat panels
  backed by Prometheus.
- **Log Explorer**: Syslog and NATS log streams. Log panels backed by Loki.

- **Argus Stack Health**: Prometheus scrape-target up/down status, exporter health
- **Log Explorer**: Syslog and NATS log streams via Loki

## Configuration

Copy `.env.example` to `.env` and set your values before starting the stack:

```bash
cp .env.example .env
# edit .env — at minimum set GF_ADMIN_PASSWORD
just start
```

Key environment variables:

| Variable            | Default                            | Purpose                             |
|---------------------|------------------------------------|-------------------------------------|
| `GF_ADMIN_PASSWORD` | —                                  | Grafana admin password (required)   |
| `NATS_LOG_DIR`      | `/home/mvillmow/.local/share/nats` | Host log dir, mounted into Promtail |

All scrape targets and service configs live in `configs/`. Alert rules are in
`rules/`. Grafana dashboards (JSON) are in `dashboards/` and auto-provisioned
on startup.

## Common Commands

```bash
just start                   # Start all services
just stop                    # Stop all services
just status                  # Show running containers
just logs prometheus         # Tail Prometheus logs
just reload-prometheus       # Hot-reload Prometheus config
just test-scrape             # Verify all scrape targets are up
just import-dashboards       # Push dashboards to Grafana API
```
