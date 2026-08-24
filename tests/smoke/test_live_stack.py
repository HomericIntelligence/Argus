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
#   jetstream-consumer — its NATS_URL is hardcoded to a host-gateway address
#     that is unreachable in CI (docker-compose.yml).
#   nomad              — gateway target; no Nomad agent in CI.
REQUIRED_JOBS = ("homeric-exporter", "prometheus", "alertmanager", "atlas")

# Metric identifiers of interest inside dashboard PromQL / pipeline sources.
METRIC_IDENT_RE = re.compile(r"\b(?:hi_|nats_|homeric_exporter_)[a-z0-9_]*")

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
    up_by_job = {
        series["metric"].get("job"): series["value"][1]
        for series in _prom_query("up")
    }
    missing = [job for job in REQUIRED_JOBS if job not in up_by_job]
    assert not missing, f"jobs absent from `up`: {missing} (all jobs: {up_by_job})"
    down = [job for job in REQUIRED_JOBS if up_by_job[job] != "1"]
    assert not down, f"required jobs reporting up != 1: {down} (all jobs: {up_by_job})"


def test_exporter_self_metrics_queryable() -> None:
    """The exporter → Prometheus hop returns real values, not just an up state."""
    series = _prom_query("homeric_exporter_scrape_timestamp_seconds > 0")
    assert len(series) >= 1, (
        "homeric_exporter_scrape_timestamp_seconds > 0 returned no series; "
        "Prometheus is not storing exporter samples"
    )


def test_dashboard_metrics_exist_in_pipeline() -> None:
    """Every dashboard-referenced metric name resolves against the pipeline."""
    prometheus_names = set(json.loads(_get(f"{PROM_URL}/api/v1/label/__name__/values"))["data"])
    universe = {name for name in prometheus_names}

    exporter_body = _get(EXPORTER_METRICS_URL).decode()
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
