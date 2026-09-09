#!/usr/bin/env python3
"""
homeric-exporter — Converts Agamemnon, Nestor, and NATS JSON APIs to Prometheus metrics.
Runs as a sidecar in the argus stack, exposes /metrics on port 9100.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

# LOG_LEVEL env var (default INFO) controls log verbosity at runtime so
# operators can flip to DEBUG (e.g. for HTTP access logs) without a redeploy.
# Accepts standard logging level names: DEBUG, INFO, WARNING, ERROR, CRITICAL.
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=_LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("homeric-exporter")

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


_METRIC_HELP: dict[str, str] = {
    "hi_agamemnon_health":                    "1 if Agamemnon /v1/health returned HTTP 200, 0 otherwise",
    "hi_agents_total":                        "Total number of agents registered in Agamemnon",
    "hi_agents_online":                       "Number of agents with status=online",
    "hi_agents_offline":                      "Number of agents with status!=online",
    "hi_agent_online":                        "1 if this individual agent is online, 0 otherwise",
    "hi_tasks_total":                         "Total number of tasks known to Agamemnon",
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
    d = _fetch(f"{AGAMEMNON_URL}/v1/agents", AGAMEMNON_TLS_CA)
    if d:
        agents = d.get("agents", [])
        total   = len(agents)
        online  = sum(1 for a in agents if a.get("status") == "online")
        offline = total - online
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
