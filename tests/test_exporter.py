"""
Unit tests for exporter/exporter.py.

All network calls are mocked via unittest.mock.patch so no real HTTP
connections are made during the test suite.
"""
from __future__ import annotations

import contextlib
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from pathlib import Path
from unittest.mock import MagicMock, patch

# Make the exporter importable without running __main__ logic
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
import exporter.exporter as exporter_mod  # noqa: E402


def _make_response(data: dict | None = None, status: int = 200) -> MagicMock:
    """Return a mock object that behaves like urllib.request.urlopen's return value."""
    mock = MagicMock()
    mock.status = status
    if data is not None:
        mock.read.return_value = json.dumps(data).encode()
    else:
        mock.read.return_value = b"{}"
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _urlopen_raises(*args, **kwargs):
    raise OSError("connection refused")


# ---------------------------------------------------------------------------
# Test _health_check
# ---------------------------------------------------------------------------

class TestHealthCheck(unittest.TestCase):
    def test_returns_1_for_http_200(self):
        mock_resp = _make_response(status=200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = exporter_mod._health_check("http://fake/health")
        self.assertEqual(result, 1)

    def test_returns_0_for_non_200(self):
        mock_resp = _make_response(status=503)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = exporter_mod._health_check("http://fake/health")
        self.assertEqual(result, 0)

    def test_returns_0_on_exception(self):
        with patch("urllib.request.urlopen", side_effect=_urlopen_raises):
            result = exporter_mod._health_check("http://fake/health")
        self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# Test _fetch
# ---------------------------------------------------------------------------

class TestFetch(unittest.TestCase):
    def test_returns_dict_on_success(self):
        mock_resp = _make_response({"key": "value"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = exporter_mod._fetch("http://fake/data")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["key"], "value")

    def test_returns_none_on_oserror(self):
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = exporter_mod._fetch("http://fake/data")
        self.assertIsNone(result)

    def test_returns_none_on_urlerror(self):
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("name or service not known")):
            result = exporter_mod._fetch("http://fake/data")
        self.assertIsNone(result)

    def test_returns_none_on_json_decode_error(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not-json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = exporter_mod._fetch("http://fake/data")
        self.assertIsNone(result)

    def test_propagates_unexpected_exception(self):
        """Exceptions outside the specific tuple must not be swallowed."""
        with patch("urllib.request.urlopen", side_effect=MemoryError("oom")):
            with self.assertRaises(MemoryError):
                exporter_mod._fetch("http://fake/data")


# ---------------------------------------------------------------------------
# Helper: patch all seven upstream calls in collect()
# ---------------------------------------------------------------------------

def _patch_collect(
    agamemnon_health: int = 1,
    agents_data: dict | None = None,
    tasks_data: dict | None = None,
    nestor_health: int = 1,
    nestor_stats: dict | None = None,
    nats_varz: dict | None = None,
    nats_jsz: dict | None = None,
):
    """Context-manager factory that patches _health_check and _fetch inside collect()."""
    agents_data = agents_data or {}
    tasks_data = tasks_data or {}

    def _fake_health_check(url: str, ca_file=None) -> int:
        if "agamemnon" in url or "8080" in url:
            return agamemnon_health
        return nestor_health

    def _fake_fetch(url: str, ca_file=None) -> dict | None:
        if "/v1/agents" in url:
            return agents_data
        if "/v1/tasks" in url:
            return tasks_data
        if "/research/stats" in url:
            return nestor_stats
        if "/varz" in url:
            return nats_varz
        if "/jsz" in url:
            return nats_jsz
        return None

    return (
        patch.object(exporter_mod, "_health_check", side_effect=_fake_health_check),
        patch.object(exporter_mod, "_fetch", side_effect=_fake_fetch),
    )


# ---------------------------------------------------------------------------
# Test collect() — output format
# ---------------------------------------------------------------------------

class TestCollectFormat(unittest.TestCase):
    def _run_collect(self, **kwargs):
        hc_patch, fetch_patch = _patch_collect(**kwargs)
        with hc_patch, fetch_patch:
            return exporter_mod.collect()

    def test_returns_string(self):
        output = self._run_collect()
        self.assertIsInstance(output, str)

    def test_ends_with_newline(self):
        output = self._run_collect()
        self.assertTrue(output.endswith("\n"), "collect() output must end with newline")

    def test_contains_type_declarations(self):
        output = self._run_collect()
        self.assertIn("# TYPE", output, "output must contain at least one # TYPE declaration")

    def test_contains_help_lines(self):
        output = self._run_collect()
        self.assertIn("# HELP", output, "output must contain at least one # HELP line")

    def test_no_exception_when_all_upstreams_down(self):
        """collect() must not raise even if every upstream returns None."""
        hc_patch, fetch_patch = _patch_collect(
            agamemnon_health=0,
            agents_data=None,
            tasks_data=None,
            nestor_health=0,
            nestor_stats=None,
            nats_varz=None,
            nats_jsz=None,
        )
        try:
            with hc_patch, fetch_patch:
                output = exporter_mod.collect()
        except Exception as exc:
            self.fail(f"collect() raised an exception when all upstreams are down: {exc}")
        self.assertIsInstance(output, str)

    def test_type_emitted_once_per_metric(self):
        """Each metric name must have exactly one # TYPE line (no duplicates)."""
        nats_varz = {
            "connections": 3, "in_msgs": 100, "out_msgs": 90,
            "in_bytes": 1024, "out_bytes": 512, "slow_consumers": 0,
        }
        output = self._run_collect(nats_varz=nats_varz)
        type_lines = [line for line in output.splitlines() if line.startswith("# TYPE")]
        names = [line.split()[2] for line in type_lines]
        self.assertEqual(len(names), len(set(names)),
                         "Duplicate # TYPE declarations found in collect() output")


# ---------------------------------------------------------------------------
# Test collect() — metric names and values
# ---------------------------------------------------------------------------

class TestCollectMetricNames(unittest.TestCase):
    def setUp(self):
        self.agents_data = {
            "agents": [
                {"name": "alpha", "host": "h1", "program": "prog", "status": "online"},
                {"name": "beta",  "host": "h2", "program": "prog", "status": "offline"},
            ]
        }
        self.tasks_data = {
            "tasks": [
                {"status": "completed"},
                {"status": "completed"},
                {"status": "failed"},
            ]
        }
        self.nats_varz = {
            "connections": 5, "in_msgs": 200, "out_msgs": 180,
            "in_bytes": 2048, "out_bytes": 1024, "slow_consumers": 1,
        }
        self.nestor_stats = {"active": 2, "completed": 10, "pending": 1}
        hc_patch, fetch_patch = _patch_collect(
            agamemnon_health=1,
            agents_data=self.agents_data,
            tasks_data=self.tasks_data,
            nestor_health=1,
            nestor_stats=self.nestor_stats,
            nats_varz=self.nats_varz,
        )
        with hc_patch, fetch_patch:
            self.output = exporter_mod.collect()

    def test_contains_agamemnon_health(self):
        self.assertIn("hi_agamemnon_health", self.output)

    def test_contains_nestor_health(self):
        self.assertIn("hi_nestor_health", self.output)

    def test_contains_nats_connections(self):
        self.assertIn("nats_connections", self.output)

    def test_agent_totals_correct(self):
        """hi_agents_total, hi_agents_online, hi_agents_offline values."""
        lines = {ln.split()[0]: ln.split()[1]
                 for ln in self.output.splitlines()
                 if not ln.startswith("#") and ln.strip()}
        self.assertEqual(lines.get("hi_agents_total{}"), "2")
        self.assertEqual(lines.get("hi_agents_online{}"), "1")
        self.assertEqual(lines.get("hi_agents_offline{}"), "1")

    def test_task_total_correct(self):
        lines = {ln.split()[0]: ln.split()[1]
                 for ln in self.output.splitlines()
                 if not ln.startswith("#") and ln.strip()}
        self.assertEqual(lines.get("hi_tasks_total{}"), "3")

    def test_exporter_self_metrics_present(self):
        self.assertIn("homeric_exporter_scrape_duration_seconds", self.output)
        self.assertIn("homeric_exporter_scrape_timestamp_seconds", self.output)
        self.assertIn("homeric_exporter_fetch_errors", self.output)
        # Must not carry the _total counter suffix (gauge, not counter)
        self.assertNotIn("homeric_exporter_fetch_errors_total", self.output)
        # Regression guard: the old (un-suffixed) name must not coexist with
        # the canonical _seconds-suffixed metric (#425). Match on the trailing
        # `{` to distinguish the bare name from `_seconds`.
        self.assertNotIn("homeric_exporter_scrape_timestamp{", self.output)

    def test_nats_msg_metrics_use_gauge_names_not_total(self):
        """nats_in_msgs and nats_out_msgs must not carry the _total counter suffix."""
        self.assertIn("nats_in_msgs", self.output)
        self.assertIn("nats_out_msgs", self.output)
        self.assertNotIn("nats_in_msgs_total", self.output)
        self.assertNotIn("nats_out_msgs_total", self.output)

    def test_nats_msg_metrics_typed_as_gauge(self):
        """Both renamed metrics must be declared as gauge, not counter."""
        type_lines = [ln for ln in self.output.splitlines() if ln.startswith("# TYPE")]
        type_map = {ln.split()[2]: ln.split()[3] for ln in type_lines}
        self.assertEqual(type_map.get("nats_in_msgs"), "gauge")
        self.assertEqual(type_map.get("nats_out_msgs"), "gauge")


# ---------------------------------------------------------------------------
# Test Handler (HTTP server)
# ---------------------------------------------------------------------------


def _make_handler(path: str) -> tuple:
    """Create a Handler instance with a mock socket/server for unit-testing methods
    that don't require a real HTTP connection (e.g. log_message).

    Returns (handler, mock_server) so callers can inspect either object.
    """
    mock_server = MagicMock()
    mock_server.server_address = ("127.0.0.1", 0)
    # Instantiating BaseHTTPRequestHandler calls handle() which would try I/O;
    # suppress that by patching the method.
    with patch.object(exporter_mod.Handler, "handle"):
        handler = exporter_mod.Handler.__new__(exporter_mod.Handler)
        handler.request = MagicMock()
        handler.client_address = ("127.0.0.1", 0)
        handler.server = mock_server
        handler.path = path
    return handler, mock_server


@contextlib.contextmanager
def _live_server():
    """Spin up a real ThreadingHTTPServer on an ephemeral port; yield the port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), exporter_mod.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()


class TestHandler(unittest.TestCase):
    def _get_response(self, path: str, mock_collect_output: str = "# TYPE x gauge\nx{} 1\n") -> str:
        with patch.object(exporter_mod, "collect", return_value=mock_collect_output):
            with _live_server() as port:
                url = f"http://127.0.0.1:{port}{path}"
                try:
                    resp = urllib.request.urlopen(url, timeout=5)
                    status_line = f"HTTP/1.1 {resp.status} {resp.reason}"
                    headers = "\r\n".join(f"{k}: {v}" for k, v in resp.headers.items())
                    body = resp.read().decode()
                    return f"{status_line}\r\n{headers}\r\n\r\n{body}"
                except urllib.error.HTTPError as exc:
                    status_line = f"HTTP/1.1 {exc.code} {exc.reason}"
                    headers = "\r\n".join(f"{k}: {v}" for k, v in exc.headers.items())
                    body = exc.read().decode()
                    return f"{status_line}\r\n{headers}\r\n\r\n{body}"

    def test_health_returns_200(self):
        response = self._get_response("/health")
        self.assertIn("200", response)

    def test_health_body_is_ok(self):
        response = self._get_response("/health")
        self.assertIn("ok", response)

    def test_metrics_returns_200(self):
        response = self._get_response("/metrics")
        self.assertIn("200", response)

    def test_metrics_content_type(self):
        response = self._get_response("/metrics")
        self.assertIn("text/plain", response)
        self.assertIn("version=0.0.4", response)

    def test_unknown_path_returns_404(self):
        response = self._get_response("/notfound")
        self.assertIn("404", response)

    def test_metrics_body_contains_collect_output(self):
        collect_output = "# TYPE hi_agents_total gauge\nhi_agents_total{} 42\n"
        response = self._get_response("/metrics", mock_collect_output=collect_output)
        self.assertIn("hi_agents_total", response)

    def test_log_message_emits_debug_record(self):
        """log_message must forward to log.debug, not swallow the record."""
        handler, _ = _make_handler("/metrics")
        fmt = '%s - - [%s] "%s" %s %s'
        args = ("127.0.0.1", "04/May/2026 12:00:00", "GET /metrics HTTP/1.1", "200", "-")
        with patch.object(exporter_mod.log, "debug") as mock_debug:
            handler.log_message(fmt, *args)
        mock_debug.assert_called_once_with(fmt, *args)

    def test_log_message_silent_at_info_level(self):
        """log_message must not raise and must produce no INFO-level output."""
        import logging
        handler, _ = _make_handler("/metrics")
        with patch.object(exporter_mod.log, "debug"):
            # At INFO level the debug call should not propagate to any handler
            original_level = exporter_mod.log.level
            exporter_mod.log.setLevel(logging.INFO)
            try:
                handler.log_message("GET /metrics HTTP/1.1 200 -")
            finally:
                exporter_mod.log.setLevel(original_level)


# ---------------------------------------------------------------------------
# Test collect() — # HELP line presence and ordering
# ---------------------------------------------------------------------------

class TestCollectHelpLines(unittest.TestCase):
    def _run_collect(self, **kwargs):
        hc_patch, fetch_patch = _patch_collect(**kwargs)
        with hc_patch, fetch_patch:
            return exporter_mod.collect()

    def _parse_headers(self, output: str) -> dict[str, dict]:
        """Return {metric_name: {"help_idx": int, "type_idx": int}} for each family."""
        result: dict[str, dict] = {}
        for idx, line in enumerate(output.splitlines()):
            if line.startswith("# HELP "):
                name = line.split()[2]
                result.setdefault(name, {})["help_idx"] = idx
            elif line.startswith("# TYPE "):
                name = line.split()[2]
                result.setdefault(name, {})["type_idx"] = idx
        return result

    def test_every_type_has_preceding_help(self):
        """Every # TYPE line must be preceded by a # HELP line for the same metric."""
        output = self._run_collect(
            nats_varz={
                "connections": 1, "in_msgs": 1, "out_msgs": 1,
                "in_bytes": 1, "out_bytes": 1, "slow_consumers": 0,
            },
            nats_jsz={"streams": 1, "consumers": 1, "messages": 10, "bytes": 1024},
            nestor_stats={"active": 1, "completed": 5, "pending": 0},
        )
        headers = self._parse_headers(output)
        for name, indices in headers.items():
            self.assertIn("help_idx", indices,
                          f"# HELP missing for metric '{name}'")
            self.assertIn("type_idx", indices,
                          f"# TYPE missing for metric '{name}'")
            self.assertLess(indices["help_idx"], indices["type_idx"],
                            f"# HELP must appear before # TYPE for metric '{name}'")

    def test_help_text_is_non_empty(self):
        """Every # HELP line must contain non-empty descriptive text."""
        output = self._run_collect(
            nats_varz={
                "connections": 1, "in_msgs": 1, "out_msgs": 1,
                "in_bytes": 1, "out_bytes": 1, "slow_consumers": 0,
            },
        )
        for line in output.splitlines():
            if line.startswith("# HELP "):
                parts = line.split(None, 3)
                self.assertGreaterEqual(len(parts), 4,
                                        f"# HELP line has no description text: {line!r}")
                self.assertTrue(parts[3].strip(),
                                f"# HELP line has empty description: {line!r}")

    def test_help_emitted_once_per_metric(self):
        """Each metric name must have exactly one # HELP line (no duplicates)."""
        output = self._run_collect(
            agents_data={
                "agents": [
                    {"name": "a", "host": "h1", "program": "p", "status": "online"},
                    {"name": "b", "host": "h2", "program": "p", "status": "offline"},
                ]
            },
        )
        help_lines = [line for line in output.splitlines() if line.startswith("# HELP")]
        names = [line.split()[2] for line in help_lines]
        self.assertEqual(len(names), len(set(names)),
                         "Duplicate # HELP declarations found in collect() output")

    def test_all_upstreams_down_still_has_help(self):
        """Even when all upstreams are down, always-emitted metrics must have # HELP."""
        output = self._run_collect(
            agamemnon_health=0,
            agents_data=None,
            tasks_data=None,
            nestor_health=0,
            nestor_stats=None,
            nats_varz=None,
            nats_jsz=None,
        )
        headers = self._parse_headers(output)
        always_present = [
            "hi_agamemnon_health",
            "hi_nestor_health",
            "homeric_exporter_scrape_timestamp_seconds",
            "homeric_exporter_scrape_duration_seconds",
            "homeric_exporter_fetch_errors",
        ]
        for name in always_present:
            self.assertIn(name, headers, f"Metric '{name}' missing from output")
            self.assertIn("help_idx", headers[name],
                          f"# HELP missing for always-present metric '{name}'")

    def test_help_contains_metric_name(self):
        """Each # HELP line's metric name must match the family it documents."""
        output = self._run_collect()
        for line in output.splitlines():
            if line.startswith("# HELP "):
                parts = line.split(None, 3)
                self.assertEqual(parts[0], "#")
                self.assertEqual(parts[1], "HELP")
                self.assertTrue(parts[2].replace("_", "").isalnum() or "_" in parts[2],
                                f"Unexpected metric name format in: {line!r}")


if __name__ == "__main__":
    unittest.main()
