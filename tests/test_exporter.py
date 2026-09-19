"""
Unit tests for exporter/exporter.py.

All network calls are mocked via unittest.mock.patch so no real HTTP
connections are made during the test suite.
"""
from __future__ import annotations

import copy
import importlib
import io
import json
import logging
import os
import re
import sys
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers_http import live_server

# Make the exporter importable without running __main__ logic
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
exporter_mod = importlib.import_module("exporter.exporter")


@pytest.fixture(autouse=True)
def _disable_fleet_for_legacy_tests(monkeypatch):
    """Only controlled Fleet fixtures may opt into the additional HTTP reads."""
    monkeypatch.setenv("FLEET_METRICS_ENABLED", "false")
    monkeypatch.delenv("AGAMEMNON_API_KEY", raising=False)


def _collect_fleet(resources=None, *, enabled="true", key="synthetic-fleet-key",
                   payloads=None, failure=None, url="https://agamemnon.test"):
    """Exercise collect with controlled HTTP responses and an exact scrape time."""
    resources = resources or {name: [] for name in ("workers", "sessions", "executions")}
    responses = []
    requests = []

    def open_response(request, **kwargs):
        requests.append((request, kwargs))
        name = request.full_url.rsplit("/", 1)[-1]
        if failure is not None:
            raise failure
        items = resources[name]
        response = _make_response({"items": items, "total": len(items)})
        if payloads and name in payloads:
            value = payloads[name]
            response.read.return_value = value if isinstance(value, bytes) else json.dumps(value).encode()
        responses.append(response)
        return response

    health, legacy = _patch_collect(agents_data={"agents": []}, tasks_data={"tasks": []})
    environment = {"AGAMEMNON_API_KEY": key}
    if enabled is not None:
        environment["FLEET_METRICS_ENABLED"] = enabled
    with (
        patch.dict(os.environ, environment, clear=True),
        patch.object(exporter_mod, "AGAMEMNON_URL", url),
        patch.object(exporter_mod.time, "time", return_value=1_789_754_400.0),
        patch("urllib.request.OpenerDirector.open", side_effect=open_response),
        health, legacy,
    ):
        output = exporter_mod.collect()
    return output, requests, responses


def test_fleet_empty_snapshot_reports_known_zero_and_preserves_legacy():
    output, requests, _ = _collect_fleet()
    assert "homeric_exporter_fleet_enabled{} 1\n" in output
    assert "hi_fleet_activity_complete{} 1\n" in output
    for activity in ("model_working", "tool_running"):
        for work_kind in ("issue", "interactive"):
            assert (
                'hi_fleet_recently_observed_active_agents{activity="'
                f'{activity}",work_kind="{work_kind}"}} 0\n'
            ) in output
    assert {r.full_url for r, _ in requests} == {
        f"https://agamemnon.test/v1/fleet/{name}"
        for name in ("workers", "sessions", "executions")
    }
    assert len(requests) == 3
    assert "hi_agents_count{} 0\n" in output
    assert 'homeric_exporter_fetch_errors{upstream="agamemnon"} 0\n' in output


_FLEET_TIME = datetime.fromtimestamp(1_789_754_400.0, exporter_mod.timezone.utc).isoformat()
_ACTIVE_METRIC = "hi_fleet_recently_observed_active_agents"


def _fleet_resources():
    return {
        "workers": [{"id": "worker-1", "kind": "workers", "schema": "hi/fleet/v1",
                     "generation": 1, "poolId": "pool-1", "status": "running"}],
        "sessions": [],
        "executions": [{
            "id": "execution-1", "executionId": "execution-1", "kind": "executions",
            "schema": "hi/fleet/v1", "workerId": "worker-1", "agentId": "agent-1",
            "workspace": "private-workspace-canary", "generation": 1, "taskId": "task-1",
            "claimStatus": "claimed", "status": "running", "observationState": "observed",
            "sourceSequence": 1, "activity": "tool_running", "lastActivityAt": _FLEET_TIME,
            "lastActivityReceivedAt": _FLEET_TIME,
        }],
    }


def _fleet_samples(output):
    return {line for line in output.splitlines()
            if line.startswith(("hi_fleet_", "homeric_exporter_fleet_"))}


@pytest.mark.parametrize("enabled", [None, "false", "", "0", "invalid"])
def test_fleet_opt_out_makes_no_requests(enabled):
    output, requests, _ = _collect_fleet(enabled=enabled)
    assert _fleet_samples(output) == {"homeric_exporter_fleet_enabled{} 0"}
    assert requests == []


def test_fleet_authentication_fixed_gets_timeout_and_response_closure():
    output, requests, responses = _collect_fleet()
    assert "hi_fleet_activity_complete{} 1\n" in output
    for request, kwargs in requests:
        assert request.get_method() == "GET"
        assert request.get_header("Authorization") == "Bearer synthetic-fleet-key"
        assert kwargs["timeout"] == 5
    assert len(responses) == 3
    for response in responses:
        response.read.assert_called_once_with(1024 * 1024 + 1)
        response.__exit__.assert_called_once()


@pytest.mark.parametrize("key", ["", "\nnot-a-key", "two words", "x" * 4097, "é"],
                         ids=["missing", "newline", "space", "too-long", "non-ascii"])
def test_fleet_invalid_key_never_attempts_fetch(key):
    output, requests, _ = _collect_fleet(key=key)
    assert requests == []
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert _ACTIVE_METRIC not in output
    assert "hi_agents_count{} 0\n" in output


@pytest.mark.parametrize("url", [
    "file:///tmp/fleet", "https://user:private@agamemnon.test", "https://agamemnon.test?key=private",
    "https://agamemnon.test#private", "https://", "https://agamemnon.test:bad", "https://agamemnon.test\n",
])
def test_fleet_invalid_upstream_never_attempts_fetch(url):
    output, requests, _ = _collect_fleet(url=url)
    assert requests == []
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert _ACTIVE_METRIC not in output


@pytest.mark.parametrize("failure", [
    OSError("private-fetch-canary"), TimeoutError("private-fetch-canary"),
    urllib.error.HTTPError("https://private-fetch-canary", 404, "private-fetch-canary", {}, None),
])
def test_fleet_fetch_failure_is_unknown_without_sensitive_logs(failure):
    with patch.object(exporter_mod.log, "warning") as warning:
        output, _, _ = _collect_fleet(failure=failure)
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert _ACTIVE_METRIC not in output
    assert "private-fetch-canary" not in output + str(warning.call_args_list)
    assert "hi_agents_count{} 0\n" in output
    for name in ("workers", "sessions", "executions"):
        assert f'homeric_exporter_fleet_fetch_success{{resource="{name}"}} 0\n' in output


@pytest.mark.parametrize("payload", [
    b"not json", b"\xff", [], {}, {"items": [], "total": True},
    {"items": [], "total": 0.0}, {"items": [], "total": -1}, {"items": [], "total": 1},
    {"items": {}, "total": 0}, b" " * (1024 * 1024 + 1),
    {"items": [{"id": "duplicate"}, {"id": "duplicate"}], "total": 2},
], ids=["json", "utf8", "array", "empty", "bool-total", "float-total", "negative-total",
        "mismatched-total", "non-list", "byte-overflow", "duplicate-id"])
def test_fleet_invalid_envelope_is_unavailable(payload):
    output, _, _ = _collect_fleet(payloads={"workers": payload})
    assert 'homeric_exporter_fleet_fetch_success{resource="workers"} 0\n' in output
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert _ACTIVE_METRIC not in output


@pytest.mark.parametrize("size,complete", [(1024, 1), (1025, 0)])
def test_fleet_record_limit_does_not_truncate(size, complete):
    resources = _fleet_resources()
    resources["executions"] = []
    worker = resources["workers"][0]
    resources["workers"] = [dict(worker, id=f"worker-{i}") for i in range(size)]
    output, _, _ = _collect_fleet(resources)
    assert f"hi_fleet_activity_complete{{}} {complete}\n" in output
    assert (_ACTIVE_METRIC in output) == bool(complete)


def test_fleet_byte_limit_accepts_exact_boundary():
    body = b'{"items":[],"total":0}'
    output, _, _ = _collect_fleet(payloads={"workers": body + b" " * (1024 * 1024 - len(body))})
    assert "hi_fleet_activity_complete{} 1\n" in output


def test_fleet_distinct_agents_share_runtime_and_work_kinds():
    resources = _fleet_resources()
    second = dict(resources["executions"][0], id="execution-2", executionId="execution-2", agentId="agent-2",
                  taskId="task-2", workspace="workspace-2")
    resources["executions"].append(second)
    interactive = dict(second, id="execution-3", executionId="execution-3", agentId="agent-3",
                       activity="model_working", workspace="workspace-3")
    del interactive["taskId"]
    resources["executions"].append(interactive)
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 1\n" in output
    assert f'{_ACTIVE_METRIC}{{activity="tool_running",work_kind="issue"}} 2\n' in output
    assert f'{_ACTIVE_METRIC}{{activity="model_working",work_kind="interactive"}} 1\n' in output


def _fleet_session(execution):
    return dict(execution, id="session-1", sessionId="session-1", kind="sessions")


def test_fleet_consistent_session_and_execution_count_once():
    resources = _fleet_resources()
    resources["sessions"] = [_fleet_session(resources["executions"][0])]
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 1\n" in output
    assert f'{_ACTIVE_METRIC}{{activity="tool_running",work_kind="issue"}} 1\n' in output


@pytest.mark.parametrize("changes", [
    {"agentId": "agent-other"}, {"workspace": "other"}, {"taskId": "other"},
    {"activity": "model_working"}, {"sourceSequence": 2}, {"generation": 2},
    {"status": "idle", "activity": "idle"}, {"executionId": "execution-other"},
    {"lastActivityReceivedAt": "2026-09-18T17:59:59+00:00"},
])
def test_fleet_conflicting_admitted_representations_exclude_both(changes):
    resources = _fleet_resources()
    resources["sessions"] = [dict(_fleet_session(resources["executions"][0]), **changes)]
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert _ACTIVE_METRIC not in output
    assert 'hi_fleet_observation_exclusions{reason="ambiguous_agent"} 2\n' in output


@pytest.mark.parametrize("changes,reason", [
    ({"claimStatus": "reserved", "status": "admitted", "observationState": "awaiting_activity"}, "unobserved"),
    ({"observationState": "reconciliation_required", "activity": "unknown"}, "reconciliation_required"),
    ({"observationState": "awaiting_activity"}, "unobserved"),
    ({"workerId": "missing"}, "missing_worker"),
    ({"generation": 2}, "generation_mismatch"),
    ({"generation": True}, "invalid_record"),
    ({"sourceSequence": True}, "invalid_record"),
    ({"sourceSequence": 0}, "invalid_record"),
    ({"sourceSequence": "1"}, "invalid_record"),
    ({"status": "created"}, "invalid_record"),
    ({"taskId": None}, "invalid_record"),
    ({"taskId": ""}, "invalid_record"),
    ({"workspace": "é" * 513}, "invalid_record"),
    ({"agentId": []}, "invalid_record"),
    ({"id": "bad id"}, "invalid_record"),
    ({"executionId": "different"}, "invalid_record"),
    ({"schema": "wrong"}, "invalid_record"),
    ({"kind": "sessions"}, "invalid_record"),
    ({"activity": "arbitrary-private-value"}, "unknown_activity"),
    ({"activity": "disconnected"}, "disconnected"),
    ({"lastActivityAt": "invalid"}, "invalid_timestamp"),
    ({"lastActivityAt": "2026-09-18T18:00:00"}, "invalid_timestamp"),
    ({"lastActivityAt": "2026-09-18T18:00:01+00:00"}, "invalid_timestamp"),
    ({"lastActivityAt": "2026-09-18T17:58:59+00:00"}, "stale"),
    ({"lastActivityReceivedAt": "2026-09-18T17:58:59+00:00"}, "stale"),
    ({"lastActivityReceivedAt": "2026-09-18T18:00:01+00:00"}, "invalid_timestamp"),
])
def test_fleet_incomplete_observations_never_report_zero(changes, reason):
    resources = _fleet_resources()
    resources["executions"][0].update(changes)
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert _ACTIVE_METRIC not in output
    assert f'hi_fleet_observation_exclusions{{reason="{reason}"}} 1\n' in output


@pytest.mark.parametrize("status", ["running", "cancelling", "interrupting"])
def test_fleet_exact_age_boundary_and_pending_stop_still_count(status):
    resources = _fleet_resources()
    resources["workers"][0]["status"] = "draining"
    resources["executions"][0].update(status=status, lastActivityAt="2026-09-18T17:59:00+00:00")
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 1\n" in output
    assert f'{_ACTIVE_METRIC}{{activity="tool_running",work_kind="issue"}} 1\n' in output


@pytest.mark.parametrize("activity,status", [("idle", "idle"), ("waiting_input", "waiting"), ("waiting_approval", "waiting")])
def test_fleet_fresh_nonactive_observations_are_known_zero(activity, status):
    resources = _fleet_resources()
    resources["executions"][0].update(activity=activity, status=status)
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 1\n" in output
    assert f'{_ACTIVE_METRIC}{{activity="tool_running",work_kind="issue"}} 0\n' in output


@pytest.mark.parametrize("claim,status", [("unclaimed", "created"), ("released", "completed")])
def test_fleet_expected_inactive_claims_need_no_fresh_observation(claim, status):
    resources = _fleet_resources()
    row = resources["executions"][0]
    row.update(claimStatus=claim, status=status, resolution={"verifiedApproval": False})
    for key in ("activity", "observationState", "sourceSequence", "lastActivityAt", "lastActivityReceivedAt"):
        row.pop(key)
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 1\n" in output
    assert 'hi_fleet_observation_exclusions{reason="inactive_claim"} 1\n' in output
    assert "approved" not in output


def test_fleet_recovery_uses_no_cached_success_or_dynamic_labels():
    resources = _fleet_resources()
    good, _, _ = _collect_fleet(resources)
    bad_resources = copy.deepcopy(resources)
    bad_resources["executions"][0].update(observationState="reconciliation_required", prompt="private-prompt-canary")
    bad, _, _ = _collect_fleet(bad_resources)
    recovered, _, _ = _collect_fleet(resources)
    assert _fleet_samples(good) == _fleet_samples(recovered)
    assert "hi_fleet_activity_complete{} 0\n" in bad
    assert _ACTIVE_METRIC not in bad
    assert len(_fleet_samples(good)) == 20
    for value in ("private-workspace-canary", "private-prompt-canary", "synthetic-fleet-key", "agent-1", "task-1"):
        assert value not in good + bad + recovered


def test_fleet_input_permutation_does_not_change_classification():
    resources = _fleet_resources()
    second = dict(resources["executions"][0], id="execution-2", executionId="execution-2", agentId="agent-2",
                  taskId="task-2", workspace="workspace-2")
    resources["executions"].append(second)
    resources["sessions"] = [dict(_fleet_session(second), taskId="different")]
    first, _, _ = _collect_fleet(resources)
    resources["executions"].reverse()
    second, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 0\n" in first
    assert _fleet_samples(first) == _fleet_samples(second)


def test_fleet_session_cross_link_cannot_join_different_executions():
    resources = _fleet_resources()
    execution = resources["executions"][0]
    execution["sessionId"] = "session-1"
    resources["sessions"] = [dict(_fleet_session(execution), agentId="agent-2", executionId="execution-2")]
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert _ACTIVE_METRIC not in output
    assert 'hi_fleet_observation_exclusions{reason="ambiguous_agent"} 2\n' in output


def test_fleet_supplied_pool_link_must_match_worker():
    resources = _fleet_resources()
    resources["executions"][0]["poolId"] = "different-pool"
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert 'hi_fleet_observation_exclusions{reason="invalid_record"} 1\n' in output


@pytest.mark.parametrize("field", ["taskId", "workspace"])
def test_fleet_conflicting_task_or_workspace_owners_are_ambiguous(field):
    resources = _fleet_resources()
    first = resources["executions"][0]
    second = dict(first, id="execution-2", executionId="execution-2", agentId="agent-2",
                  taskId="task-2", workspace="workspace-2")
    second[field] = first[field]
    resources["executions"].append(second)
    output, _, _ = _collect_fleet(resources)
    assert "hi_fleet_activity_complete{} 0\n" in output
    assert _ACTIVE_METRIC not in output
    assert 'hi_fleet_observation_exclusions{reason="ambiguous_agent"} 2\n' in output


def test_legacy_collect_defaults_to_no_fleet_network():
    health, legacy = _patch_collect(agents_data={"agents": []})
    with health, legacy, patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("unexpected Fleet network")):
        output = exporter_mod.collect()
    assert "hi_agents_count{} 0\n" in output
    assert _fleet_samples(output) == {"homeric_exporter_fleet_enabled{} 0"}


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
        with patch("urllib.request.urlopen", side_effect=MemoryError("oom")), self.assertRaises(MemoryError):
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
    """Context-manager factory that patches _health_check and _fetch inside collect().

    A ``None`` payload means "upstream fetch failed" (mirroring real
    ``_fetch`` behaviour) and flows into the per-upstream
    ``homeric_exporter_fetch_errors`` tally inside collect(). Empty-dict
    payloads are falsy and skip metric emission without counting as errors.
    """

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
        output = ""
        try:
            with hc_patch, fetch_patch:
                output = exporter_mod.collect()
        except Exception as exc:  # noqa: BLE001 - fail() turns into a clean assertion
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
        """hi_agents_count, hi_agents_online, hi_agents_offline values."""
        lines = {ln.split()[0]: ln.split()[1]
                 for ln in self.output.splitlines()
                 if not ln.startswith("#") and ln.strip()}
        self.assertEqual(lines.get("hi_agents_count{}"), "2")
        self.assertEqual(lines.get("hi_agents_online{}"), "1")
        self.assertEqual(lines.get("hi_agents_offline{}"), "1")

    def test_deprecated_agent_total_alias_emitted(self):
        """hi_agents_total is still emitted as a deprecated alias (#426)."""
        self.assertIn("# HELP hi_agents_total (deprecated, use hi_agents_count)", self.output)
        lines = {ln.split()[0]: ln.split()[1]
                 for ln in self.output.splitlines()
                 if not ln.startswith("#") and ln.strip()}
        self.assertEqual(lines.get("hi_agents_total{}"), "2")

    def test_task_total_correct(self):
        lines = {ln.split()[0]: ln.split()[1]
                 for ln in self.output.splitlines()
                 if not ln.startswith("#") and ln.strip()}
        self.assertEqual(lines.get("hi_tasks_count{}"), "3")

    def test_deprecated_task_total_alias_emitted(self):
        """hi_tasks_total is still emitted as a deprecated alias (#426)."""
        self.assertIn("# HELP hi_tasks_total (deprecated, use hi_tasks_count)", self.output)
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

    def test_nats_bytes_and_jetstream_names_have_no_total(self):
        """Byte and JetStream gauges must not carry the _total counter suffix (#426)."""
        hc_patch, fetch_patch = _patch_collect(
            agents_data=self.agents_data,
            tasks_data=self.tasks_data,
            nats_varz=self.nats_varz,
            nats_jsz={"streams": 2, "consumers": 4, "messages": 100, "bytes": 4096},
        )
        with hc_patch, fetch_patch:
            output = exporter_mod.collect()
        for name in ("nats_in_bytes", "nats_out_bytes",
                     "nats_jetstream_messages", "nats_jetstream_bytes",
                     "nats_jetstream_streams", "nats_jetstream_consumers"):
            self.assertIn(name, output)
            self.assertNotIn(f"{name}_total", output)

    def test_no_gauge_family_carries_counter_total_suffix(self):
        """Full-sweep naming invariant (#426): no gauge family may end in _total.

        _total is reserved for counters per Prometheus naming best practices.
        The exporter is gauge-only; the only permitted exceptions are the two
        deprecated aliases emitted during the #426 rename window.
        """
        deprecated_aliases = {"hi_agents_total", "hi_tasks_total"}
        type_lines = [ln for ln in self.output.splitlines() if ln.startswith("# TYPE")]
        self.assertTrue(type_lines, "collect() output must contain # TYPE lines")
        for ln in type_lines:
            parts = ln.split()
            name, metric_type = parts[2], parts[3]
            if metric_type != "gauge":
                self.fail(f"{name} declared as {metric_type}; this exporter emits gauges only")
            if name.endswith("_total"):
                self.assertIn(
                    name, deprecated_aliases,
                    f"gauge family '{name}' carries the counter-reserved _total suffix",
                )

    def test_deprecated_aliases_are_marked_deprecated(self):
        """Any allowlisted _total alias must carry a (deprecated) HELP marker."""
        help_lines = {ln.split()[2]: ln for ln in self.output.splitlines()
                      if ln.startswith("# HELP")}
        for alias in ("hi_agents_total", "hi_tasks_total"):
            self.assertIn(alias, help_lines)
            self.assertIn("(deprecated", help_lines[alias])

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


class TestHandler(unittest.TestCase):
    def _get_response(self, path: str, mock_collect_output: str = "# TYPE x gauge\nx{} 1\n") -> str:
        with patch.object(exporter_mod, "collect", return_value=mock_collect_output), live_server() as port:
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

    def test_live_server_yields_usable_port(self):
        """The shared live_server() helper yields a positive ephemeral port."""
        with live_server() as port:
            self.assertIsInstance(port, int)
            self.assertGreater(port, 0)

    def test_metrics_body_contains_collect_output(self):
        collect_output = "# TYPE hi_agents_count gauge\nhi_agents_count{} 42\n"
        response = self._get_response("/metrics", mock_collect_output=collect_output)
        self.assertIn("hi_agents_count", response)

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
# Test _METRIC_HELP constants contract
# ---------------------------------------------------------------------------

class TestMetricHelpCoverage(unittest.TestCase):
    """_METRIC_HELP is the single importable source of truth for HELP strings (#420)."""

    def _run_collect(self, **kwargs):
        hc_patch, fetch_patch = _patch_collect(**kwargs)
        # A fully populated collection includes the optional Fleet upstream.
        with (
            hc_patch, fetch_patch,
            patch.dict(os.environ, {"FLEET_METRICS_ENABLED": "true", "AGAMEMNON_API_KEY": "synthetic-fleet-key"}),
            patch("urllib.request.OpenerDirector.open", side_effect=lambda *a, **kw: _make_response({"items": [], "total": 0})),
        ):
            return exporter_mod.collect()

    def test_every_value_is_non_empty_string(self):
        """Every dict value must be a non-empty str so the dict is reusable as-is."""
        for name, text in exporter_mod._METRIC_HELP.items():
            self.assertIsInstance(text, str, f"{name} help is not a str")
            self.assertTrue(text.strip(), f"{name} help string is empty")

    def test_emitted_metrics_all_have_help_entries(self):
        """Every metric emitted by collect() must have a key in _METRIC_HELP."""
        output = self._run_collect(
            nats_varz={
                "connections": 1, "in_msgs": 1, "out_msgs": 1,
                "in_bytes": 1, "out_bytes": 1, "slow_consumers": 0,
            },
            nats_jsz={"streams": 1, "consumers": 1, "messages": 10, "bytes": 1024},
            nestor_stats={"active": 1, "completed": 5, "pending": 0},
            agents_data={
                "agents": [
                    {"name": "a", "host": "h1", "program": "p", "status": "online"},
                ]
            },
            tasks_data={"tasks": [{"status": "completed"}]},
        )
        emitted = {
            line.split()[2] for line in output.splitlines()
            if line.startswith("# TYPE ")
        }
        missing = emitted - set(exporter_mod._METRIC_HELP)
        self.assertEqual(missing, set(),
                         f"Metrics emitted without a _METRIC_HELP entry: {sorted(missing)}")

    def test_help_lines_match_dict_text(self):
        """Each # HELP line's text must equal the canonical _METRIC_HELP value."""
        output = self._run_collect(
            nats_varz={
                "connections": 1, "in_msgs": 1, "out_msgs": 1,
                "in_bytes": 1, "out_bytes": 1, "slow_consumers": 0,
            },
            nestor_stats={"active": 1, "completed": 5, "pending": 0},
        )
        for line in output.splitlines():
            if line.startswith("# HELP "):
                parts = line.split(None, 3)
                name = parts[2]
                self.assertIn(name, exporter_mod._METRIC_HELP)
                self.assertEqual(parts[3], exporter_mod._METRIC_HELP[name],
                                 f"# HELP text for '{name}' drifted from _METRIC_HELP")

    def test_fully_populated_collect_emits_every_dict_key(self):
        """With all upstreams returning data, every _METRIC_HELP key must be emitted."""
        output = self._run_collect(
            nats_varz={
                "connections": 1, "in_msgs": 1, "out_msgs": 1,
                "in_bytes": 1, "out_bytes": 1, "slow_consumers": 0,
            },
            nats_jsz={"streams": 1, "consumers": 1, "messages": 10, "bytes": 1024},
            nestor_stats={"active": 1, "completed": 5, "pending": 0},
            agents_data={
                "agents": [
                    {"name": "a", "host": "h1", "program": "p", "status": "online"},
                    {"name": "b", "host": "h2", "program": "p", "status": "offline"},
                ]
            },
            tasks_data={"tasks": [{"status": "completed"}, {"status": "failed"}]},
        )
        emitted = {
            line.split()[2] for line in output.splitlines()
            if line.startswith("# TYPE ")
        }
        missing = set(exporter_mod._METRIC_HELP) - emitted
        self.assertEqual(missing, set(),
                         f"_METRIC_HELP keys never emitted by collect(): {sorted(missing)}")


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


# ---------------------------------------------------------------------------
# Test collect() — partial upstream failure branches
# ---------------------------------------------------------------------------

_AGENTS_DATA = {
    "agents": [
        {"name": "alpha", "host": "h1", "program": "prog", "status": "online"},
        {"name": "beta",  "host": "h2", "program": "prog", "status": "offline"},
    ]
}
_TASKS_DATA = {"tasks": [{"status": "completed"}, {"status": "failed"}]}
_NESTOR_STATS = {"active": 2, "completed": 10, "pending": 1}
_NATS_VARZ = {
    "connections": 5, "in_msgs": 200, "out_msgs": 180,
    "in_bytes": 2048, "out_bytes": 1024, "slow_consumers": 1,
}
_NATS_JSZ = {"streams": 1, "consumers": 2, "messages": 10, "bytes": 1024}


def _sample_values(output: str) -> dict[str, str]:
    """Map each non-comment exposition line's full key (name + labels) to its value."""
    return {
        ln.split()[0]: ln.split()[1]
        for ln in output.splitlines()
        if not ln.startswith("#") and ln.strip()
    }


def _fetch_error_tally(output: str) -> dict[str, int]:
    """Parse homeric_exporter_fetch_errors{upstream="..."} values from output."""
    return {
        match.group(1): int(match.group(2))
        for match in re.finditer(
            r'homeric_exporter_fetch_errors\{upstream="(\w+)"\} (\d+)', output
        )
    }


class TestCollectPartialFailure(unittest.TestCase):
    """One upstream returning None while the others succeed must omit only
    that upstream's metric families and increment its fetch_errors tally."""

    def _run_collect(self, **kwargs):
        hc_patch, fetch_patch = _patch_collect(**kwargs)
        with hc_patch, fetch_patch:
            return exporter_mod.collect()

    def _all_success(self) -> dict:
        return {
            "agamemnon_health": 1,
            "agents_data": _AGENTS_DATA,
            "tasks_data": _TASKS_DATA,
            "nestor_health": 1,
            "nestor_stats": _NESTOR_STATS,
            "nats_varz": _NATS_VARZ,
            "nats_jsz": _NATS_JSZ,
        }

    def test_agents_fetch_fails_others_succeed(self):
        overrides = self._all_success() | {"agents_data": None}
        output = self._run_collect(**overrides)
        samples = _sample_values(output)
        # Agent family entirely absent (including per-agent samples)
        for name in ("hi_agents_total{}", "hi_agents_online{}", "hi_agents_offline{}"):
            self.assertNotIn(name, samples)
        self.assertNotIn("hi_agent_online{name=\"alpha\",host=\"h1\",program=\"prog\"}", samples)
        # Unrelated families still emitted
        self.assertIn("hi_tasks_total{}", samples)
        self.assertEqual(samples["hi_tasks_total{}"], "2")
        tally = _fetch_error_tally(output)
        self.assertEqual(tally["agamemnon"], 1)
        self.assertEqual(tally["nestor"], 0)
        self.assertEqual(tally["nats"], 0)

    def test_tasks_fetch_fails_others_succeed(self):
        overrides = self._all_success() | {"tasks_data": None}
        output = self._run_collect(**overrides)
        samples = _sample_values(output)
        self.assertNotIn("hi_tasks_total{}", samples)
        self.assertFalse(
            [key for key in samples if key.startswith("hi_tasks_by_status")]
        )
        # Agent gauges unaffected
        self.assertEqual(samples["hi_agents_total{}"], "2")
        self.assertEqual(_fetch_error_tally(output)["agamemnon"], 1)

    def test_both_agamemnon_endpoints_fail_health_still_emitted(self):
        overrides = self._all_success() | {"agents_data": None, "tasks_data": None}
        output = self._run_collect(**overrides)
        samples = _sample_values(output)
        self.assertNotIn("hi_agents_total{}", samples)
        self.assertNotIn("hi_tasks_total{}", samples)
        self.assertIn("hi_agamemnon_health{}", samples)
        self.assertEqual(samples["hi_agamemnon_health{}"], "1")
        self.assertEqual(_fetch_error_tally(output)["agamemnon"], 2)

    def test_nestor_stats_fail_health_still_emitted(self):
        overrides = self._all_success() | {"nestor_stats": None}
        output = self._run_collect(**overrides)
        samples = _sample_values(output)
        for name in (
            "hi_nestor_research_active{}",
            "hi_nestor_research_completed{}",
            "hi_nestor_research_pending{}",
        ):
            self.assertNotIn(name, samples)
        self.assertIn("hi_nestor_health{}", samples)
        self.assertEqual(samples["hi_nestor_health{}"], "1")
        tally = _fetch_error_tally(output)
        self.assertEqual(tally["nestor"], 1)
        self.assertEqual(tally["agamemnon"], 0)

    def test_nats_varz_fail_jsz_succeeds(self):
        overrides = self._all_success() | {"nats_varz": None}
        output = self._run_collect(**overrides)
        samples = _sample_values(output)
        for name in ("nats_connections{}", "nats_in_msgs{}", "nats_out_msgs{}",
                     "nats_in_bytes{}", "nats_out_bytes{}", "nats_slow_consumers{}"):
            self.assertNotIn(name, samples)
        self.assertIn("nats_jetstream_streams{}", samples)
        self.assertEqual(_fetch_error_tally(output)["nats"], 1)

    def test_nats_jsz_fail_varz_succeeds(self):
        overrides = self._all_success() | {"nats_jsz": None}
        output = self._run_collect(**overrides)
        samples = _sample_values(output)
        self.assertFalse(
            [key for key in samples if key.startswith("nats_jetstream_")]
        )
        self.assertIn("nats_connections{}", samples)
        self.assertEqual(_fetch_error_tally(output)["nats"], 1)

    def test_all_upstreams_down_tally_counts_every_endpoint(self):
        """The tally counts failed endpoints per upstream: agamemnon 2, nestor 1, nats 2."""
        output = self._run_collect(
            agamemnon_health=0,
            agents_data=None,
            tasks_data=None,
            nestor_health=0,
            nestor_stats=None,
            nats_varz=None,
            nats_jsz=None,
        )
        tally = _fetch_error_tally(output)
        self.assertEqual(tally, {"agamemnon": 2, "nestor": 1, "nats": 2})
        samples = _sample_values(output)
        self.assertEqual(samples["hi_agamemnon_health{}"], "0")
        self.assertEqual(samples["hi_nestor_health{}"], "0")


def _make_record(msg: str, *args: object, **kwargs) -> logging.LogRecord:
    """Build a real LogRecord so formatter tests exercise the stdlib pipeline."""
    return logging.LogRecord(
        name="homeric-exporter",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args or None,
        exc_info=kwargs.pop("exc_info", None),
    )


class TestJsonFormatter(unittest.TestCase):
    def setUp(self):
        self.formatter = exporter_mod._JsonFormatter()

    def test_output_is_parseable_json(self):
        line = self.formatter.format(_make_record("hello %s", "world"))
        parsed = json.loads(line)
        self.assertEqual(parsed["message"], "hello world")

    def test_required_fields_present(self):
        parsed = json.loads(self.formatter.format(_make_record("boot")))
        self.assertEqual(parsed["level"], "DEBUG")
        self.assertEqual(parsed["logger"], "homeric-exporter")
        self.assertIn("timestamp", parsed)

    def test_timestamp_is_iso8601(self):
        parsed = json.loads(self.formatter.format(_make_record("boot")))
        # Raises ValueError if not valid ISO-8601
        datetime.fromisoformat(parsed["timestamp"])

    def test_lazy_args_resolved_in_message(self):
        record = _make_record("fetch %s failed: %s", "http://x", "timeout")
        parsed = json.loads(self.formatter.format(record))
        self.assertEqual(parsed["message"], "fetch http://x failed: timeout")

    def test_extra_flattened_as_top_level_key(self):
        record = _make_record("request done")
        record.__dict__["path"] = "/metrics"
        parsed = json.loads(self.formatter.format(record))
        self.assertEqual(parsed["path"], "/metrics")

    def test_reserved_collision_gets_ctx_prefix(self):
        record = _make_record("collision test")
        record.__dict__["level"] = "BOGUS"
        parsed = json.loads(self.formatter.format(record))
        self.assertEqual(parsed["ctx_level"], "BOGUS")
        self.assertEqual(parsed["level"], "DEBUG")

    def test_exc_info_rendered_into_exception_field(self):
        try:
            raise ValueError("boom")
        except ValueError:
            record = _make_record("failed", exc_info=sys.exc_info())
        parsed = json.loads(self.formatter.format(record))
        self.assertIn("ValueError: boom", parsed["exception"])

    def test_non_serializable_extra_survives_via_default_str(self):
        record = _make_record("odd payload")
        record.__dict__["blob"] = object()
        parsed = json.loads(self.formatter.format(record))
        self.assertIsInstance(parsed["blob"], str)

    def test_unicode_message_preserved(self):
        parsed = json.loads(
            self.formatter.format(_make_record("café 路径"))
        )
        self.assertEqual(parsed["message"], "café 路径")

    def test_pipeline_through_handler_handle(self):
        """Formatter must compose correctly through the full handler pipeline."""
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(exporter_mod._JsonFormatter())
        record = _make_record("GET %s", "/metrics")
        record.__dict__["status_code"] = "200"
        handler.handle(record)
        handler.flush()
        parsed = json.loads(stream.getvalue().splitlines()[-1])
        self.assertEqual(parsed["status_code"], "200")


class TestLogRequest(unittest.TestCase):
    def test_log_request_emits_structured_extras(self):
        handler, _ = _make_handler("/metrics")
        with patch.object(exporter_mod.log, "debug") as mock_debug:
            handler.log_request(200)
        mock_debug.assert_called_once()
        call = mock_debug.call_args
        extra = call.kwargs["extra"]
        self.assertEqual(extra["client_ip"], "127.0.0.1")
        self.assertEqual(extra["method"], "")
        self.assertEqual(extra["path"], "/metrics")
        self.assertEqual(extra["status_code"], "200")
        self.assertEqual(extra["response_bytes"], "-")

    def test_log_request_parses_method_and_strips_query(self):
        handler, _ = _make_handler("/metrics?collect=all")
        handler.requestline = "GET /metrics?collect=all HTTP/1.1"
        with patch.object(exporter_mod.log, "debug") as mock_debug:
            handler.log_request(200, "1024")
        extra = mock_debug.call_args.kwargs["extra"]
        self.assertEqual(extra["method"], "GET")
        self.assertEqual(extra["path"], "/metrics")
        self.assertEqual(extra["response_bytes"], "1024")

    def test_log_request_tolerates_missing_client_address(self):
        handler, _ = _make_handler("/metrics")
        del handler.client_address
        with patch.object(exporter_mod.log, "debug") as mock_debug:
            handler.log_request()
        self.assertEqual(mock_debug.call_args.kwargs["extra"]["client_ip"], "-")

    def test_log_request_silent_at_info_level(self):
        handler, _ = _make_handler("/metrics")
        original_level = exporter_mod.log.level
        exporter_mod.log.setLevel(logging.INFO)
        try:
            handler.log_request(200)
        finally:
            exporter_mod.log.setLevel(original_level)


# ---------------------------------------------------------------------------
# Test collect() — # HELP line presence and ordering
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
