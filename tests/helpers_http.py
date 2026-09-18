"""Shared HTTP test helpers.

Provides an in-process ``ThreadingHTTPServer`` fixture-style context manager
for exporter / handler-level tests so they don't have to copy-paste server
setup (#287).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

# Relies on pyproject.toml's `pythonpath = ["."]` (repo root on sys.path).
import exporter.exporter as exporter_mod


class SilentHandler(exporter_mod.Handler):
    """Test-only Handler subclass that suppresses access log output (#286).

    The production Handler routes log_message to log.debug, which is silent at
    the default INFO level but spams stderr if a developer flips LOG_LEVEL to
    DEBUG while running the test suite. Override with a no-op so tests using
    this helper stay quiet regardless of the surrounding log config.
    """

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        return


@contextlib.contextmanager
def live_server() -> Iterator[int]:
    """Spin up a real ThreadingHTTPServer on an ephemeral 127.0.0.1 port.

    Yields:
        The bound port (int), suitable for building
        http://127.0.0.1:<port>/<path> URLs. The server is shut down on
        context exit.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), SilentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
