#!/usr/bin/env python3
"""
homeric-exporter — Converts Agamemnon, Nestor, and NATS JSON APIs to Prometheus metrics.
Runs as a sidecar in the argus stack, exposes /metrics on port 9100.
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, HTTPServer

# LOG_LEVEL env var (default INFO) controls log verbosity at runtime so
# operators can flip to DEBUG (e.g. for HTTP access logs) without a redeploy.
# Accepts standard logging level names: DEBUG, INFO, WARNING, ERROR, CRITICAL.
#
# LOG_FORMAT env var (default json): "json" emits one JSON object per log line
# on stdout so Loki/Grafana can query fields like client_ip/path/status_code
# individually (`{container="argus-exporter"} | json | status=~"5.."`).
# "text" restores the previous plaintext format as a rollback escape hatch.
# In text mode the named logger keeps propagate=True so records fall through
# to root's basicConfig handler unchanged; in JSON mode it owns a dedicated
# handler and cuts propagation to avoid double emission.
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_LOG_FORMAT = os.environ.get("LOG_FORMAT", "json").lower()

# Top-level JSON keys this formatter owns; extras colliding with them get a
# ctx_ prefix so user context is never silently dropped.
_RESERVED_JSON_FIELDS = frozenset({
    "timestamp", "level", "logger", "message", "exception", "stack_info",
})


class _JsonFormatter(logging.Formatter):
    """Format each LogRecord as a single-line JSON object.

    Extras passed via ``extra=`` are flattened as top-level keys so they become
    individually queryable fields in Loki/Grafana. Keys colliding with the
    formatter's reserved fields get a ``ctx_`` prefix instead of being lost.
    """

    _DEFAULT_ATTRS: frozenset[str] = (
        frozenset(
            logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None).__dict__
        )
        | {"message", "msg", "args"}
    )

    def format(self, record: logging.LogRecord) -> str:
        """Render one record as compact JSON (UTF-8 friendly)."""
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            # getMessage() resolves lazy %-style args into the final message.
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        for key, value in record.__dict__.items():
            if key in _JsonFormatter._DEFAULT_ATTRS:
                continue
            out_key = f"ctx_{key}" if key in _RESERVED_JSON_FIELDS else key
            payload[out_key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


logging.basicConfig(level=_LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("homeric-exporter")

if _LOG_FORMAT == "json":
    # Attach the JSON formatter to the named logger only — third-party loggers
    # keep root's plaintext formatting. setLevel is required because once
    # propagation is cut, only this logger's own level gates emission.
    _json_handler = logging.StreamHandler()
    _json_handler.setFormatter(_JsonFormatter())
    log.addHandler(_json_handler)
    log.setLevel(_LOG_LEVEL)
    log.propagate = False

AGAMEMNON_URL     = os.environ.get("AGAMEMNON_URL",     "http://172.20.0.1:8080")
NESTOR_URL        = os.environ.get("NESTOR_URL",        "http://172.20.0.1:8081")
NATS_URL          = os.environ.get("NATS_URL",          "http://172.24.0.1:8222")
PORT              = int(os.environ.get("EXPORTER_PORT", "9100"))

# Optional CA bundle paths for TLS verification on each upstream.
# Set to the path of a CA certificate file (PEM) to enable custom trust.
# Leave unset to use the system trust store (appropriate when the upstream
# uses a publicly-trusted cert or when Tailscale handles transport encryption).
AGAMEMNON_TLS_CA  = os.environ.get("AGAMEMNON_TLS_CA")
NESTOR_TLS_CA     = os.environ.get("NESTOR_TLS_CA")
NATS_TLS_CA       = os.environ.get("NATS_TLS_CA")

# Set TLS_VERIFY=false to disable certificate verification entirely.
# Only for development — never disable in production.
_TLS_VERIFY       = os.environ.get("TLS_VERIFY", "true").lower() != "false"


def _build_ssl_context(ca_file: str | None = None) -> ssl.SSLContext | None:
    """Return an SSLContext for HTTPS requests, or None for plain HTTP."""
    if not _TLS_VERIFY:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if ca_file:
        ctx = ssl.create_default_context(cafile=ca_file)
        return ctx
    # No custom CA specified; use the system trust store (default urllib behaviour).
    return None


def _fetch(url: str, ca_file: str | None = None) -> dict | None:
    try:
        ctx = _build_ssl_context(ca_file)
        r = urllib.request.urlopen(url, timeout=5, context=ctx)
        return json.loads(r.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        log.warning("fetch %s failed: %s", url, e)
        return None


def _health_check(url: str, ca_file: str | None = None) -> int:
    """Return 1 if the URL returns HTTP 200, 0 otherwise."""
    try:
        ctx = _build_ssl_context(ca_file)
        r = urllib.request.urlopen(url, timeout=5, context=ctx)
        return 1 if r.status == 200 else 0
    except Exception as e:  # noqa: BLE001 - broad catch: probe must never propagate
        # Log at DEBUG so operators can distinguish a misconfigured URL from
        # a genuine upstream outage without changing the return-value contract.
        log.debug("health_check %s failed: %s", url, e)
        return 0


_FLEET_RESOURCES = ("workers", "sessions", "executions")
_FLEET_ACTIVITIES = ("model_working", "tool_running")
_FLEET_WORK_KINDS = ("issue", "interactive")
_FLEET_EXCLUSIONS = (
    "inactive_claim", "unobserved", "reconciliation_required", "stale",
    "invalid_timestamp", "generation_mismatch", "missing_worker", "invalid_record",
    "ambiguous_agent", "unknown_activity", "disconnected",
)
_FLEET_MAX_BYTES = 1024 * 1024


class _FleetNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the operator's credential, including to the same origin.
        return None


def _fleet_configuration() -> tuple[str, str] | None:
    key = os.environ.get("AGAMEMNON_API_KEY", "")
    if not re.fullmatch(r"[\x21-\x7e]{1,4096}", key):
        return None
    try:
        url = urllib.parse.urlsplit(AGAMEMNON_URL)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.password is not None
                or "?" in AGAMEMNON_URL or "#" in AGAMEMNON_URL
                or any(c.isspace() or ord(c) < 32 for c in AGAMEMNON_URL)):
            return None
        if url.port is not None and not 1 <= url.port <= 65535:
            return None
    except ValueError:
        return None
    return AGAMEMNON_URL.rstrip("/"), key


def _fleet_fetch(base: str, key: str, resource: str) -> list | None:
    """Read one bounded list without sharing authentication or an opener."""
    try:
        opener = urllib.request.build_opener(
            _FleetNoRedirect(),
            urllib.request.HTTPSHandler(context=_build_ssl_context(AGAMEMNON_TLS_CA)),
        )
        request = urllib.request.Request(
            f"{base}/v1/fleet/{resource}", headers={"Authorization": f"Bearer {key}"}, method="GET",
        )
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                return None
            body = response.read(_FLEET_MAX_BYTES + 1)
        if len(body) > _FLEET_MAX_BYTES:
            return None
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return None
        items, total = data["items"], data.get("total")
        if type(total) is not int or total != len(items) or not 0 <= total <= 1024:
            return None
        ids = [row["id"] for row in items if isinstance(row, dict) and isinstance(row.get("id"), str)]
        if len(ids) != len(set(ids)):
            return None
        return items
    except urllib.error.HTTPError as error:
        error.close()
    except (OSError, HTTPException, ValueError, RecursionError):
        # Fixed metric diagnostics carry failure; URLs, payloads and exception
        # strings can contain credentials or private work and are not logged.
        pass
    return None


def _fleet_string(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return 0 < len(value.encode("utf-8")) <= 1024
    except UnicodeEncodeError:
        return False


def _fleet_positive(value) -> bool:
    return type(value) is int and value > 0


def _fleet_record_valid(row, kind: str) -> bool:
    if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}", row["id"]) is None
            or row.get("schema") != "hi/fleet/v1" or row.get("kind") != kind
            or not _fleet_positive(row.get("generation"))):
        return False
    if kind == "workers":
        return True
    if not all(_fleet_string(row.get(field)) for field in ("workerId", "agentId", "workspace", "executionId")):
        return False
    if kind == "sessions" and row.get("sessionId") != row["id"]:
        return False
    if kind == "executions" and row["executionId"] != row["id"]:
        return False
    for field in ("sessionId", "taskId", "poolId"):
        if field in row and not _fleet_string(row[field]):
            return False
    return isinstance(row.get("status"), str) and row.get("claimStatus") in ("unclaimed", "reserved", "claimed", "released")


def _fleet_inactive(row: dict) -> bool:
    status = row["status"]
    consistent = ((row["claimStatus"] == "unclaimed" and status == "created")
                  or (row["claimStatus"] == "released" and status in ("completed", "failed", "cancelled", "interrupted")))
    return consistent and row.get("activity") in (None, "idle", "unknown", "disconnected")


def _fleet_reason(row: dict, workers: dict, now: float) -> str | None:
    if row["claimStatus"] in ("unclaimed", "released"):
        return "invalid_record"  # Consistent inactive rows were removed first.
    worker = workers.get(row["workerId"])
    if worker is None:
        return "missing_worker"
    if "poolId" in row and row["poolId"] != worker.get("poolId"):
        return "invalid_record"
    if row["generation"] != worker["generation"]:
        return "generation_mismatch"
    if row.get("observationState") == "reconciliation_required":
        return "reconciliation_required"
    if row["claimStatus"] != "claimed" or row.get("observationState") != "observed":
        return "unobserved"
    if not _fleet_positive(row.get("sourceSequence")):
        return "invalid_record"
    activity = row.get("activity")
    allowed_status = {
        "model_working": ("running", "cancelling", "interrupting"),
        "tool_running": ("running", "cancelling", "interrupting"),
        "idle": ("idle", "cancelling", "interrupting"),
        "waiting_input": ("waiting", "cancelling", "interrupting"),
        "waiting_approval": ("waiting", "cancelling", "interrupting"),
    }
    if not isinstance(activity, str):
        return "invalid_record"
    if activity in allowed_status and row["status"] not in allowed_status[activity]:
        return "invalid_record"
    ages = []
    for field in ("lastActivityAt", "lastActivityReceivedAt"):
        try:
            if not _fleet_string(row.get(field)):
                return "invalid_timestamp"
            stamp = datetime.fromisoformat(row[field])
            if stamp.tzinfo is None:
                return "invalid_timestamp"
            age = now - stamp.timestamp()
            if age < 0:
                return "invalid_timestamp"
            ages.append(age)
        except (ValueError, OverflowError, OSError):
            return "invalid_timestamp"
    if any(age > 60 for age in ages):
        return "stale"
    if activity == "disconnected":
        return "disconnected"
    if activity not in allowed_status:
        return "unknown_activity"
    return None


def _fleet_ambiguous(rows: list[dict]) -> set[int]:
    """Representations sharing an identity or exclusive claim must agree."""
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        for field in ("agentId", "executionId", "sessionId", "taskId", "workspace"):
            if field in row:
                groups[(field, row[field])].append(index)
    fields = ("agentId", "executionId", "workerId", "workspace", "generation", "taskId",
              "claimStatus", "status", "observationState", "activity", "sourceSequence",
              "lastActivityAt", "lastActivityReceivedAt")
    ambiguous = set()
    for indices in groups.values():
        first = rows[indices[0]]
        differs = any(any(row.get(field) != first.get(field) for field in fields)
                      for row in (rows[i] for i in indices[1:]))
        # Optional cross-links may be absent, but two supplied links must agree.
        for field in ("sessionId", "poolId"):
            values = {rows[i][field] for i in indices if field in rows[i]}
            differs = differs or len(values) > 1
        if differs:
            ambiguous.update(indices)
    return ambiguous


def _collect_fleet(gauge) -> None:
    enabled = os.environ.get("FLEET_METRICS_ENABLED", "false").lower() == "true"
    gauge("homeric_exporter_fleet_enabled", int(enabled))
    if not enabled:
        return
    lists = dict.fromkeys(_FLEET_RESOURCES)
    configuration = _fleet_configuration()
    if configuration is not None:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {name: pool.submit(_fleet_fetch, *configuration, name) for name in _FLEET_RESOURCES}
            lists = {name: future.result() for name, future in futures.items()}
    complete = all(value is not None for value in lists.values())
    for name, value in lists.items():
        gauge("homeric_exporter_fleet_fetch_success", int(value is not None), {"resource": name})
    exclusions = dict.fromkeys(_FLEET_EXCLUSIONS, 0)
    workers = {}
    rows = []
    for kind, items in lists.items():
        for row in items or []:
            if not _fleet_record_valid(row, kind):
                exclusions["invalid_record"] += 1
                complete = False
            elif kind == "workers":
                workers[row["id"]] = row
            elif _fleet_inactive(row):
                exclusions["inactive_claim"] += 1
            else:
                rows.append(row)
    ambiguous = _fleet_ambiguous(rows)
    counts = {(activity, kind): set() for activity in _FLEET_ACTIVITIES for kind in _FLEET_WORK_KINDS}
    now = time.time()
    for index, row in enumerate(rows):
        reason = "ambiguous_agent" if index in ambiguous else _fleet_reason(row, workers, now)
        if reason:
            exclusions[reason] += 1
            complete = False
        elif row["activity"] in _FLEET_ACTIVITIES:
            kind = "issue" if "taskId" in row else "interactive"
            counts[row["activity"], kind].add(row["agentId"])
    gauge("hi_fleet_activity_complete", int(complete))
    if complete:
        for (activity, kind), agents in counts.items():
            gauge("hi_fleet_recently_observed_active_agents", len(agents), {"activity": activity, "work_kind": kind})
    for reason, count in exclusions.items():
        gauge("hi_fleet_observation_exclusions", count, {"reason": reason})


_METRIC_HELP: dict[str, str] = {
    "hi_agamemnon_health":                    "1 if Agamemnon /v1/health returned HTTP 200, 0 otherwise",
    "hi_agents_count":                        "Number of agents registered in Agamemnon",
    "hi_agents_online":                       "Number of agents with status=online",
    "hi_agents_offline":                      "Number of agents with status!=online",
    "hi_agent_online":                        "1 if this individual agent is online, 0 otherwise",
    "hi_tasks_count":                         "Number of tasks known to Agamemnon",
    # Deprecated aliases (#426): gauges must not carry the counter-reserved
    # _total suffix. Kept for one scrape-retention window so live Prometheus
    # data survives the rename; removal tracked as a follow-up.
    "hi_agents_total":                        "(deprecated, use hi_agents_count) Number of agents registered in Agamemnon",
    "hi_tasks_total":                         "(deprecated, use hi_tasks_count) Number of tasks known to Agamemnon",
    "hi_tasks_by_status":                     "Task count grouped by status label",
    "hi_nestor_health":                       "1 if Nestor /v1/health returned HTTP 200, 0 otherwise",
    "hi_nestor_research_active":              "Number of active research jobs in Nestor",
    "hi_nestor_research_completed":           "Number of completed research jobs in Nestor",
    "hi_nestor_research_pending":             "Number of pending research jobs in Nestor",
    "nats_connections":                       "Current number of client connections to NATS",
    "nats_in_msgs":                           "Current inbound message rate from NATS server",
    "nats_out_msgs":                          "Current outbound message rate from NATS server",
    "nats_in_bytes":                          "Current inbound bytes rate from NATS server",
    "nats_out_bytes":                         "Current outbound bytes rate from NATS server",
    "nats_slow_consumers":                    "Current number of slow consumers on NATS",
    "nats_jetstream_streams":                 "Number of JetStream streams",
    "nats_jetstream_consumers":               "Number of JetStream consumers",
    "nats_jetstream_messages":                "Number of messages stored in JetStream",
    "nats_jetstream_bytes":                   "Bytes stored in JetStream",
    "homeric_exporter_scrape_timestamp_seconds": "Unix timestamp (seconds) when the last scrape completed",
    "homeric_exporter_scrape_duration_seconds":  "Wall-clock seconds spent in the last collect() call",
    "homeric_exporter_fetch_errors":          "Number of upstream fetch failures per scrape, by upstream",
    "homeric_exporter_fleet_enabled": "1 if Fleet observation collection is enabled, 0 otherwise",
    "homeric_exporter_fleet_fetch_success": "1 if this bounded Fleet list read succeeded, 0 otherwise",
    "hi_fleet_activity_complete": "1 if all potentially admitted Fleet records can be classified, 0 otherwise",
    "hi_fleet_recently_observed_active_agents": "Distinct logical agents with recent qualifying model or tool activity",
    "hi_fleet_observation_exclusions": "Fleet input records excluded per scrape, by fixed reason",
}


def collect() -> str:
    start = time.time()
    lines: list[str] = []
    emitted_types: set[str] = set()

    def gauge(name: str, value: float, labels: dict | None = None) -> None:
        lstr = ",".join(f'{k}="{v}"' for k, v in (labels or {}).items())
        if name not in emitted_types:
            help_text = _METRIC_HELP.get(name, "")
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            emitted_types.add(name)
        lines.append(f"{name}{{{lstr}}} {value}")

    # ── Parallelise all independent upstream fetches ──────────────────────
    with ThreadPoolExecutor(max_workers=7) as pool:
        f_agamemnon_health = pool.submit(_health_check, f"{AGAMEMNON_URL}/v1/health", AGAMEMNON_TLS_CA)
        f_agents           = pool.submit(_fetch,        f"{AGAMEMNON_URL}/v1/agents", AGAMEMNON_TLS_CA)
        f_tasks            = pool.submit(_fetch,        f"{AGAMEMNON_URL}/v1/tasks",  AGAMEMNON_TLS_CA)
        f_nestor_health    = pool.submit(_health_check, f"{NESTOR_URL}/v1/health",    NESTOR_TLS_CA)
        f_nestor_stats     = pool.submit(_fetch,        f"{NESTOR_URL}/v1/research/stats", NESTOR_TLS_CA)
        f_nats_varz        = pool.submit(_fetch,        f"{NATS_URL}/varz",           NATS_TLS_CA)
        f_nats_jsz         = pool.submit(_fetch,        f"{NATS_URL}/jsz",            NATS_TLS_CA)
        # Resolve all futures before building metric lines
        agamemnon_health = f_agamemnon_health.result()
        agents_data      = f_agents.result()
        tasks_data       = f_tasks.result()
        nestor_health    = f_nestor_health.result()
        nestor_stats     = f_nestor_stats.result()
        nats_varz        = f_nats_varz.result()
        nats_jsz         = f_nats_jsz.result()

    # ── Tally fetch errors per upstream ───────────────────────────────────
    fetch_errors: dict[str, int] = {
        "agamemnon": int(agents_data is None) + int(tasks_data is None),
        "nestor":    int(nestor_stats is None),
        "nats":      int(nats_varz is None) + int(nats_jsz is None),
    }

    # ── Agamemnon health ───────────────────────────────────────────────────
    gauge("hi_agamemnon_health", agamemnon_health)

    # ── Agamemnon agents ───────────────────────────────────────────────────
    # Reuse agents_data from the parallel batch above. A second serial fetch
    # here once doubled worst-case /metrics latency past Prometheus'
    # scrape_timeout (up == 0 with dead upstreams) — closes #623.
    d = agents_data
    if d:
        agents = d.get("agents", [])
        total   = len(agents)
        online  = sum(1 for a in agents if a.get("status") == "online")
        offline = total - online
        gauge("hi_agents_count",   total)
        gauge("hi_agents_total",   total)
        gauge("hi_agents_online",  online)
        gauge("hi_agents_offline", offline)
        for ag in agents:
            gauge("hi_agent_online",
                  1 if ag.get("status") == "online" else 0,
                  {"name":    ag.get("name", "unknown"),
                   "host":    ag.get("host", "unknown"),
                   "program": ag.get("program", "unknown")})

    # ── Agamemnon tasks ────────────────────────────────────────────────────
    if tasks_data:
        tasks = tasks_data.get("tasks", [])
        gauge("hi_tasks_count", len(tasks))
        gauge("hi_tasks_total", len(tasks))
        status_counts: dict[str, int] = {}
        for task in tasks:
            s = task.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1
        for status, count in status_counts.items():
            gauge("hi_tasks_by_status", count, {"status": status})

    # ── Nestor health + research stats ────────────────────────────────────
    gauge("hi_nestor_health", nestor_health)

    if nestor_stats:
        gauge("hi_nestor_research_active",    nestor_stats.get("active", 0))
        gauge("hi_nestor_research_completed", nestor_stats.get("completed", 0))
        gauge("hi_nestor_research_pending",   nestor_stats.get("pending", 0))

    # ── NATS ───────────────────────────────────────────────────────────────
    if nats_varz:
        gauge("nats_connections",    nats_varz.get("connections", 0))
        gauge("nats_in_msgs",        nats_varz.get("in_msgs", 0))
        gauge("nats_out_msgs",       nats_varz.get("out_msgs", 0))
        gauge("nats_in_bytes",       nats_varz.get("in_bytes", 0))
        gauge("nats_out_bytes",      nats_varz.get("out_bytes", 0))
        gauge("nats_slow_consumers", nats_varz.get("slow_consumers", 0))

    if nats_jsz:
        gauge("nats_jetstream_streams",   nats_jsz.get("streams", 0))
        gauge("nats_jetstream_consumers", nats_jsz.get("consumers", 0))
        gauge("nats_jetstream_messages",  nats_jsz.get("messages", 0))
        gauge("nats_jetstream_bytes",     nats_jsz.get("bytes", 0))

    _collect_fleet(gauge)

    # ── exporter self ──────────────────────────────────────────────────────
    gauge("homeric_exporter_scrape_timestamp_seconds", time.time())
    gauge("homeric_exporter_scrape_duration_seconds",  time.time() - start)
    for upstream, count in fetch_errors.items():
        gauge("homeric_exporter_fetch_errors", count, {"upstream": upstream})

    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/metrics":
            body = collect().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Emit an access-log record with structured, individually queryable fields.

        Overrides BaseHTTPRequestHandler.log_request so client IP, method,
        path, status, and response size travel as ``extra=`` fields (rendered
        as top-level JSON keys) instead of being baked into the message text.
        """
        requestline: str = getattr(self, "requestline", "")
        safe_requestline = requestline.replace("\r", " ").replace("\n", " ")
        safe_method = safe_requestline.split(" ", 1)[0]
        safe_path = self.path.split("?", 1)[0].replace("\r", " ").replace("\n", " ")
        address = getattr(self, "client_address", None)
        log.debug(
            "%s %s",
            safe_requestline,
            code,
            extra={
                "client_ip": str(address[0]) if address else "-",
                "method": safe_method,
                "path": safe_path,
                "status_code": str(code),
                "response_bytes": str(size),
            },
        )

    def log_message(self, fmt: str, *args: object) -> None:
        log.debug(fmt, *args)


if __name__ == "__main__":  # pragma: no cover
    log.info("homeric-exporter starting on port %d", PORT)
    log.info("Scraping Agamemnon at %s (CA: %s)", AGAMEMNON_URL, AGAMEMNON_TLS_CA or "system trust store")
    log.info("Scraping Nestor at %s (CA: %s)", NESTOR_URL, NESTOR_TLS_CA or "system trust store")
    log.info("Scraping NATS at %s (CA: %s)", NATS_URL, NATS_TLS_CA or "system trust store")
    if not _TLS_VERIFY:
        log.warning("TLS certificate verification is DISABLED (TLS_VERIFY=false)")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # nosec B104
