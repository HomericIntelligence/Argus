"""Integration test for jetstream-consumer against a real nats-server.

Spins up a real NATS server (JetStream enabled) via the session-scoped
fixture in `conftest.py`, runs the consumer's `subscribe_loop` in a
background thread, publishes synthetic `hi.tasks.completed` and
`hi.agents.created` events, and asserts that `/metrics` reflects them.

Mirrors Charybdis's `REQUIRES_NATS` ctest gate semantically: when the
`nats-server` binary is not on PATH the entire module is skipped.
"""
from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from .conftest import NATSPublisher


def _nats_py_installed() -> bool:
    """Return True iff the real `nats-py` package is importable on disk.

    Unit tests inject a `MagicMock` stub at `sys.modules["nats"]`; we
    explicitly bypass that cache so this probe reflects what is actually
    installed in the environment.
    """
    saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "nats" or k.startswith("nats.")}
    try:
        return importlib.util.find_spec("nats") is not None
    finally:
        sys.modules.update(saved)


_nats_py_available = _nats_py_installed()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_nats,
    pytest.mark.skipif(
        shutil.which("nats-server") is None,
        reason="nats-server not in PATH",
    ),
    pytest.mark.skipif(
        not _nats_py_available,
        reason="nats-py not installed",
    ),
]


# ---------------------------------------------------------------------------
# Stub `nats.errors` / `nats.js.errors` only if they are not provided by an
# installed nats-py. The publisher fixture imports the real `nats` package
# (it must be installed for integration tests anyway), so we just guard
# against the case where the unit-test stub is still cached from a prior
# test in the same session.
# ---------------------------------------------------------------------------

def _ensure_real_nats_loaded() -> None:
    """Drop any unit-test stub so we get the real installed `nats` package."""
    nats_mod = sys.modules.get("nats")
    if nats_mod is not None and isinstance(getattr(nats_mod, "connect", None), MagicMock):
        # A unit-test stub is loaded. Purge so the real package re-imports.
        for key in [k for k in sys.modules if k == "nats" or k.startswith("nats.")]:
            del sys.modules[key]


@pytest.fixture(scope="module")
def consumer_module() -> types.ModuleType:
    """Load `jetstream-consumer/consumer.py` against the real nats-py.

    Unit tests in `tests/test_jetstream_consumer.py` install a `MagicMock`
    stub at module scope; we explicitly purge it here so this integration
    module loads the real client.
    """
    _ensure_real_nats_loaded()
    path = Path(__file__).resolve().parents[2] / "jetstream-consumer" / "consumer.py"
    spec = importlib.util.spec_from_file_location("consumer_integration", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _reset_consumer_state(consumer: types.ModuleType) -> None:
    """Clear the consumer's module-global metric state before a test."""
    with consumer._lock:
        consumer._event_counts.clear()
        consumer._last_seq.clear()
        consumer._latency_accum.clear()
        consumer._scrape_ts = 0.0
        consumer._connected = 0


def test_subscribe_loop_records_published_events(
    consumer_module: types.ModuleType,
    nats_url: str,
    publisher: NATSPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: publish events → subscribe_loop consumes → metrics reflect them.

    This exercises connect → pull_subscribe → fetch → ack → render_metrics
    against a real nats-server, the path that unit tests cannot cover
    because they replace `nats` with a `MagicMock`.
    """
    consumer = consumer_module
    _reset_consumer_state(consumer)
    monkeypatch.setattr(consumer, "NATS_URL", nats_url)
    monkeypatch.setattr(consumer, "FETCH_TIMEOUT", 0.25)
    monkeypatch.setattr(consumer, "FETCH_BATCH", 10)

    # Publish before starting the consumer so messages are queued in
    # JetStream and the durable consumer picks them up on first fetch.
    publisher.publish_json(
        "hi.tasks.completed",
        {"status": "completed", "created_at": 100.0, "completed_at": 100.5},
    )
    publisher.publish_json("hi.agents.created", {"agent_id": "alpha"})

    stop_event_box: dict[str, asyncio.Event] = {}
    loop_box: dict[str, asyncio.AbstractEventLoop] = {}
    ready = threading.Event()

    def run_subscriber() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_box["loop"] = loop
        stop_event = asyncio.Event()
        stop_event_box["stop"] = stop_event
        ready.set()
        try:
            loop.run_until_complete(consumer.subscribe_loop(stop_event))
        finally:
            loop.close()

    t = threading.Thread(target=run_subscriber, daemon=True)
    t.start()
    assert ready.wait(timeout=5.0), "subscriber thread failed to initialise"

    try:
        # Wait up to 15s for both events to be consumed and acked.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            with consumer._lock:
                tasks_count = consumer._event_counts.get(
                    ("hi.tasks", "completed"), 0
                )
                agents_count = consumer._event_counts.get(
                    ("hi.agents", "created"), 0
                )
                connected = consumer._connected
            if tasks_count >= 1 and agents_count >= 1 and connected == 1:
                break
            time.sleep(0.2)

        metrics = consumer._render_metrics()
    finally:
        loop = loop_box["loop"]
        stop_event = stop_event_box["stop"]
        loop.call_soon_threadsafe(stop_event.set)
        t.join(timeout=10.0)
        assert not t.is_alive(), "subscriber thread failed to shut down"

    # Verify metric state reflects what we published.
    assert "hi_jetstream_consumer_connected 1" in metrics
    assert 'subject_prefix="hi.tasks"' in metrics
    assert 'event_type="completed"' in metrics
    assert 'subject_prefix="hi.agents"' in metrics
    assert 'event_type="created"' in metrics
    # Latency for the completed task: 100.5 - 100.0 = 0.5s
    assert 'hi_jetstream_task_latency_seconds{status="completed"} 0.5' in metrics
