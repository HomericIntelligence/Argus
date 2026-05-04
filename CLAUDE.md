# ProjectArgus — CLAUDE.md

## Project Overview

ProjectArgus is the observability stack for the HomericIntelligence ecosystem. It
collects metrics from ProjectAgamemnon, ProjectNestor, NATS, Nomad, and all running
containers, aggregates logs via Promtail → Loki, and exposes everything through
Grafana dashboards.

**Important**: ProjectArgus only reads from other services via HTTP scrapes and log
tailing. It does NOT modify Agamemnon or any other HomericIntelligence service.

## Stack Components

| Service    | Image                          | Purpose                                 |
|------------|--------------------------------|-----------------------------------------|
| Prometheus | prom/prometheus:v2.54.1        | Scrape and store metrics                |
| Loki       | grafana/loki:3.1.2             | Store and query log streams             |
| Promtail   | grafana/promtail:3.1.2         | Tail container logs and ship to Loki    |
| Grafana    | grafana/grafana:11.2.2         | Visualize metrics and logs              |
| Atlas dashboard | ghcr.io/homericintelligence/atlas:latest | Unified status dashboard on :3002 |

| Service    | Image                     | Purpose                                 |
|------------|---------------------------|-----------------------------------------|
| Prometheus | prom/prometheus:v2.54.1   | Scrape and store metrics                |
| Loki       | grafana/loki:3.1.2        | Store and query log streams             |
| Promtail   | grafana/promtail:3.1.2    | Tail container logs and ship to Loki    |
| Grafana    | grafana/grafana:11.2.2    | Visualize metrics and logs              |

All services run on the `argus` Docker network and are managed via `docker-compose.yml`.

## Scrape Targets

| Job              | Host              | Path            | What it provides              |
|------------------|-------------------|-----------------|-------------------------------|
| agamemnon        | 172.20.0.1:8080   | /v1/agents      | Agent health via exporter     |
| nestor           | 172.20.0.1:8081   | /v1/health      | Nestor health via exporter    |
| nats             | localhost:8222    | /metrics        | Message rates, stream storage |
| nomad            | localhost:4646    | /v1/metrics     | Job and allocation metrics    |

`172.20.0.1` is the WSL2 host gateway — this reaches services running on the Windows host or in other WSL distros.

## Environment Variables

Copy `.env.example` to `.env` at the repository root and set values before running `just start`.

Atlas dashboard variables use the `ATLAS_` prefix — see `dashboard/README.md` for the full table.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `NATS_LOG_DIR` | No | `~/.local/share/nats` | Host path Promtail mounts read-only for NATS logs |
| `GF_ADMIN_PASSWORD` | **Yes** | `changeme` | Grafana admin password — change before first run |
| `AGAMEMNON_URL` | No | `http://172.20.0.1:8080` | Agamemnon API base URL |
| `NESTOR_URL` | No | `http://172.20.0.1:8081` | Nestor API base URL |
| `NATS_URL` | No | `http://172.24.0.1:8222` | NATS monitoring API URL |

`NATS_LOG_DIR` is used by the `promtail` service as a read-only bind mount
(`docker-compose.yml:98`). The full default is `/home/mvillmow/.local/share/nats`.

## Dashboard Descriptions

- **agent-health.json** (`uid: agent-health`): Total agent count (`hi_agents_total`),
  online vs. offline agents (`hi_agents_online`, `hi_agents_offline`), Agamemnon
  health status. Stat and timeseries panels backed by Prometheus.
- **nats-events.json** (`uid: nats-events`): NATS message throughput, JetStream
  storage used, distinct subject counts.
- **task-throughput.json** (`uid: task-throughput`): Tasks by status
  (`hi_tasks_by_status`), completed/failed counts per hour.
- **argus-health.json** (`uid: argus-health`): Prometheus scrape-target counts (`up`),
  Homeric Exporter health (`up{job="homeric-exporter"}`), total targets. Stat panels
  backed by Prometheus.
- **loki-explorer.json** (`uid: loki-explorer`): Syslog stream (`{job="syslog"}`),
  NATS log stream (`{job="nats"}`). Log panels backed by Loki.

## Repository Structure

```
ProjectArgus/
├── configs/
│   ├── prometheus.yml        # Scrape configs
│   ├── loki.yml              # Loki server config
│   ├── promtail.yml          # Log scraping config
│   └── grafana/
│       ├── datasources.yml   # Auto-provision Prometheus + Loki datasources
│       └── dashboards.yml    # Auto-provision dashboards from dashboards/
├── dashboards/               # Grafana dashboard JSON files
├── rules/
│   └── agent-alerts.yml      # Prometheus alerting rules
├── scripts/
│   └── scrape-agamemnon.sh   # Manual endpoint test script
├── docker-compose.yml
├── justfile
└── pixi.toml
```

## Key Principles

1. Read-only access to the rest of the HomericIntelligence ecosystem — no modifications to external services.
2. All configuration is file-based and version-controlled; no manual Grafana UI changes that are not exported to JSON.
3. Prometheus scrape interval is 15s globally; do not tighten below 10s without understanding cardinality impact.
4. Loki retention is 30 days (720h); adjust `retention_period` in `configs/loki.yml` if storage is constrained.

## Development Guidelines

- Edit scrape targets in `configs/prometheus.yml` and run `just reload-prometheus` — no restart required.
- Add new dashboards as JSON files in `dashboards/` and run `just import-dashboards`.
- Alert rules in `rules/` also take effect after `just reload-prometheus`.
- Use `just test-scrape` to verify the `up` metric for all targets before declaring a scrape job healthy.

## Common Commands

```bash
just start                   # docker compose up -d
just stop                    # docker compose down
just status                  # docker compose ps
just logs <service>          # docker compose logs -f <service>
just reload-prometheus       # POST /-/reload to Prometheus
just test-scrape             # Query Prometheus /api/v1/query?query=up
just import-dashboards       # POST each dashboard JSON to Grafana API
just scrape-agamemnon        # Manually test Agamemnon and Nestor health endpoints
```
