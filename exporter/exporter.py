#!/usr/bin/env python3
"""
homeric-exporter — Converts Agamemnon, Nestor, and NATS JSON APIs to Prometheus metrics.
Runs as a sidecar in the argus stack, exposes /metrics on port 9100.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("homeric-exporter")

AGAMEMNON_URL = os.environ.get("AGAMEMNON_URL", "http://172.20.0.1:8080")
NESTOR_URL    = os.environ.get("NESTOR_URL",    "http://172.20.0.1:8081")
NATS_URL      = os.environ.get("NATS_URL",      "http://172.24.0.1:8222")
PORT          = int(os.environ.get("EXPORTER_PORT", "9100"))

_raw_timeout = os.environ.get("SCRAPE_TIMEOUT", "5")
try:
    SCRAPE_TIMEOUT: float = float(_raw_timeout)
except ValueError:
    log.warning("SCRAPE_TIMEOUT=%r is not numeric; falling back to 5", _raw_timeout)
    SCRAPE_TIMEOUT = 5.0

for _var, _val in (("AGAMEMNON_URL", AGAMEMNON_URL),
                   ("NESTOR_URL",    NESTOR_URL),
                   ("NATS_URL",      NATS_URL)):
    if not _val:
        log.warning("environment variable %s is empty; scrapes against this target will fail", _var)


def _fetch(url: str) -> dict | None:
    try:
        r = urllib.request.urlopen(url, timeout=SCRAPE_TIMEOUT)
        return json.loads(r.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        log.warning("fetch %s failed: %s", url, e)
        return None


def _health_check(url: str) -> int:
    """Return 1 if the URL returns HTTP 200, 0 otherwise."""
    try:
        r = urllib.request.urlopen(url, timeout=SCRAPE_TIMEOUT)
        return 1 if r.status == 200 else 0
    except Exception:  # broad catch: probe must never propagate
        return 0


def collect() -> str:
    start = time.time()
    lines: list[str] = []
    emitted_types: set[str] = set()

    def gauge(name: str, help: str, value: float | int, labels: dict | None = None) -> None:
        lstr = ",".join(f'{k}="{v}"' for k, v in (labels or {}).items())
        if name not in emitted_types:
            lines.append(f"# HELP {name} {help}")
            lines.append(f"# TYPE {name} gauge")
            emitted_types.add(name)
        lines.append(f"{name}{{{lstr}}} {value}")

    # ── Parallelise all independent upstream fetches ──────────────────────
    with ThreadPoolExecutor(max_workers=7) as pool:
        f_agamemnon_health = pool.submit(_health_check, f"{AGAMEMNON_URL}/v1/health")
        f_agents           = pool.submit(_fetch,        f"{AGAMEMNON_URL}/v1/agents")
        f_tasks            = pool.submit(_fetch,        f"{AGAMEMNON_URL}/v1/tasks")
        f_nestor_health    = pool.submit(_health_check, f"{NESTOR_URL}/v1/health")
        f_nestor_stats     = pool.submit(_fetch,        f"{NESTOR_URL}/v1/research/stats")
        f_nats_varz        = pool.submit(_fetch,        f"{NATS_URL}/varz")
        f_nats_jsz         = pool.submit(_fetch,        f"{NATS_URL}/jsz")
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
    gauge("hi_agamemnon_health", "1 if Agamemnon /v1/health returned HTTP 200, 0 otherwise", agamemnon_health)

    # ── Agamemnon agents ───────────────────────────────────────────────────
    if agents_data:
        agents = agents_data.get("agents", [])
        total   = len(agents)
        online  = sum(1 for a in agents if a.get("status") == "online")
        offline = total - online
        gauge("hi_agents_total",   "Total number of agents registered in Agamemnon", total)
        gauge("hi_agents_online",  "Number of agents with status=online", online)
        gauge("hi_agents_offline", "Number of agents with status!=online", offline)
        for ag in agents:
            gauge("hi_agent_online",
                  "1 if the individual agent is online, 0 otherwise",
                  1 if ag.get("status") == "online" else 0,
                  {"name":    ag.get("name", "unknown"),
                   "host":    ag.get("host", "unknown"),
                   "program": ag.get("program", "unknown")})

    # ── Agamemnon tasks ────────────────────────────────────────────────────
    if tasks_data:
        tasks = tasks_data.get("tasks", [])
        gauge("hi_tasks_total", "Total number of tasks known to Agamemnon", len(tasks))
        status_counts: dict[str, int] = {}
        for task in tasks:
            s = task.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1
        for status, count in status_counts.items():
            gauge("hi_tasks_by_status", "Task count partitioned by status label", count, {"status": status})

    # ── Nestor health + research stats ────────────────────────────────────
    gauge("hi_nestor_health", "1 if Nestor /v1/health returned HTTP 200, 0 otherwise", nestor_health)

    if nestor_stats:
        gauge("hi_nestor_research_active",    "Number of research jobs currently active in Nestor",    nestor_stats.get("active", 0))
        gauge("hi_nestor_research_completed", "Number of research jobs completed in Nestor",           nestor_stats.get("completed", 0))
        gauge("hi_nestor_research_pending",   "Number of research jobs pending in Nestor",             nestor_stats.get("pending", 0))

    # ── NATS ───────────────────────────────────────────────────────────────
    if nats_varz:
        gauge("nats_connections",    "Current number of client connections to NATS",              nats_varz.get("connections", 0))
        gauge("nats_in_msgs_total",  "Total inbound messages received by NATS since start",       nats_varz.get("in_msgs", 0))
        gauge("nats_out_msgs_total", "Total outbound messages sent by NATS since start",          nats_varz.get("out_msgs", 0))
        gauge("nats_in_bytes_total", "Total inbound bytes received by NATS since start",          nats_varz.get("in_bytes", 0))
        gauge("nats_out_bytes_total","Total outbound bytes sent by NATS since start",             nats_varz.get("out_bytes", 0))
        gauge("nats_slow_consumers", "Number of slow consumer connections detected by NATS",      nats_varz.get("slow_consumers", 0))

    if nats_jsz:
        gauge("nats_jetstream_streams",   "Number of JetStream streams",                              nats_jsz.get("streams", 0))
        gauge("nats_jetstream_consumers", "Number of JetStream consumers",                            nats_jsz.get("consumers", 0))
        gauge("nats_jetstream_messages",  "Total messages stored across all JetStream streams",       nats_jsz.get("messages", 0))
        gauge("nats_jetstream_bytes",     "Total bytes stored across all JetStream streams",          nats_jsz.get("bytes", 0))

    # ── exporter self ──────────────────────────────────────────────────────
    gauge("homeric_exporter_scrape_timestamp",        "Unix timestamp when the last scrape completed",              time.time())
    gauge("homeric_exporter_scrape_duration_seconds", "Duration in seconds of the last upstream scrape cycle",     time.time() - start)
    for upstream, count in fetch_errors.items():
        gauge("homeric_exporter_fetch_errors_total",  "Number of fetch failures per upstream service",             count, {"upstream": upstream})

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


if __name__ == "__main__":
    log.info("homeric-exporter starting on port %d", PORT)
    log.info("Scraping Agamemnon at %s", AGAMEMNON_URL)
    log.info("Scraping Nestor at %s", NESTOR_URL)
    log.info("Scraping NATS at %s", NATS_URL)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)

    def _shutdown(signum, frame):
        sig_name = signal.Signals(signum).name
        log.info("received %s — shutting down gracefully", sig_name)
        t = threading.Thread(target=server.shutdown, daemon=True)
        t.start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server.serve_forever()
    log.info("homeric-exporter stopped cleanly")
