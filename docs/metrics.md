# Argus Exporter — Metric Catalog

Canonical reference for every Prometheus metric exposed by `homeric-exporter`
on `:9100/metrics`. Source of truth is the `_METRIC_HELP` dict in
[`exporter/exporter.py`](../exporter/exporter.py); update this file in the
same commit when you add or rename a metric.

All metrics are emitted as `# TYPE … gauge` (the exporter does not currently
maintain monotonic counter state across scrapes — every value is computed
fresh per scrape). Names that historically carried a `_total` suffix have been
renamed; do not reintroduce the suffix. `_total` is reserved for counters per
Prometheus naming best practices.

## HomericIntelligence-specific metrics (`hi_` prefix)

| Metric | Labels | Description |
| --- | --- | --- |
| `hi_agamemnon_health` | — | `1` if `GET ${AGAMEMNON_URL}/v1/health` returned 200, else `0`. |
| `hi_agents_count` | — | Number of agents registered in Agamemnon. |
| `hi_agents_online` | — | Number of agents with `status=online`. |
| `hi_agents_offline` | — | Number of agents with any non-online status. |
| `hi_agent_online` | `name`, `host`, `program` | `1` if this individual agent is online, else `0`. |
| `hi_tasks_count` | — | Number of tasks known to Agamemnon. |
| `hi_tasks_by_status` | `status` | Task count partitioned by status label. |
| `hi_nestor_health` | — | `1` if `GET ${NESTOR_URL}/v1/health` returned 200, else `0`. |
| `hi_nestor_research_active` | — | Active research jobs reported by Nestor `/v1/research/stats`. |
| `hi_nestor_research_completed` | — | Completed research jobs reported by Nestor `/v1/research/stats`. |
| `hi_nestor_research_pending` | — | Pending research jobs reported by Nestor `/v1/research/stats`. |

### Deprecated aliases (renamed in #426)

The former inventory-count gauges carried `_total`, which Prometheus reserves
for counters. They were renamed to `hi_agents_count` / `hi_tasks_count`
(#426). The old names are still emitted as deprecated aliases so live
Prometheus data survives one scrape-retention window:

| Metric | Description |
| --- | --- |
| `hi_agents_total` | *(deprecated, use `hi_agents_count`)* Same value as `hi_agents_count`. Remove after one release. |
| `hi_tasks_total` | *(deprecated, use `hi_tasks_count`)* Same value as `hi_tasks_count`. Remove after one release. |

## NATS metrics (`nats_` prefix)

Sourced from NATS `/varz` and `/jsz` monitoring endpoints. The `/varz`
`in_msgs`/`out_msgs`/`in_bytes`/`out_bytes` fields are cumulative since NATS
startup and reset on restart, so these are gauges (no `_total` suffix); they
increase monotonically between NATS restarts.

| Metric | Description |
| --- | --- |
| `nats_connections` | Current client connections. |
| `nats_in_msgs` | Cumulative inbound messages reported by NATS `/varz`; monotonic between NATS restarts (gauge). |
| `nats_out_msgs` | Cumulative outbound messages reported by NATS `/varz`; monotonic between NATS restarts (gauge). |
| `nats_in_bytes` | Cumulative inbound bytes reported by NATS `/varz`; monotonic between NATS restarts (gauge). |
| `nats_out_bytes` | Cumulative outbound bytes reported by NATS `/varz`; monotonic between NATS restarts (gauge). |
| `nats_slow_consumers` | Current slow-consumer connections. |
| `nats_jetstream_streams` | Number of JetStream streams. |
| `nats_jetstream_consumers` | Number of JetStream consumers. |
| `nats_jetstream_messages` | Messages currently stored across all JetStream streams. |
| `nats_jetstream_bytes` | Bytes currently stored across all JetStream streams. |

## Exporter self-metrics (`homeric_exporter_` prefix)

| Metric | Labels | Description |
| --- | --- | --- |
| `homeric_exporter_scrape_timestamp_seconds` | — | Unix timestamp (seconds) when the last scrape completed. |
| `homeric_exporter_scrape_duration_seconds` | — | Wall-clock seconds spent in the last `collect()` call. |
| `homeric_exporter_fetch_errors` | `upstream` (`agamemnon`/`nestor`/`nats`) | Per-scrape count of upstream fetch failures. Gauge — resets each collection cycle. |

## Naming audit (issue #426)

Audit of all metric names against Prometheus naming best practices (unit
suffixes on gauges where meaningful, `_total` reserved for counters):

| Metric / family | Verdict |
| --- | --- |
| `hi_agents_count`, `hi_tasks_count` | Renamed from `*_total` in #426 (gauges must not carry `_total`); deprecated aliases still emitted. |
| `hi_agamemnon_health`, `hi_nestor_health`, `hi_agent_online` | PASS — boolean 0/1 gauges, no unit applicable. |
| `hi_agents_online`, `hi_agents_offline`, `hi_tasks_by_status`, `hi_nestor_research_*` | PASS — count gauges; Prometheus does not mandate a `_count` suffix for instance counts. |
| `nats_jetstream_messages` | PASS — unit noun ("messages") already in the name; instance count, not a counter accumulator. |
| `nats_jetstream_bytes` | PASS — `_bytes` IS the unit suffix. |
| `nats_in_msgs` / `nats_out_msgs` | PASS — cumulative-since-startup counts exposed as gauges (`/varz` semantics); `_msgs` is the unit. |
| `nats_in_bytes` / `nats_out_bytes` | PASS — cumulative-since-startup byte counts exposed as gauges; `_bytes` is the unit. No `_total`. |
| `homeric_exporter_scrape_duration_seconds`, `homeric_exporter_scrape_timestamp_seconds` | PASS — `_seconds` unit suffix (fixed in #100). |
| `homeric_exporter_fetch_errors` | PASS — gauge that resets each scrape; correctly has no `_total`. |
| `hi_jetstream_events_total`, `hi_jetstream_consumer_last_seq` (jetstream-consumer) | PASS — true counters; `_total` is correct here. |
| `hi_jetstream_task_latency_seconds` (jetstream-consumer) | PASS — `_seconds` unit suffix. |
| `atlas_nats_connected`, `atlas_nats_messages_processed_total` (dashboard) | PASS — gauge without suffix; counter with `_total`. |
| `hi_jetstream_consumer_scrape_timestamp` (jetstream-consumer) | FLAGGED — gauge timestamp missing the `_seconds` unit suffix (cf. `homeric_exporter_scrape_timestamp_seconds`). Rename deferred: consumers include `rules/agent-alerts.yml` and its own test suite; tracked as follow-up. |
