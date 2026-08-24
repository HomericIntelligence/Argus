"""
Validate that all Grafana dashboard JSON files have required fields.
Uses only stdlib: json, pathlib, unittest.
"""
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARDS_DIR = REPO_ROOT / "dashboards"

# Metrics the argus-exporter actually emits (see exporter/exporter.py _METRIC_HELP).
EXPORTER_METRICS = {
    "nats_connections",
    "nats_slow_consumers",
    "nats_in_msgs",
    "nats_out_msgs",
    "nats_in_bytes",
    "nats_out_bytes",
    "nats_jetstream_streams",
    "nats_jetstream_consumers",
    "nats_jetstream_messages",
    "nats_jetstream_bytes",
}


def load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def get_dashboard_files():
    return sorted(DASHBOARDS_DIR.glob("*.json"))


class TestDashboardFilesExist(unittest.TestCase):
    def test_dashboards_directory_exists(self):
        assert DASHBOARDS_DIR.is_dir(), f"dashboards/ directory not found at {DASHBOARDS_DIR}"

    def test_at_least_one_dashboard_json(self):
        files = get_dashboard_files()
        assert len(files) > 0, "No .json files found in dashboards/"


class TestAgentHealthDashboard(unittest.TestCase):
    def setUp(self):
        path = DASHBOARDS_DIR / "agent-health.json"
        self.path = path
        self.dashboard = load_json(path)

    def test_parses_without_error(self):
        assert self.dashboard is not None

    def test_has_title(self):
        assert "title" in self.dashboard, f"{self.path.name} missing 'title'"

    def test_title_is_string(self):
        assert isinstance(self.dashboard["title"], str)

    def test_title_not_empty(self):
        assert self.dashboard["title"].strip() != ""

    def test_has_uid(self):
        assert "uid" in self.dashboard, f"{self.path.name} missing 'uid'"

    def test_uid_is_string(self):
        assert isinstance(self.dashboard["uid"], str)

    def test_uid_not_empty(self):
        assert self.dashboard["uid"].strip() != ""

    def test_has_panels(self):
        assert "panels" in self.dashboard, f"{self.path.name} missing 'panels'"

    def test_panels_is_list(self):
        assert isinstance(self.dashboard["panels"], list)

    def test_panels_not_empty(self):
        assert len(self.dashboard["panels"]) > 0

    def test_each_panel_has_id(self):
        for panel in self.dashboard["panels"]:
            assert "id" in panel, f"Panel missing 'id' in {self.path.name}: {panel}"

    def test_each_panel_has_title(self):
        for panel in self.dashboard["panels"]:
            assert "title" in panel, f"Panel missing 'title' in {self.path.name}: {panel}"

    def test_each_panel_has_type(self):
        for panel in self.dashboard["panels"]:
            assert "type" in panel, f"Panel missing 'type' in {self.path.name}: {panel}"


class TestAllDashboards(unittest.TestCase):
    """Generic tests that run against every dashboard JSON in dashboards/."""

    def _check_dashboard(self, path: Path):
        dashboard = load_json(path)
        name = path.name
        assert "title" in dashboard, f"{name}: missing 'title'"
        assert isinstance(dashboard["title"], str) and dashboard["title"].strip(), \
            f"{name}: 'title' must be a non-empty string"
        assert "uid" in dashboard, f"{name}: missing 'uid'"
        assert isinstance(dashboard["uid"], str) and dashboard["uid"].strip(), \
            f"{name}: 'uid' must be a non-empty string"
        assert "panels" in dashboard, f"{name}: missing 'panels'"
        assert isinstance(dashboard["panels"], list), f"{name}: 'panels' must be a list"
        assert len(dashboard["panels"]) > 0, f"{name}: 'panels' must not be empty"

    def test_agent_health_dashboard(self):
        self._check_dashboard(DASHBOARDS_DIR / "agent-health.json")

    def test_nats_events_dashboard(self):
        self._check_dashboard(DASHBOARDS_DIR / "nats-events.json")

    def test_task_throughput_dashboard(self):
        self._check_dashboard(DASHBOARDS_DIR / "task-throughput.json")

    def test_all_json_files_have_required_fields(self):
        """Catch any future dashboards that might be missing required fields."""
        files = get_dashboard_files()
        assert len(files) > 0, "No dashboard files found"
        for path in files:
            with self.subTest(dashboard=path.name):
                self._check_dashboard(path)

    def test_all_dashboard_uids_are_unique(self):
        files = get_dashboard_files()
        uids = []
        for path in files:
            d = load_json(path)
            if "uid" in d:
                uids.append(d["uid"])
        assert len(uids) == len(set(uids)), \
            f"Duplicate dashboard UIDs found: {[u for u in uids if uids.count(u) > 1]}"

    def test_no_dashboards_reference_legacy_gnatsd_metrics(self):
        """Guard against reintroducing the legacy gnatsd_exporter metric prefix."""
        files = get_dashboard_files()
        for path in files:
            dashboard = load_json(path)
            for panel in dashboard.get("panels", []):
                for target in panel.get("targets", []):
                    expr = target.get("expr", "")
                    assert "gnatsd_" not in expr, \
                        f"{path.name}: legacy gnatsd_ metric in expr: {expr}"


class TestNatsEventsDashboardMetrics(unittest.TestCase):
    """Regression tests for issue #431: nats-events panels must query
    exporter-emitted metric names, not legacy gnatsd_varz_* or nonexistent
    *_total counter names."""

    def setUp(self):
        self.dashboard = load_json(DASHBOARDS_DIR / "nats-events.json")

    def _exprs(self) -> list:
        return [
            t["expr"]
            for panel in self.dashboard["panels"]
            for t in panel.get("targets", [])
            if "expr" in t
        ]

    def test_uid_stable(self):
        assert self.dashboard["uid"] == "nats-events"

    def test_no_legacy_gnatsd_varz_references(self):
        for expr in self._exprs():
            assert "gnatsd_varz" not in expr, f"Legacy gnatsd_varz metric in expr: {expr}"

    def test_no_nonexistent_total_counter_names(self):
        pattern = re.compile(r"nats_(in|out)_(msgs|bytes)_total")
        for expr in self._exprs():
            assert not pattern.search(expr), f"Nonexistent _total metric in expr: {expr}"

    def test_no_rate_wrappers_on_rate_gauges(self):
        # nats_in_msgs/nats_out_msgs are gauges already holding per-second rates;
        # wrapping them in rate() computes a derivative of a rate.
        pattern = re.compile(r"rate\(\s*nats_(in|out)_(msgs|bytes)\s*\[")
        for expr in self._exprs():
            assert not pattern.search(expr), f"rate() applied to rate gauge in expr: {expr}"

    def test_nats_metric_references_exist_in_exporter(self):
        pattern = re.compile(r"\bnats_[a-z_]+")
        for expr in self._exprs():
            for match in pattern.findall(expr):
                assert match in EXPORTER_METRICS, (
                    f"expr references unknown metric {match!r}; "
                    f"exporter emits: {sorted(EXPORTER_METRICS)}"
                )


if __name__ == "__main__":
    unittest.main()
