# AGENTS.md — Argus Multi-Agent Coordination

This file is the **sole authoritative agent contract** for this repository. It is the
coordination contract for any AI agent (interactive Claude Code session, Myrmidon swarm
worker, or Agamemnon-orchestrated agent) that reads or modifies this repository, and it
follows the conventions established across HomericIntelligence projects. Read it before
touching any file. `CLAUDE.md` is a pointer to this file.

---

## Repo Role in the HomericIntelligence Ecosystem

Argus is a **read-only observability layer** — the observability stack for the
HomericIntelligence ecosystem. It scrapes metrics and tails logs from ProjectAgamemnon,
ProjectNestor, NATS, Nomad, and all running containers, aggregates logs via
Promtail → Loki, and exposes everything through Grafana dashboards. It does **not**
push data or commands back to any external service.

**Important**: Argus only reads from other services via HTTP scrapes and log tailing.
It does NOT modify Agamemnon or any other HomericIntelligence service. Agents working
in this repository must maintain this invariant.

**Permitted write targets** (within this repo only):

| Path | Description |
| ------ | ------------- |
| `configs/` | Prometheus, Loki, Promtail, Grafana config files |
| `dashboards/` | Grafana dashboard JSON |
| `rules/` | Prometheus alerting rules |
| `justfile` | Task runner commands |
| `pixi.toml` | Python / tool dependency spec |
| `AGENTS.md`, `CLAUDE.md` | Coordination and project docs |

Agents **MUST NOT** modify `docker-compose.yml` network topology, external service configs
(Agamemnon, Nestor, NATS, Nomad), or anything outside this repository.

---

## Stack Components

| Service         | Image                          | Purpose                                                |
|-----------------|--------------------------------|--------------------------------------------------------|
| Prometheus      | prom/prometheus:v2.54.1        | Scrape and store metrics                               |
| Alertmanager    | prom/alertmanager:v0.32.1      | Route Prometheus alerts to receivers                   |
| Loki            | grafana/loki:3.1.2             | Store and query log streams                            |
| loki-proxy      | nginx:1.27-alpine              | Basic-auth proxy in front of Loki                      |
| grafana-proxy   | nginx:1.27-alpine              | Basic-auth proxy in front of Grafana (issue #321)      |
| Promtail        | grafana/promtail:3.1.2         | Tail container logs and ship to Loki                   |
| Grafana         | grafana/grafana:11.2.2         | Visualize metrics and logs                             |
| argus-exporter  | built from exporter/           | Convert HomericIntelligence APIs to Prometheus metrics |

### Network topology (two-network design)

The compose stack defines two Docker bridge networks:

- **`argus`** — public-facing bridge that prometheus, alertmanager, loki-proxy,
  grafana-proxy, promtail, grafana, and argus-exporter share. Anything that needs
  to talk to other services in the stack lives here.
- **`loki-internal`** — `internal: true` bridge with no egress. Only `loki`,
  `loki-proxy`, and `promtail` are attached. Loki is intentionally not on
  `argus`, so the only path to reach it is via `loki-proxy` (which terminates
  basic auth). Do not re-add `loki` to the `argus` network — that would let any
  container hit Loki directly without auth.

All services are managed via `docker-compose.yml`.

## Architecture

```mermaid
graph TD
    A[Agamemnon :8080] -->|HTTP pull| E[argus-exporter :9100]
    N[Nestor :8081]    -->|HTTP pull| E
    NATS[NATS :8222]   -->|HTTP pull| E
    E -->|/metrics| P[Prometheus :9090]
    NOMAD[Nomad :4646] -->|/v1/metrics| P
    P -->|query| G[Grafana :3000]
    L[Loki :3100] -->|query| G
    PT[Promtail :9080] -->|push| L
    LP[loki-proxy :3101] -->|auth proxy| L
    GP[grafana-proxy :3001] -->|auth proxy + TLS| G
    LOGS[/var/log + NATS logs] -->|tail| PT
```

## Environment Variables

Copy `.env.example` to `.env` before running `just start`. The stack will refuse
to start without a `.env` file.

| Variable            | Default in .env.example              | Required | Purpose                                            |
|---------------------|--------------------------------------|----------|----------------------------------------------------|
| `GF_ADMIN_PASSWORD` | `changeme`                           | **Yes**  | Grafana admin password                             |
| `GRAFANA_PROXY_USER` | `grafana`                           | **Yes**  | grafana-proxy Basic Auth user (issue #321)         |
| `GRAFANA_PROXY_PASSWORD` | `changeme`                     | **Yes**  | grafana-proxy Basic Auth password (issue #321)     |
| `AGAMEMNON_URL`     | `http://172.20.0.1:8080`             | Yes      | Agamemnon API base URL                             |
| `NESTOR_URL`        | `http://172.20.0.1:8081`             | Yes      | Nestor API base URL                                |
| `NATS_URL`          | `http://172.24.0.1:8222`             | Yes      | NATS monitoring API base URL                       |
| `NATS_LOG_DIR`      | `/home/mvillmow/.local/share/nats`   | Yes      | Host path to NATS log files (Promtail mounts this) |

Optional overrides (not required by `just start`):

- `PROMTAIL_HOST_LABEL` — overrides the `host` label Promtail attaches to log
  streams. Defaults to the container's `$HOSTNAME`.
- `HOSTNAME` — Promtail's `promtail.yml` substitutes `${HOSTNAME:-hermes}` into
  the Loki `host` label. The Promtail container inherits `HOSTNAME` from the
  `hostname:` field set on the `promtail` service in `docker-compose.yml`. When
  deploying on a new host, set `hostname:` in `docker-compose.yml` (or set the
  `HOSTNAME` env var explicitly) so each host's logs are distinguishable in
  Loki. If unset, Promtail falls back to the container's runtime ID, which
  changes on every restart and is not human-readable.
- `CONTAINER_CMD` — runtime used by `scripts/backup.sh` and `scripts/restore.sh`.
  Defaults to `docker` (auto-promoted to `podman` if `podman-compose` is on
  `$PATH`). Justfile recipes pass `CONTAINER_CMD={{container_cmd}}`
  automatically, so you rarely need to set it by hand.

`172.20.0.1` / `172.24.0.1` are WSL2 host gateway addresses — they reach services
running on the Windows host or in other WSL distros. Substitute Tailscale IPs for
cross-host deployments. The `NATS_URL` gateway IP `172.24.0.1` differs from
the `172.20.0.1` used for Agamemnon/Nestor because NATS runs on a separate WSL
distro with its own bridge — the discrepancy is intentional, not a typo.

### Exporter Environment Variables

Atlas dashboard variables use the `ATLAS_` prefix — see `dashboard/README.md` for the full table.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `NATS_LOG_DIR` | No | `~/.local/share/nats` | Host path Promtail mounts read-only for NATS logs |
| `GF_ADMIN_PASSWORD` | **Yes** | `changeme` | Grafana admin password — change before first run |
| `AGAMEMNON_URL` | No | `http://172.20.0.1:8080` | Agamemnon API base URL |
| `NESTOR_URL` | No | `http://172.20.0.1:8081` | Nestor API base URL |
| `NATS_URL` | No | `http://172.24.0.1:8222` | NATS monitoring API URL |

`NATS_LOG_DIR` is used by the `promtail` service as a read-only bind mount
(`docker-compose.yml:98`). The full default is `/home/mvillmow/.local/share/nats`.

## Scrape Targets

| Job              | Source Env Var   | Default Host      | Path            | What it provides              |
|------------------|------------------|-------------------|-----------------|-------------------------------|
| homeric-exporter | —                | argus-exporter    | /metrics        | Agent, task, NATS metrics     |
| prometheus       | —                | localhost:9090    | /metrics        | Prometheus self-monitoring    |
| nomad            | —                | 172.20.0.1:4646   | /v1/metrics     | Job and allocation metrics    |

The exporter aggregates Agamemnon, Nestor, and NATS data and exposes them as
Prometheus metrics on port 9100.

The `NATS_URL` env var (`http://172.24.0.1:8222`) addresses the host gateway,
which the exporter container uses to reach NATS on the WSL host. Prometheus,
in contrast, scrapes NATS at `localhost:8222` — that target is interpreted
*inside* the prometheus container (because both Prometheus and the NATS
host gateway resolve to the host's loopback there). The two addresses point
at the same NATS instance from different network namespaces.

## Operator Notes

These are easy-to-miss preconditions and runtime behaviours that operators
new to the stack frequently trip on:

1. **Copy `.env.example` to `.env` first.** `just start` and `docker compose`
   both load `.env`; without it Grafana silently falls back to its
   built-in `admin:admin` credentials.
2. **`/tmp/hermes.log` must exist on the host before `just start`.** Promtail
   bind-mounts the file. If it is missing, Docker creates an empty
   *directory* at that path, which silently breaks the mount. Run
   `touch /tmp/hermes.log` (or symlink to the real Hermes log) once per host.
3. **Proxy htpasswd files are generated automatically.** `just start` depends
   on `just gen-htpasswd`, which writes `secrets/htpasswd` (Loki) and
   `secrets/htpasswd-grafana` (Grafana, issue #321) from
   `LOKI_AUTH_USER`/`LOKI_AUTH_PASSWORD` and
   `GRAFANA_PROXY_USER`/`GRAFANA_PROXY_PASSWORD` in `.env`. To rotate either
   password, update the value in `.env`, then run `just gen-htpasswd && just restart`.
4. **All host ports are loopback-only.** Prometheus (`127.0.0.1:9090`),
   the Grafana auth proxy (`127.0.0.1:3001`, plain HTTP), Alertmanager
   (`127.0.0.1:9093`), and the exporter (`127.0.0.1:9100`) only accept
   connections from the host. To reach them from another machine use an SSH tunnel
   (`ssh -L 3001:localhost:3001 host`) or a Tailscale-encrypted route — the
   stack intentionally does not expose unauthenticated metric/log endpoints
   to the LAN.
5. **Grafana sits behind two credentials since #321.** Browsers hit the nginx
   basic-auth proxy on `127.0.0.1:3001` first (`GRAFANA_PROXY_USER` /
   `GRAFANA_PROXY_PASSWORD`, hard-failed by `just start` if left at an insecure
   default), then Grafana's own login page (`GF_ADMIN_PASSWORD`). Rotate each
   independently: proxy creds via `.env` + `just gen-htpasswd && just restart`;
   admin password via `.env` + updating Grafana itself (or wiping
   `grafana_data`). Grafana has **no host port**; `just import-dashboards`
   therefore runs the API call inside the grafana container.
6. **Grafana cookies are not `Secure`.** The proxy listener is plain HTTP on
   loopback by design — the threat model is *unauthenticated access*, not
   passive eavesdropping on loopback. Operators who want HTTPS to the browser
   can override `GF_SERVER_ROOT_URL=https://...` and add a TLS listener to
   `configs/nginx/grafana.conf` (future option).
7. **Atlas bypasses grafana-proxy by design.** Atlas connects to Grafana
   directly over its internal HTTPS endpoint (`https://argus-grafana:3000`);
   both sit inside the trust boundary on the `argus` bridge, so routing
   through the proxy would add no security.
8. **`just test-scrape` requires the stack to be running.** After the host
   port for Prometheus was removed, `test-scrape` runs the query *inside*
   the prometheus container via `docker exec`. Use `just debug-prometheus`,
   `just debug-loki`, and `just debug-grafana-proxy` for ad-hoc inspection
   (these wrappers exec into the respective containers).
6. **`just backup` / `just restore` need a running compose project.** The
   restore script calls `docker compose stop` to quiesce services before
   replacing volume data; on a cold host with no containers, the stop is a
   no-op and the script still runs, but operators should expect to bring
   the stack up at least once before relying on restore.
7. **`jq` is unavailable on `win-64`.** Conda-forge does not ship a `jq`
   package for Windows; tasks like `just test-scrape` that pipe through `jq`
   will fail there. Windows contributors should install `jq` via `winget` or
   `choco` and put it on `$PATH`.

## Metric Catalog

The full catalog of every metric the exporter emits — name, labels, and
semantics — lives at [`docs/metrics.md`](docs/metrics.md). Treat
`exporter/exporter.py`'s `_METRIC_HELP` dict as the source of truth and update
`docs/metrics.md` in the same commit when renaming or adding a metric.

## Metric Naming Conventions

All HomericIntelligence-specific metrics follow the `hi_` prefix:

- `hi_agamemnon_health` — health probes (0/1 gauges)
- `hi_agents_*` — agent inventory counts
- `hi_tasks_*` — task counts by status
- `hi_nestor_*` — Nestor health and research stats

NATS metrics use the `nats_` prefix:

- `nats_connections`, `nats_slow_consumers` — current state (gauges)
- `nats_in_msgs`, `nats_out_msgs`, `nats_in_bytes`, `nats_out_bytes` —
  current rates from `/varz` (gauges; reset on NATS restart)
- `nats_jetstream_*` — JetStream stats

Exporter self-metrics use the `homeric_exporter_` prefix:

- `homeric_exporter_scrape_duration_seconds` — last collect() wall time
- `homeric_exporter_scrape_timestamp_seconds` — unix timestamp of last scrape
- `homeric_exporter_fetch_errors` — per-upstream fetch error counts (gauge; resets each scrape)

All metrics include `# HELP` and `# TYPE` lines.

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
Argus/
├── configs/
│   ├── prometheus.yml        # Scrape configs
│   ├── loki.yml              # Loki server config
│   ├── promtail.yml          # Log scraping config
│   ├── nginx/
│   │   ├── loki.conf         # Nginx proxy config for Loki auth
│   │   ├── grafana.conf      # Nginx proxy config for Grafana auth (#321)
│   │   └── grafana-map.conf  # WebSocket Connection-header map for grafana-proxy
│   └── grafana/
│       ├── datasources.yml   # Auto-provision Prometheus + Loki datasources
│       └── dashboards.yml    # Auto-provision dashboards from dashboards/
├── dashboards/               # Grafana dashboard JSON files
├── exporter/
│   ├── exporter.py           # Custom Prometheus exporter (stdlib only)
│   └── Dockerfile            # Multi-stage, non-root image build
├── rules/
│   ├── agent-alerts.yml      # Prometheus alerting rules
│   └── recording-rules.yml   # Pre-computed recording rules
├── scripts/
│   └── scrape-agamemnon.sh   # Manual endpoint test script
├── tests/                    # pytest unit tests
├── docker-compose.yml
├── justfile
└── pixi.toml
```

## Key Principles

1. Read-only access to the rest of the HomericIntelligence ecosystem — no modifications to external services.
2. All configuration is file-based and version-controlled; no manual Grafana UI changes that are not exported to JSON.
3. Prometheus scrape interval is 15s globally; do not tighten below 10s without understanding cardinality impact.
4. Loki retention is 30 days (720h); adjust `retention_period` in `configs/loki.yml` if storage is constrained.
5. The `.env` file is gitignored and must never be committed. Use `.env.example` as the template.

## Development Guidelines

- Edit scrape targets in `configs/prometheus.yml` and run `just reload-prometheus` — no restart required.
- Add new dashboards as JSON files in `dashboards/` and run `just import-dashboards`.
- Alert rules in `rules/` also take effect after `just reload-prometheus`.
- Use `just test-scrape` to verify the `up` metric for all targets before declaring a scrape job healthy.
- Run `just test` to execute the unit test suite before submitting a PR.
- `import-dashboards` reads `GF_ADMIN_PASSWORD` from `.env` — never hardcode credentials.

## Common Commands

```bash
just start                   # docker compose up -d (requires .env)
just stop                    # docker compose down
just status                  # docker compose ps
just logs <service>          # docker compose logs -f <service>
just reload-prometheus       # Send SIGHUP to Prometheus (hot-reload config)
just reload-alertmanager     # POST /-/reload to Alertmanager (hot-reload config)
just test-alertmanager       # Check Alertmanager /-/healthy and cluster status
just test-scrape             # Query Prometheus /api/v1/query?query=up
just import-dashboards       # POST each dashboard JSON to Grafana API
just scrape-agamemnon        # Manually test Agamemnon and Nestor health endpoints
just test                    # Run pytest unit tests
just backup                  # Back up data volumes to ./backups/
```

---

## Agent Access Model

Three agent classes operate on this repo:

| Class | Spawned By | Write Access | Notes |
| ------- | ----------- | -------------- | ------- |
| **Interactive** | Human via Claude Code CLI/IDE | Yes (user-confirmed) | Full permission scope |
| **Myrmidon swarm worker** | `hephaestus:myrmidon-swarm` | Worktree only | `isolation: "worktree"` required |
| **Agamemnon-orchestrated** | Remote trigger from Agamemnon | Read-only | Scrape validation and metric checks only |

---

## Available Skills (Hephaestus Plugin)

Argus delegates orchestration to the **Hephaestus plugin**. There is no `.claude/agents/`
directory in this repo. Use these skills instead:

| Skill | Trigger Condition | Description |
| ------- | ------------------ | ------------- |
| `hephaestus:advise` | Before unfamiliar work or unknown errors | Searches team knowledge base for prior learnings |
| `hephaestus:learn` | After experiments or novel discoveries | Saves session learnings as a new skill |
| `hephaestus:myrmidon-swarm` | Multi-step parallel file changes | Hierarchical delegation (Opus → Sonnet → Haiku) |
| `hephaestus:repo-analyze` | Full quality audit (15 dimensions) | Graded audit; use before major releases |
| `hephaestus:repo-analyze-quick` | Rapid health check — showstoppers only | Grade B baseline; flags broken/missing |
| `hephaestus:repo-analyze-strict` | Quality gate for release candidates | Starts at F; requires concrete evidence |

**Recommended flow:** Run `/hephaestus:advise` before any unfamiliar work. Run
`/hephaestus:learn` after discovering something non-obvious so the next agent benefits.

---

## Model-Tier Assignments

| Task Class | Model Tier | Rationale |
| ----------- | ----------- | ----------- |
| Architecture decisions, cross-service impact analysis | L0/L1 — Opus | Strategic reasoning across many constraints |
| Config authoring, dashboard JSON, alert rules, code review | L2/L3 — Sonnet | Domain specialist; cost-effective |
| Boilerplate YAML edits, single-file formatting, lint fixes | L4/L5 — Haiku | Focused, fast, cheap |

When using `hephaestus:myrmidon-swarm`, the orchestrator (Opus) decomposes the task and
delegates leaf work to Sonnet/Haiku workers. Do not override model tiers without explicit
user approval.

---

## Conflict-Avoidance Protocol

1. **Worktree isolation is mandatory** for all file-modifying swarm agents. Use
   `isolation: "worktree"` in every `Agent(...)` call that touches the filesystem.
2. The `.worktrees/` directory is the designated landing zone. One issue → one worktree.
3. No two agents may hold a write lock on the same file simultaneously within a wave.
4. Before opening any file for editing, run `git status` to detect uncommitted changes.
5. If an unexpected file modification is found, stop and report to the user before proceeding.

---

## Mandatory Approval Gate

Before any agent spawns subordinates that modify files, the following three-phase sequence
is **required**:

1. **Phase 1 — Exploration**: Read-only research agents gather facts (no file writes).
2. **Phase 2 — Design**: Orchestrator produces a plan and presents it to the user.
3. **Phase 3 — Execution**: User confirms ("yes", "go ahead", or equivalent) before any
   write tool is called.

Skipping or collapsing phases 1–2 into phase 3 is explicitly prohibited. An agent that
writes files without user confirmation has violated this protocol.

---

## Wave Execution Constraints

| Constraint | Value |
| ----------- | ------- |
| Max agents per wave | 5 |
| Shared file writes within a wave | Not permitted |
| Each agent write scope | Its own worktree only |
| CI requirement before merge | All checks must pass |

Waves run concurrently but must not overlap on the same file. Design task decomposition so
each wave agent owns a disjoint set of files.

---

## Handoff Protocol

When handing off between agents (e.g., end of context, model swap, swarm worker completion):

1. Record the following in the issue tracker file
   (`.issue_implementer/<issue-number>.json` if it exists):
   - Current branch name
   - Active worktree path
   - Output of `git log --oneline -5`
   - Summary of completed and remaining steps
2. Leave no uncommitted changes in the worktree. Either commit or stash before handoff.
3. The receiving agent reads this file first before taking any action.

---

## Verification Commands

```bash
# Confirm no local agent definitions exist (should return nothing)
grep -r "^level:" .claude/agents/*.md 2>/dev/null | sort | uniq -c

# List active worktrees
ls .worktrees/ 2>/dev/null || echo "(no active worktrees)"

# Check stack health
just status

# Verify scrape targets are reachable
just test-scrape
```

---

## Scope of Automated Changes

Agents are permitted to make the following changes autonomously:

| Area | Permitted |
| ------ | ----------- |
| `configs/prometheus.yml` — add/edit scrape jobs | Yes |
| `configs/loki.yml` — adjust retention, limits | Yes |
| `configs/promtail.yml` — add log scrape targets | Yes |
| `dashboards/*.json` — add or update Grafana dashboards | Yes |
| `rules/*.yml` — add or update alert and recording rules | Yes |
| `exporter/exporter.py` — add metrics, fix bugs | Yes |
| `tests/` — add or update tests | Yes |
| `docker-compose.yml` — add/remove services or env vars | **Review required** |
| `.github/workflows/` — CI changes | **Review required** |
| Credentials, secrets, `.env` | **Never** |

## Coordination Protocol

When multiple agents work on this repository simultaneously:

1. **One scrape target per PR** — Do not bundle unrelated Prometheus job additions.
2. **Metric names are stable API** — Never rename an existing metric without a
   deprecation period and dashboard update in the same PR.
3. **Alert rules must not regress** — `just validate` must pass before merging.
4. **Test coverage** — Any change to `exporter/exporter.py` must include or update
   tests in `tests/test_exporter.py`. `just test` must pass.

## Validation Gates

Before opening a PR, agents must verify:

```bash
just validate          # docker compose config + YAML lint
just test              # pytest unit tests
pixi run ruff check exporter/exporter.py
pixi run bandit -ll exporter/exporter.py
```

## Prohibited Actions

- Do not commit `.env` or any file containing credentials.
- Do not modify external service configurations (Agamemnon, Nestor, NATS).
- Do not remove existing alert rules without explicit instruction.
- Do not tighten the Prometheus scrape interval below 10s.
- Do not expose internal ports (Loki 3100, Prometheus 9090) beyond localhost.

## AI Agent Collaboration Notes

- This is a **config-only / infrastructure** repository. There is no application code to compile.
- The primary source of truth for metric definitions is `exporter/exporter.py`.
- Do not add scrape targets that pull from services outside the HomericIntelligence ecosystem without discussion.
- Alert rule changes in `rules/` must be validated with `just validate` before merging.
