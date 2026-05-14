"""Shared fixtures for integration tests that need a real NATS server.

Spins up a `nats-server` subprocess with JetStream enabled on an ephemeral
port and yields a connection URL. Tests are skipped (per their own
`pytest.mark.skipif`) when `nats-server` is not on PATH.

Mirrors the pattern used by ProjectScylla's `tests/integration/nats/conftest.py`
and Charybdis's `REQUIRES_NATS` ctest gate so contributors can recognize the
shape across the HomericIntelligence ecosystem.
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import tempfile
import time
from collections.abc import Generator
from typing import Any

import pytest


def _find_free_port() -> int:
    """Find a free TCP port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> None:
    """Block until a TCP port is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(
        f"nats-server did not start on {host}:{port} within {timeout}s"
    )


class NATSPublisher:
    """Synchronous wrapper around nats-py for publishing JetStream messages.

    Manages its own asyncio event loop so tests stay synchronous.
    """

    def __init__(
        self,
        url: str,
        streams: tuple[tuple[str, list[str]], ...] = (
            ("hi_agents", ["hi.agents.>"]),
            ("hi_tasks", ["hi.tasks.>"]),
        ),
    ) -> None:
        """Initialize the publisher and ensure all configured streams exist."""
        self._url = url
        self._streams = streams
        self._loop = asyncio.new_event_loop()
        self._nc: Any = None
        self._js: Any = None
        self._setup()

    def _setup(self) -> None:
        """Connect to NATS and create configured JetStream streams."""
        import nats as nats_client

        self._nc = self._loop.run_until_complete(nats_client.connect(self._url))
        self._js = self._nc.jetstream()
        for name, subjects in self._streams:
            self._loop.run_until_complete(
                self._js.add_stream(name=name, subjects=subjects)
            )

    def publish_json(self, subject: str, payload: dict[str, Any]) -> None:
        """Publish a JSON-encoded message to the given subject."""
        self._loop.run_until_complete(
            self._js.publish(subject, json.dumps(payload).encode())
        )

    def purge_all(self) -> None:
        """Purge every configured stream so tests start with a clean slate."""
        for name, _ in self._streams:
            self._loop.run_until_complete(self._js.purge_stream(name))

    def close(self) -> None:
        """Drain the connection and close the event loop."""
        if self._nc is not None:
            self._loop.run_until_complete(self._nc.drain())
        self._loop.close()


@pytest.fixture(scope="session")
def nats_port() -> int:
    """Return an ephemeral TCP port for nats-server."""
    return _find_free_port()


@pytest.fixture(scope="session")
def nats_url(nats_port: int) -> Generator[str, None, None]:
    """Start a real nats-server process with JetStream and yield its URL.

    The subprocess is terminated in teardown. Tests that depend on this
    fixture should `pytest.mark.skipif(shutil.which("nats-server") is None)`
    so the suite remains green on hosts without the binary.
    """
    with tempfile.TemporaryDirectory() as store_dir:
        proc = subprocess.Popen(
            [
                "nats-server",
                "-p",
                str(nats_port),
                "-js",
                "-sd",
                store_dir,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            _wait_for_port(nats_port)
            yield f"nats://127.0.0.1:{nats_port}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


@pytest.fixture()
def publisher(nats_url: str) -> Generator[NATSPublisher, None, None]:
    """Yield a NATSPublisher connected to the test server.

    Creates `hi_agents` and `hi_tasks` streams and tears down the
    connection after the test.
    """
    pub = NATSPublisher(nats_url)
    pub.purge_all()
    try:
        yield pub
    finally:
        pub.close()
