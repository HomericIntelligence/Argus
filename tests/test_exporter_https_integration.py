"""Integration tests: exercise _fetch/_health_check against a live HTTPS server.

Unlike tests/test_exporter_tls.py (which mocks urllib.request.urlopen), these
tests start an in-process TLS server with a self-signed certificate for
``localhost`` and verify the full scrape path end-to-end: SSL context
construction, hostname verification, and certificate-chain validation all run
for real. This catches regressions such as accidentally passing
``context=None`` for HTTPS URLs or misconfiguring ``check_hostname``.
"""
from __future__ import annotations

import json
import ssl
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.test_exporter_tls import _import_exporter


# ---------------------------------------------------------------------------
# Live HTTPS server fixture
# ---------------------------------------------------------------------------


def _generate_cert(tmp_dir: Path) -> tuple[Path, Path]:
    """Generate a self-signed key/cert pair valid for DNS:localhost, IP:127.0.0.1."""
    cert = tmp_dir / "server.crt"
    key = tmp_dir / "server.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(cert),
            "-days", "1", "-nodes",
            "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


class _StubHandler(BaseHTTPRequestHandler):
    """Routes /api to canned JSON and /v1/health to a bare 200."""

    def do_GET(self) -> None:
        if self.path == "/api":
            body = json.dumps({"status": "ok", "answer": 42}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/v1/health":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def https_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """Run a ThreadingHTTPServer wrapped in TLS; yield (base_url, ca_path)."""
    tmp_dir = tmp_path_factory.mktemp("tls")
    try:
        cert, key = _generate_cert(tmp_dir)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        pytest.skip(f"openssl unavailable or failed: {e}")

    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    port = int(server.server_address[1])
    yield f"https://localhost:{port}", str(cert)

    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


# ---------------------------------------------------------------------------
# _fetch against a real HTTPS server
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFetchAgainstLiveHttpsServer:
    def test_fetch_json_over_real_tls(self, https_server: tuple[str, str]) -> None:
        base_url, ca_path = https_server
        mod = _import_exporter({"TLS_VERIFY": "true"})
        result = mod._fetch(f"{base_url}/api", ca_file=ca_path)
        assert result == {"status": "ok", "answer": 42}

    def test_fetch_without_ca_fails_verification(
        self, https_server: tuple[str, str]
    ) -> None:
        base_url, _ = https_server
        mod = _import_exporter({"TLS_VERIFY": "true"})
        # Self-signed cert must be rejected by the default (system trust store)
        # context -- proves the CA context is genuinely applied.
        assert mod._fetch(f"{base_url}/api", ca_file=None) is None


# ---------------------------------------------------------------------------
# _health_check against a real HTTPS server
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestHealthCheckAgainstLiveHttpsServer:
    def test_health_check_200_over_real_tls(
        self, https_server: tuple[str, str]
    ) -> None:
        base_url, ca_path = https_server
        mod = _import_exporter({"TLS_VERIFY": "true"})
        assert mod._health_check(f"{base_url}/v1/health", ca_file=ca_path) == 1

    def test_health_check_without_ca_returns_0(
        self, https_server: tuple[str, str]
    ) -> None:
        base_url, _ = https_server
        mod = _import_exporter({"TLS_VERIFY": "true"})
        assert mod._health_check(f"{base_url}/v1/health", ca_file=None) == 0
