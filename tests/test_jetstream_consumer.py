"""
Unit tests for jetstream-consumer/consumer.py.
Tests pure helper functions and metric rendering without a live NATS connection.
"""
from __future__ import annotations

import importlib
import json
import sys
import threading
import time
import types
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub out the `nats` package so consumer.py can be imported without
# installing nats-py in the test environment.
# ---------------------------------------------------------------------------

def _make_nats_stub() -> types.ModuleType:
    nats_mod = types.ModuleType("nats")
    nats_mod.connect = MagicMock()

    errors_mod = types.ModuleType("nats.errors")

    class TimeoutError(Exception):
        pass

    errors_mod.TimeoutError = TimeoutError
    nats_mod.errors = errors_mod
    sys.modules["nats.errors"] = errors_mod

    js_errors_mod = types.ModuleType("nats.js.errors")

    class NotFoundError(Exception):
        pass

    js_errors_mod.NotFoundError = NotFoundError
    nats_mod.js = types.ModuleType("nats.js")
    nats_mod.js.errors = js_errors_mod
    sys.modules["nats.js"] = nats_mod.js
    sys.modules["nats.js.errors"] = js_errors_mod

    return nats_mod


@pytest.fixture(autouse=True, scope="module")
def _stub_nats():
    """Install the nats stub before importing the consumer module."""
    nats_stub = _make_nats_stub()
    sys.modules["nats"] = nats_stub
    yield
    # Leave stub in place — other tests in the same session may need it.


@pytest.fixture(autouse=True, scope="module")
def consumer():
    """Import the consumer module once after the nats stub is in place."""
    spec = importlib.util.spec_from_file_location(
        "consumer",
        "/home/mvillmow/Projects/ProjectArgus/.worktrees/issue-4/jetstream-consumer/consumer.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["consumer"] = mod
    return mod


# ---------------------------------------------------------------------------
# Helper: reset shared state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state(consumer):
    with consumer._lock:
        consumer._event_counts.clear()
        consumer._last_seq.clear()
        consumer._latency_accum.clear()
        consumer._scrape_ts = 0.0
        consumer._connected = 0
    yield


# ---------------------------------------------------------------------------
# _subject_prefix
# ---------------------------------------------------------------------------

class TestSubjectPrefix:
    def test_two_part(self, consumer):
        assert consumer._subject_prefix("hi.agents") == "hi.agents"

    def test_three_part(self, consumer):
        assert consumer._subject_prefix("hi.agents.created") == "hi.agents"

    def test_deep_subject(self, consumer):
        assert consumer._subject_prefix("hi.tasks.foo.bar") == "hi.tasks"

    def test_single_segment(self, consumer):
        assert consumer._subject_prefix("hi") == "hi"

    def test_empty(self, consumer):
        assert consumer._subject_prefix("") == ""


# ---------------------------------------------------------------------------
# _event_type
# ---------------------------------------------------------------------------

class TestEventType:
    @pytest.mark.parametrize("subject,expected", [
        ("hi.agents.created", "created"),
        ("hi.tasks.completed", "completed"),
        ("hi.tasks.failed", "failed"),
    ])
    def test_known_types(self, consumer, subject, expected):
        assert consumer._event_type(subject) == expected

    def test_short_subject_returns_unknown(self, consumer):
        assert consumer._event_type("hi.agents") == "unknown"

    def test_deep_subject_uses_third_token(self, consumer):
        assert consumer._event_type("hi.tasks.updated.extra") == "updated"


# ---------------------------------------------------------------------------
# _update_event
# ---------------------------------------------------------------------------

class TestUpdateEvent:
    def test_increments_count(self, consumer):
        consumer._update_event("hi.agents.created", seq=1, stream="hi_agents")
        with consumer._lock:
            assert consumer._event_counts[("hi.agents", "created")] == 1

    def test_accumulates_multiple(self, consumer):
        for _ in range(5):
            consumer._update_event("hi.tasks.completed", seq=1, stream="hi_tasks")
        with consumer._lock:
            assert consumer._event_counts[("hi.tasks", "completed")] == 5

    def test_tracks_last_seq(self, consumer):
        consumer._update_event("hi.agents.online", seq=42, stream="hi_agents")
        with consumer._lock:
            assert consumer._last_seq["hi_agents"] == 42

    def test_last_seq_only_advances(self, consumer):
        consumer._update_event("hi.agents.online", seq=10, stream="hi_agents")
        consumer._update_event("hi.agents.online", seq=5, stream="hi_agents")
        with consumer._lock:
            assert consumer._last_seq["hi_agents"] == 10

    def test_different_streams_independent(self, consumer):
        consumer._update_event("hi.agents.created", seq=3, stream="hi_agents")
        consumer._update_event("hi.tasks.created", seq=7, stream="hi_tasks")
        with consumer._lock:
            assert consumer._last_seq["hi_agents"] == 3
            assert consumer._last_seq["hi_tasks"] == 7


# ---------------------------------------------------------------------------
# _update_task_latency
# ---------------------------------------------------------------------------

class TestUpdateTaskLatency:
    def _payload(self, **kwargs) -> bytes:
        return json.dumps(kwargs).encode()

    def test_computes_latency(self, consumer):
        payload = self._payload(status="completed", created_at=1000.0, completed_at=1010.0)
        consumer._update_task_latency(payload, "hi.tasks.completed")
        with consumer._lock:
            total, count = consumer._latency_accum["completed"]
        assert count == 1
        assert total == pytest.approx(10.0)

    def test_running_mean_accumulates(self, consumer):
        consumer._update_task_latency(
            self._payload(status="completed", created_at=0.0, completed_at=10.0),
            "hi.tasks.completed",
        )
        consumer._update_task_latency(
            self._payload(status="completed", created_at=0.0, completed_at=30.0),
            "hi.tasks.completed",
        )
        with consumer._lock:
            total, count = consumer._latency_accum["completed"]
        assert count == 2
        assert total == pytest.approx(40.0)

    def test_ignores_invalid_json(self, consumer):
        consumer._update_task_latency(b"not json", "hi.tasks.completed")
        with consumer._lock:
            assert "completed" not in consumer._latency_accum

    def test_ignores_missing_timestamps(self, consumer):
        consumer._update_task_latency(
            self._payload(status="completed"),
            "hi.tasks.completed",
        )
        with consumer._lock:
            assert "completed" not in consumer._latency_accum

    def test_status_from_subject_when_missing_in_payload(self, consumer):
        payload = self._payload(created_at=0.0, completed_at=5.0)
        consumer._update_task_latency(payload, "hi.tasks.done")
        with consumer._lock:
            assert "done" in consumer._latency_accum


# ---------------------------------------------------------------------------
# _render_metrics
# ---------------------------------------------------------------------------

class TestRenderMetrics:
    def test_includes_scrape_timestamp(self, consumer):
        with consumer._lock:
            consumer._scrape_ts = 9999.0
        output = consumer._render_metrics()
        assert "hi_jetstream_consumer_scrape_timestamp 9999.0" in output

    def test_includes_connected_gauge(self, consumer):
        with consumer._lock:
            consumer._connected = 1
        output = consumer._render_metrics()
        assert "hi_jetstream_consumer_connected 1" in output

    def test_includes_event_count(self, consumer):
        with consumer._lock:
            consumer._event_counts[("hi.agents", "created")] = 3
        output = consumer._render_metrics()
        assert 'hi_jetstream_events_total{subject_prefix="hi.agents",event_type="created"} 3' in output

    def test_includes_last_seq(self, consumer):
        with consumer._lock:
            consumer._last_seq["hi_agents"] = 100
        output = consumer._render_metrics()
        assert 'hi_jetstream_consumer_last_seq{stream="hi_agents"} 100' in output

    def test_includes_latency(self, consumer):
        with consumer._lock:
            consumer._latency_accum["completed"] = (20.0, 2)
        output = consumer._render_metrics()
        assert 'hi_jetstream_task_latency_seconds{status="completed"} 10.0' in output

    def test_empty_state_still_has_type_lines(self, consumer):
        output = consumer._render_metrics()
        assert "hi_jetstream_consumer_scrape_timestamp" in output
        assert "hi_jetstream_consumer_connected" in output

    def test_zero_count_latency_renders_zero(self, consumer):
        with consumer._lock:
            consumer._latency_accum["failed"] = (0.0, 0)
        output = consumer._render_metrics()
        assert 'hi_jetstream_task_latency_seconds{status="failed"} 0.0' in output


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class TestHandler:
    """Smoke-test the HTTP metrics and health endpoints via a live server."""

    @pytest.fixture(scope="class")
    def server_port(self, consumer):
        import socketserver

        class ThreadedServer(socketserver.ThreadingMixIn, urllib.request.AbstractHTTPHandler.__class__):
            pass

        from http.server import HTTPServer
        server = HTTPServer(("127.0.0.1", 0), consumer.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        yield port
        server.shutdown()

    def test_metrics_returns_200(self, consumer, server_port):
        resp = urllib.request.urlopen(f"http://127.0.0.1:{server_port}/metrics", timeout=3)
        assert resp.status == 200

    def test_metrics_content_type(self, consumer, server_port):
        resp = urllib.request.urlopen(f"http://127.0.0.1:{server_port}/metrics", timeout=3)
        ct = resp.headers.get("Content-Type", "")
        assert "text/plain" in ct

    def test_health_returns_ok(self, consumer, server_port):
        resp = urllib.request.urlopen(f"http://127.0.0.1:{server_port}/health", timeout=3)
        assert resp.status == 200
        assert resp.read() == b"ok"

    def test_unknown_path_returns_404(self, consumer, server_port):
        import urllib.error
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{server_port}/unknown", timeout=3)
        assert exc_info.value.code == 404
