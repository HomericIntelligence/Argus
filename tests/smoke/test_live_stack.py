"""Live-stack smoke tests: exporter → Prometheus → queryable metric.

Validates the full scrape pipeline against the running docker-compose stack
(the regression class from #35/#210, where a dashboard referenced
``nats_in_msgs_total`` while the exporter emits the gauge ``nats_in_msgs``).

Gating (skip-when-unset, hard-fail-when-set): tests are skipped unless
``ARGUS_SMOKE_STACK=1``. ``scripts/smoke-stack.sh`` sets that variable after
bringing the stack up with ``docker compose up -d --wait``, so an unreachable
Prometheus inside the gated run is a hard failure, never a silent pass.

Dashboard cross-check universe: a dashboard identifier is considered valid if
it appears in *any* of

1. Prometheus's queryable metric names (``/api/v1/label/__name__/values``),
2. the exporter's live ``/metrics`` output on loopback, or
3. the pipeline source declarations (exporter ``_METRIC_HELP`` keys and
   string literals in ``jetstream-consumer/consumer.py``).

Layer 3 exists because some metrics are emitted only once real data flows:
the consumer emits ``hi_jetstream_events_total`` only after its first JetStream
event, so on a freshly booted stack with no events those panels legitimately
render empty. A name declared in source but never emitted is not drift; a name
referenced by a dashboard but declared nowhere is exactly the #35 drift class.

Stdlib-only per house convention (mirrors exporter/exporter.py).
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
PROM_URL = os.environ.get("PROM_URL", "https://127.0.0.1:9090")
EXPORTER_METRICS_URL = os.environ.get(
    "EXPORTER_METRICS_URL", "http://127.0.0.1:9100/metrics"
)
DASHBOARDS_DIR = ROOT / "dashboards"

# Jobs that must report up == 1 without external upstreams reachable.
# Excluded deliberately:
#   jetstream-consumer — it does come up in CI (it serves /metrics whether or
#     not NATS is reachable, since its NATS_URL is a host-gateway address), but
#     it emits no event metrics until a real event flows, so it is covered by
#     the source-declaration layer below rather than by a scrape assertion.
#   nomad              — gateway target; no Nomad agent in CI.
REQUIRED_JOBS = ("homeric-exporter", "prometheus", "alertmanager", "atlas")

# Metric identifiers of interest inside dashboard PromQL / pipeline sources.
METRIC_IDENT_RE = re.compile(r"\b(?:hi_|nats_|homeric_exporter_)[a-z0-9_]*")

# `up` is a sampled gauge: a scrape that failed during startup is recorded as
# 0 and stays 0 for a whole scrape_interval, even after the target recovers.
# Asserting it once, immediately after the stack reports ready, therefore
# tests the poll timing rather than the pipeline. Poll to a deadline instead.
SETTLE_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 5

# Counter/histogram family suffixes that may or may not be present on either
# side of the dashboard-vs-pipeline comparison (e.g. rate(X_total[1m]) vs X).
_FAMILY_SUFFIXES = ("_total", "_bucket", "_sum", "_count")

pytestmark = [
    pytest.mark.live_stack,
    pytest.mark.skipif(
        os.environ.get("ARGUS_SMOKE_STACK") != "1",
        reason="requires the live docker-compose stack; "
        "run via scripts/smoke-stack.sh (sets ARGUS_SMOKE_STACK=1)",
    ),
]


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context trusting the repo's self-signed CA."""
    return ssl.create_default_context(cafile=str(ROOT / "certs" / "ca.crt"))


def _get(url: str, timeout: float = 10.0) -> bytes:
    """GET a URL, using the CA-pinned context for https:// endpoints."""
    ctx = _ssl_context() if url.startswith("https://") else None
    with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _prom_query(promql: str) -> list[dict]:
    """Run an instant PromQL query and return the result series list."""
    body = _get(f"{PROM_URL}/api/v1/query?query={urllib.parse.quote(promql)}")
    data = json.loads(body)
    if data["status"] != "success":
        raise AssertionError(f"Prometheus query failed for {promql!r}: {data}")
    return list(data["data"]["result"])


def _poll_until(check, describe_failure):
    """Poll ``check`` until it returns a truthy value or the deadline passes.

    Returns the truthy value. On timeout, calls ``describe_failure`` (which
    raises) so the assertion message carries Prometheus' own ``lastError``
    rather than a bare "expected 1, got 0".
    """
    deadline = time.monotonic() + SETTLE_TIMEOUT_SECONDS
    result = check()
    while not result and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        result = check()
    if not result:
        describe_failure()
    return result


def _target_diagnostics() -> str:
    """Render Prometheus target health, including each failing lastError."""
    try:
        targets = json.loads(_get(f"{PROM_URL}/api/v1/targets"))["data"]["activeTargets"]
    except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the failure
        return f"(could not read {PROM_URL}/api/v1/targets: {exc})"
    lines = []
    for target in targets:
        lines.append(
            f"  {target['labels'].get('job', '?')}: health={target['health']} "
            f"lastError={target.get('lastError') or '-'}"
        )
    return "\n".join(lines)


def _up_by_job() -> dict:
    return {s["metric"].get("job"): s["value"][1] for s in _prom_query("up")}


def _normalize(name: str) -> str:
    """Strip counter/histogram family suffixes for drift-tolerant comparison."""
    for suffix in _FAMILY_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _dashboard_metric_identifiers() -> set[str]:
    """Extract hi_/nats_/homeric_exporter_ identifiers from every dashboard expr."""
    idents: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            expr = node.get("expr")
            if isinstance(expr, str):
                idents.update(METRIC_IDENT_RE.findall(expr))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for path in sorted(DASHBOARDS_DIR.glob("*.json")):
        walk(json.loads(path.read_text()))
    return idents


def _pipeline_source_metric_names() -> set[str]:
    """Collect metric names declared in exporter and jetstream-consumer sources."""
    names: set[str] = set()
    sources = (
        ROOT / "exporter" / "exporter.py",
        ROOT / "jetstream-consumer" / "consumer.py",
    )
    for src in sources:
        names.update(METRIC_IDENT_RE.findall(src.read_text()))
    return names


def test_prometheus_ready() -> None:
    """Prometheus answers /-/ready over its CA-pinned HTTPS endpoint."""
    body = _get(f"{PROM_URL}/-/ready").decode()
    assert "Ready" in body


def test_required_jobs_up() -> None:
    """Every job that serves /metrics without external upstreams reports up == 1."""

    def all_required_up() -> bool:
        observed = _up_by_job()
        return all(observed.get(job) == "1" for job in REQUIRED_JOBS)

    def fail() -> None:
        observed = _up_by_job()
        missing = [job for job in REQUIRED_JOBS if job not in observed]
        down = [job for job in REQUIRED_JOBS if observed.get(job) != "1"]
        raise AssertionError(
            f"required jobs not up after {SETTLE_TIMEOUT_SECONDS}s: "
            f"absent={missing} down={down} (all jobs: {observed})\n"
            f"prometheus targets:\n{_target_diagnostics()}"
        )

    _poll_until(all_required_up, fail)


def test_exporter_self_metrics_queryable() -> None:
    """The exporter → Prometheus hop returns real values, not just an up state."""

    def exporter_samples_present() -> bool:
        return len(_prom_query("homeric_exporter_scrape_timestamp_seconds > 0")) >= 1

    def fail() -> None:
        raise AssertionError(
            "homeric_exporter_scrape_timestamp_seconds > 0 returned no series "
            f"after {SETTLE_TIMEOUT_SECONDS}s; Prometheus is not storing exporter "
            f"samples. A scrape that exceeds Prometheus' 10s scrape_timeout is "
            f"recorded as up == 0, which points at collect() latency.\n"
            f"prometheus targets:\n{_target_diagnostics()}"
        )

    _poll_until(exporter_samples_present, fail)


def test_dashboard_metrics_exist_in_pipeline() -> None:
    """Every dashboard-referenced metric name resolves against the pipeline."""
    prometheus_names = set(json.loads(_get(f"{PROM_URL}/api/v1/label/__name__/values"))["data"])
    universe = {name for name in prometheus_names}

    # A cold exporter against unreachable upstreams spends one 5s upstream
    # timeout per collect(); give the fetch room and turn a timeout into a
    # diagnosable failure rather than a bare socket TimeoutError.
    try:
        exporter_body = _get(EXPORTER_METRICS_URL, timeout=30.0).decode()
    except (TimeoutError, OSError) as exc:
        raise AssertionError(
            f"exporter /metrics at {EXPORTER_METRICS_URL} did not answer within "
            f"30s ({exc}). Prometheus' scrape_timeout is 10s, so a slow "
            f"collect() also shows up as up == 0.\n"
            f"prometheus targets:\n{_target_diagnostics()}"
        ) from exc
    for line in exporter_body.splitlines():
        if line.startswith("#"):
            continue
        match = re.match(r"[a-zA-Z_:][a-zA-Z0-9_:]*", line)
        if match:
            universe.add(match.group(0))

    universe |= _pipeline_source_metric_names()

    normalized_universe = {_normalize(name) for name in universe}
    missing = sorted(
        ident
        for ident in _dashboard_metric_identifiers()
        if _normalize(ident) not in normalized_universe
    )
    assert not missing, (
        "dashboards reference metric names the pipeline does not declare or "
        f"expose (the #35 drift class): {missing}"
    )
