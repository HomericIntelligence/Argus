#!/usr/bin/env python3
"""
jetstream-consumer — Durable JetStream pull subscriber for hi.agents.> and hi.tasks.>.
Exposes real-time event-rate and task-completion-latency metrics in Prometheus format
on port 9101.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import nats
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import NotFoundError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jetstream-consumer")
logging.getLogger("nats").setLevel(logging.CRITICAL)

NATS_URL       = os.environ.get("NATS_URL",       "nats://172.24.0.1:4222")
EXPORTER_PORT  = int(os.environ.get("EXPORTER_PORT", "9101"))
FETCH_BATCH    = int(os.environ.get("FETCH_BATCH",   "100"))
FETCH_TIMEOUT  = float(os.environ.get("FETCH_TIMEOUT", "1.0"))
RETRY_INTERVAL = int(os.environ.get("RETRY_INTERVAL", "30"))
DURABLE_NAME   = "argus-jetstream-consumer"

# Streams the consumer requires. {stream_name: [subject_filters]}
# If pull_subscribe raises NotFoundError because a stream does not yet exist,
# the consumer will attempt to create it with these subject filters.
REQUIRED_STREAMS: dict[str, list[str]] = {
    "hi_agents": ["hi.agents.>"],
    "hi_tasks":  ["hi.tasks.>"],
}

# ── Shared metrics state (guarded by _lock) ────────────────────────────────
_lock = threading.Lock()

# {(subject_prefix, event_type): count}
_event_counts: dict[tuple[str, str], int] = {}

# {stream_name: last_sequence}
_last_seq: dict[str, int] = {}

# {status: (sum_latency_seconds, count)} for running mean
_latency_accum: dict[str, tuple[float, int]] = {}

_scrape_ts: float = 0.0
_connected: int = 0  # 1 = connected, 0 = disconnected


def _subject_prefix(subject: str) -> str:
    """Extract the two-part prefix from a dotted subject, e.g. 'hi.agents' from 'hi.agents.created'."""
    parts = subject.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else subject


def _event_type(subject: str) -> str:
    """Extract the event type token (3rd segment) from a subject."""
    parts = subject.split(".")
    return parts[2] if len(parts) >= 3 else "unknown"


def _update_event(subject: str, seq: int, stream: str) -> None:
    prefix = _subject_prefix(subject)
    etype = _event_type(subject)
    with _lock:
        key = (prefix, etype)
        _event_counts[key] = _event_counts.get(key, 0) + 1
        if seq > _last_seq.get(stream, 0):
            _last_seq[stream] = seq


def _update_task_latency(payload: bytes, subject: str) -> None:
    """Parse task payload for created_at/completed_at and accumulate latency by status."""
    try:
        data: dict[str, Any] = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return

    status = data.get("status", _event_type(subject))
    completed_at = data.get("completed_at")
    created_at = data.get("created_at")
    if completed_at is not None and created_at is not None:
        try:
            latency = float(completed_at) - float(created_at)
        except (TypeError, ValueError):
            return
        with _lock:
            prev_sum, prev_count = _latency_accum.get(status, (0.0, 0))
            _latency_accum[status] = (prev_sum + latency, prev_count + 1)


def _render_metrics() -> str:
    lines: list[str] = []

    def gauge(name: str, value: float | int, labels: dict[str, str] | None = None) -> None:
        if labels:
            lstr = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name}{{{lstr}}} {value}")
        else:
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

    with _lock:
        for (prefix, etype), count in _event_counts.items():
            gauge(
                "hi_jetstream_events_total",
                count,
                {"subject_prefix": prefix, "event_type": etype},
            )
        for stream, seq in _last_seq.items():
            gauge("hi_jetstream_consumer_last_seq", seq, {"stream": stream})
        for status, (total_lat, count) in _latency_accum.items():
            mean = total_lat / count if count > 0 else 0.0
            gauge("hi_jetstream_task_latency_seconds", mean, {"status": status})
        gauge("hi_jetstream_consumer_scrape_timestamp", _scrape_ts)
        gauge("hi_jetstream_consumer_connected", _connected)

    return "\n".join(lines) + "\n"


# ── HTTP server ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/metrics":
            body = _render_metrics().encode()
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

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


def _run_http_server(port: int) -> None:
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


# ── JetStream subscription loop ────────────────────────────────────────────

async def _pull_subscribe_with_auto_create(
    js: Any,
    subject: str,
    stream: str,
    durable: str,
) -> Any:
    """Call ``js.pull_subscribe`` and, on :class:`NotFoundError`, create the
    expected stream and retry once.

    nats-py raises ``NotFoundError`` when ``pull_subscribe`` is asked to bind to
    a stream that does not exist on the JetStream server. The previous
    behaviour was to surface this as a generic exception and fall back to the
    outer retry loop, which would never make progress until an operator
    manually created the stream. This wrapper attempts an automatic recovery
    by creating the stream with the subject filter we are about to subscribe
    to, then retrying ``pull_subscribe`` exactly once. If the second attempt
    still fails, the exception propagates so the outer loop can log it.
    """
    try:
        return await js.pull_subscribe(subject, durable=durable, stream=stream)
    except NotFoundError:
        subjects = REQUIRED_STREAMS.get(stream, [subject])
        log.warning(
            "JetStream stream %r not found; auto-creating with subjects %s",
            stream,
            subjects,
        )
        await js.add_stream(name=stream, subjects=subjects)
        return await js.pull_subscribe(subject, durable=durable, stream=stream)


async def _fetch_loop(
    sub: Any,
    stream: str,
    stop_event: asyncio.Event,
) -> None:
    """Pull messages in batches until stop_event is set."""
    while not stop_event.is_set():
        try:
            msgs = await asyncio.wait_for(
                sub.fetch(FETCH_BATCH),
                timeout=FETCH_TIMEOUT,
            )
        except NatsTimeoutError:
            msgs = []
        except asyncio.TimeoutError:
            msgs = []

        for msg in msgs:
            meta = msg.metadata
            _update_event(msg.subject, meta.sequence.stream if meta else 0, stream)
            if msg.subject.startswith("hi.tasks."):
                _update_task_latency(msg.data, msg.subject)
            await msg.ack()

        with _lock:
            global _scrape_ts
            _scrape_ts = time.time()


async def subscribe_loop(stop_event: asyncio.Event) -> None:
    """Outer retry loop: connect → subscribe → pull until stop or disconnect."""
    global _connected, _scrape_ts
    while not stop_event.is_set():
        nc = None
        try:
            log.info("Connecting to NATS at %s", NATS_URL)
            nc = await asyncio.wait_for(
                nats.connect(
                    NATS_URL,
                    allow_reconnect=False,
                    connect_timeout=3,
                ),
                timeout=5,
            )
            js = nc.jetstream()

            sub_agents = await _pull_subscribe_with_auto_create(
                js,
                "hi.agents.>",
                stream="hi_agents",
                durable=DURABLE_NAME,
            )
            sub_tasks = await _pull_subscribe_with_auto_create(
                js,
                "hi.tasks.>",
                stream="hi_tasks",
                durable=DURABLE_NAME,
            )

            with _lock:
                _connected = 1
            log.info("Connected. Consuming hi.agents.> and hi.tasks.>")

            fetch_agents = asyncio.ensure_future(
                _fetch_loop(sub_agents, "hi_agents", stop_event)
            )
            fetch_tasks = asyncio.ensure_future(
                _fetch_loop(sub_tasks, "hi_tasks", stop_event)
            )

            await asyncio.gather(fetch_agents, fetch_tasks)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("NATS connection/subscription error: %s", exc)
        finally:
            with _lock:
                _connected = 0
            if nc is not None:
                try:
                    await nc.close()
                except Exception:
                    pass

        if stop_event.is_set():
            break

        log.info("Retrying in %ds...", RETRY_INTERVAL)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RETRY_INTERVAL)
        except asyncio.TimeoutError:
            pass


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("jetstream-consumer starting on port %d", EXPORTER_PORT)

    http_thread = threading.Thread(
        target=_run_http_server, args=(EXPORTER_PORT,), daemon=True
    )
    http_thread.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event: asyncio.Event = asyncio.Event()

    try:
        loop.run_until_complete(subscribe_loop(stop_event))
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        loop.close()
        log.info("jetstream-consumer stopped")
