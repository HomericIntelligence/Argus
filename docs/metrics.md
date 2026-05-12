# Argus Exporter — Metric Catalog

Canonical reference for every Prometheus metric exposed by `homeric-exporter`
on `:9100/metrics`. Source of truth is the `_METRIC_HELP` dict in
[`exporter/exporter.py`](../exporter/exporter.py); update this file in the
same commit when you add or rename a metric.

All metrics are emitted as `# TYPE … gauge` (the exporter does not currently
maintain monotonic counter state across scrapes — every value is computed
fresh per scrape). Names that historically carried a `_total` suffix have been
renamed; do not reintroduce the suffix.

## HomericIntelligence-specific metrics (`hi_` prefix)

| Metric | Labels | Description |
| --- | --- | --- |
| `hi_agamemnon_health` | — | `1` if `GET ${AGAMEMNON_URL}/v1/health` returned 200, else `0`. |
| `hi_agents_total` | — | Total number of agents registered in Agamemnon. |
| `hi_agents_online` | — | Number of agents with `status=online`. |
| `hi_agents_offline` | — | Number of agents with any non-online status. |
| `hi_agent_online` | `name`, `host`, `program` | `1` if this individual agent is online, else `0`. |
| `hi_tasks_total` | — | Total number of tasks known to Agamemnon. |
| `hi_tasks_by_status` | `status` | Task count partitioned by status label. |
| `hi_nestor_health` | — | `1` if `GET ${NESTOR_URL}/v1/health` returned 200, else `0`. |
| `hi_nestor_research_active` | — | Active research jobs reported by Nestor `/v1/research/stats`. |
| `hi_nestor_research_completed` | — | Completed research jobs reported by Nestor `/v1/research/stats`. |
| `hi_nestor_research_pending` | — | Pending research jobs reported by Nestor `/v1/research/stats`. |

## NATS metrics (`nats_` prefix)

Sourced from NATS `/varz` and `/jsz` monitoring endpoints. Values reset on
NATS server restart, so these are gauges (no `_total` suffix).

| Metric | Description |
| --- | --- |
| `nats_connections` | Current client connections. |
| `nats_in_msgs` | Inbound message rate. |
| `nats_out_msgs` | Outbound message rate. |
| `nats_in_bytes` | Inbound bytes rate. |
| `nats_out_bytes` | Outbound bytes rate. |
| `nats_slow_consumers` | Current slow-consumer connections. |
| `nats_jetstream_streams` | Number of JetStream streams. |
| `nats_jetstream_consumers` | Number of JetStream consumers. |
| `nats_jetstream_messages` | Total messages stored across all JetStream streams. |
| `nats_jetstream_bytes` | Total bytes stored across all JetStream streams. |

## Exporter self-metrics (`homeric_exporter_` prefix)

| Metric | Labels | Description |
| --- | --- | --- |
| `homeric_exporter_scrape_timestamp_seconds` | — | Unix timestamp (seconds) when the last scrape completed. |
| `homeric_exporter_scrape_duration_seconds` | — | Wall-clock seconds spent in the last `collect()` call. |
| `homeric_exporter_fetch_errors` | `upstream` (`agamemnon`/`nestor`/`nats`) | Per-scrape count of upstream fetch failures. Gauge — resets each collection cycle. |
