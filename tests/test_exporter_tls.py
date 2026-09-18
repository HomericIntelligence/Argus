"""Tests for TLS/SSL context handling in the homeric-exporter."""
from __future__ import annotations

import importlib
import os
import ssl
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the exporter module with specific env vars set
# ---------------------------------------------------------------------------

EXPORTER_PATH = str(Path(__file__).parent.parent / "exporter")


@pytest.fixture(autouse=True)
def _disable_fleet_for_legacy_tests(monkeypatch):
    monkeypatch.setenv("FLEET_METRICS_ENABLED", "false")
    monkeypatch.delenv("AGAMEMNON_API_KEY", raising=False)


def _import_exporter(env: dict[str, str]):
    """Import (or re-import) exporter with the given environment overrides."""
    with patch.dict(os.environ, env, clear=False):
        if "exporter" in sys.modules:
            del sys.modules["exporter"]
        sys.path.insert(0, EXPORTER_PATH)
        try:
            return importlib.import_module("exporter")
        finally:
            sys.path.pop(0)


# ---------------------------------------------------------------------------
# Tests for _build_ssl_context
# ---------------------------------------------------------------------------

class TestBuildSslContext:
    def test_no_ca_file_returns_none_when_verify_enabled(self):
        mod = _import_exporter({"TLS_VERIFY": "true"})
        ctx = mod._build_ssl_context(ca_file=None)
        assert ctx is None

    def test_ca_file_returns_ssl_context(self, tmp_path: Path):
        # Write a minimal PEM-like file (content doesn't matter for context creation test)
        ca = tmp_path / "ca.crt"
        # Generate a real self-signed cert so SSLContext can load it
        import subprocess
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(tmp_path / "ca.key"),
                "-out", str(ca),
                "-days", "1", "-nodes",
                "-subj", "/CN=test-ca",
            ],
            check=True,
            capture_output=True,
        )
        mod = _import_exporter({"TLS_VERIFY": "true"})
        ctx = mod._build_ssl_context(ca_file=str(ca))
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_tls_verify_false_returns_insecure_context(self):
        mod = _import_exporter({"TLS_VERIFY": "false"})
        ctx = mod._build_ssl_context(ca_file=None)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False

    def test_tls_verify_false_overrides_ca_file(self, tmp_path: Path):
        mod = _import_exporter({"TLS_VERIFY": "false"})
        ctx = mod._build_ssl_context(ca_file=str(tmp_path / "nonexistent.crt"))
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Tests for _fetch and _health_check with mocked urlopen
# ---------------------------------------------------------------------------

class TestFetchWithTls:
    def test_fetch_passes_none_context_for_http(self):
        mod = _import_exporter({"TLS_VERIFY": "true"})
        fake_response = MagicMock()
        fake_response.read.return_value = b'{"key": "value"}'
        with patch("urllib.request.urlopen", return_value=fake_response) as mock_open:
            result = mod._fetch("http://example.com/api", ca_file=None)
        assert result == {"key": "value"}
        # context=None is passed for plain HTTP
        assert mock_open.call_args[1].get("context") is None

    def test_fetch_passes_ssl_context_when_ca_file_set(self, tmp_path: Path):
        import subprocess
        ca = tmp_path / "ca.crt"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048",
             "-keyout", str(tmp_path / "ca.key"), "-out", str(ca),
             "-days", "1", "-nodes", "-subj", "/CN=test-ca"],
            check=True, capture_output=True,
        )
        mod = _import_exporter({"TLS_VERIFY": "true"})
        fake_response = MagicMock()
        fake_response.read.return_value = b'{"ok": true}'
        with patch("urllib.request.urlopen", return_value=fake_response) as mock_open:
            result = mod._fetch("https://example.com/api", ca_file=str(ca))
        assert result == {"ok": True}
        ctx_kwarg = mock_open.call_args[1].get("context")
        assert isinstance(ctx_kwarg, ssl.SSLContext)
        assert ctx_kwarg.verify_mode == ssl.CERT_REQUIRED

    def test_fetch_returns_none_on_exception(self):
        mod = _import_exporter({"TLS_VERIFY": "true"})
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = mod._fetch("http://unreachable/api")
        assert result is None

    def test_health_check_returns_1_on_200(self):
        mod = _import_exporter({"TLS_VERIFY": "true"})
        fake_response = MagicMock()
        fake_response.status = 200
        with patch("urllib.request.urlopen", return_value=fake_response):
            assert mod._health_check("http://example.com/health") == 1

    def test_health_check_returns_0_on_exception(self):
        mod = _import_exporter({"TLS_VERIFY": "true"})
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            assert mod._health_check("http://unreachable/health") == 0

    def test_health_check_passes_ssl_context(self, tmp_path: Path):
        import subprocess
        ca = tmp_path / "ca.crt"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048",
             "-keyout", str(tmp_path / "ca.key"), "-out", str(ca),
             "-days", "1", "-nodes", "-subj", "/CN=test-ca"],
            check=True, capture_output=True,
        )
        mod = _import_exporter({"TLS_VERIFY": "true"})
        fake_response = MagicMock()
        fake_response.status = 200
        with patch("urllib.request.urlopen", return_value=fake_response) as mock_open:
            mod._health_check("https://example.com/health", ca_file=str(ca))
        ctx_kwarg = mock_open.call_args[1].get("context")
        assert isinstance(ctx_kwarg, ssl.SSLContext)


# ---------------------------------------------------------------------------
# Tests for env var wiring in collect()
# ---------------------------------------------------------------------------

class TestCollectTlsEnvWiring:
    """Verify that AGAMEMNON_TLS_CA / NESTOR_TLS_CA / NATS_TLS_CA are threaded
    through to _fetch/_health_check when set."""

    def test_tls_ca_env_vars_default_to_none(self):
        env = {
            "AGAMEMNON_TLS_CA": "",
            "NESTOR_TLS_CA": "",
            "NATS_TLS_CA": "",
        }
        mod = _import_exporter(env)
        assert mod.AGAMEMNON_TLS_CA in (None, "")
        assert mod.NESTOR_TLS_CA in (None, "")
        assert mod.NATS_TLS_CA in (None, "")

    def test_tls_ca_env_vars_set_correctly(self, tmp_path: Path):
        ca_path = str(tmp_path / "ca.crt")
        env = {
            "AGAMEMNON_TLS_CA": ca_path,
            "NESTOR_TLS_CA": ca_path,
            "NATS_TLS_CA": ca_path,
        }
        mod = _import_exporter(env)
        assert mod.AGAMEMNON_TLS_CA == ca_path
        assert mod.NESTOR_TLS_CA == ca_path
        assert mod.NATS_TLS_CA == ca_path

    def test_collect_passes_ca_to_agamemnon_calls(self, tmp_path: Path):
        ca_path = str(tmp_path / "ca.crt")
        env = {
            "AGAMEMNON_URL": "https://agamemnon.test:8080",
            "NESTOR_URL": "https://nestor.test:8081",
            "NATS_URL": "https://nats.test:8222",
            "AGAMEMNON_TLS_CA": ca_path,
            "NESTOR_TLS_CA": ca_path,
            "NATS_TLS_CA": ca_path,
        }
        mod = _import_exporter(env)
        # All upstream calls should fail (no real server), but we verify ca_file threading.
        calls: list[tuple] = []

        original_fetch = mod._fetch
        original_health = mod._health_check

        def spy_fetch(url: str, ca_file=None):
            calls.append(("fetch", url, ca_file))

        def spy_health(url: str, ca_file=None):
            calls.append(("health", url, ca_file))
            return 0

        mod._fetch = spy_fetch
        mod._health_check = spy_health
        mod.collect()
        mod._fetch = original_fetch
        mod._health_check = original_health

        fetch_ca_files = {c[2] for c in calls if c[0] == "fetch"}
        health_ca_files = {c[2] for c in calls if c[0] == "health"}
        assert ca_path in fetch_ca_files, "CA file not passed to _fetch"
        assert ca_path in health_ca_files, "CA file not passed to _health_check"


def _collect_fleet_module(mod):
    with (
        patch.dict(os.environ, {"FLEET_METRICS_ENABLED": "true", "AGAMEMNON_API_KEY": "synthetic-fleet-key"}),
        patch.object(mod, "_fetch", return_value={"agents": []}),
        patch.object(mod, "_health_check", return_value=1),
    ):
        return mod.collect()


@pytest.mark.parametrize("verify", ["true", "false"])
def test_fleet_tls_preserves_system_trust_and_development_override(verify):
    mod = _import_exporter({"TLS_VERIFY": verify, "AGAMEMNON_TLS_CA": "", "AGAMEMNON_URL": "https://agamemnon.test"})
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.read.return_value = b'{"items":[],"total":0}'
    with (
        patch("urllib.request.build_opener", wraps=urllib.request.build_opener) as build,
        patch("urllib.request.OpenerDirector.open", return_value=response),
    ):
        output = _collect_fleet_module(mod)
    assert "hi_fleet_activity_complete{} 1\n" in output
    assert build.call_count == 3
    for call in build.call_args_list:
        handler = next(item for item in call.args if isinstance(item, urllib.request.HTTPSHandler))
        context = handler._context
        if verify == "true":
            # Python versions differ in when urllib creates the default context.
            if context is not None:
                assert context.verify_mode == ssl.CERT_REQUIRED
                assert context.check_hostname is True
        else:
            assert context.verify_mode == ssl.CERT_NONE
            assert context.check_hostname is False


def test_fleet_tls_invalid_ca_is_unavailable_without_a_request(tmp_path):
    mod = _import_exporter({"TLS_VERIFY": "true", "AGAMEMNON_TLS_CA": str(tmp_path / "missing.pem"), "AGAMEMNON_URL": "https://agamemnon.test"})
    with patch("urllib.request.OpenerDirector.open") as opened:
        output = _collect_fleet_module(mod)
    opened.assert_not_called()
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert "hi_fleet_recently_observed_active_agents" not in output
    assert "hi_agents_count{} 0\n" in output


def test_fleet_tls_uses_configured_ca_bundle(tmp_path):
    import subprocess

    ca = tmp_path / "fleet-ca.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(tmp_path / "fleet-ca.key"), "-out", str(ca),
         "-days", "1", "-subj", "/CN=fleet-test-ca"],
        check=True, capture_output=True, timeout=15,
    )
    mod = _import_exporter({"TLS_VERIFY": "true", "AGAMEMNON_TLS_CA": str(ca), "AGAMEMNON_URL": "https://agamemnon.test"})
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.read.return_value = b'{"items":[],"total":0}'
    with (
        patch("urllib.request.build_opener", wraps=urllib.request.build_opener) as build,
        patch("urllib.request.OpenerDirector.open", return_value=response),
    ):
        output = _collect_fleet_module(mod)
    assert "hi_fleet_activity_complete{} 1\n" in output
    assert build.call_count == 3
    for call in build.call_args_list:
        context = next(item._context for item in call.args if isinstance(item, urllib.request.HTTPSHandler))
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert any((("commonName", "fleet-test-ca"),) in cert["subject"] for cert in context.get_ca_certs())


@pytest.mark.parametrize("redirect_code", [301, 302, 303, 307, 308])
def test_fleet_authenticated_redirect_is_not_followed(redirect_code):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("Authorization")))
            self.send_response(redirect_code)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/credential-sink")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        mod = _import_exporter({"TLS_VERIFY": "true", "AGAMEMNON_TLS_CA": "", "AGAMEMNON_URL": f"http://127.0.0.1:{server.server_port}"})
        output = _collect_fleet_module(mod)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(received) == 3
    assert {path for path, _ in received} == {f"/v1/fleet/{name}" for name in ("workers", "sessions", "executions")}
    assert {key for _, key in received} == {"Bearer synthetic-fleet-key"}
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert "hi_fleet_recently_observed_active_agents" not in output
