"""
Validate that all Grafana dashboard JSON files have required fields.
Uses only stdlib: json, pathlib, unittest.
"""
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARDS_DIR = REPO_ROOT / "dashboards"


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


class TestDashboardDocumentation(unittest.TestCase):
    """Every dashboard in dashboards/ must be documented in README.md and AGENTS.md.

    Guards against the doc drift that caused issue #102: a dashboard JSON file
    added to dashboards/ without a corresponding entry in the documentation.
    A dashboard counts as documented when its uid appears verbatim in each doc
    file. CLAUDE.md is a pointer file whose sole content defers to AGENTS.md,
    so AGENTS.md is checked as the authoritative agent-facing documentation.
    """

    DOC_FILES = ("README.md", "AGENTS.md")

    def _dashboard_uids(self) -> dict[str, str]:
        files = get_dashboard_files()
        assert len(files) > 0, "No dashboard files found"
        return {load_json(p)["uid"]: p.name for p in files}

    def test_every_dashboard_uid_documented(self) -> None:
        uids = self._dashboard_uids()
        for doc_name in self.DOC_FILES:
            doc_text = (REPO_ROOT / doc_name).read_text()
            for uid, filename in sorted(uids.items()):
                with self.subTest(doc=doc_name, dashboard=filename):
                    assert uid in doc_text, (
                        f"{filename} (uid: {uid}) is not documented in {doc_name}; "
                        f"add an entry for it (see issues #102 and #231)"
                    )

    def test_documented_count_matches_dashboard_count(self) -> None:
        uids = self._dashboard_uids()
        for doc_name in self.DOC_FILES:
            doc_text = (REPO_ROOT / doc_name).read_text()
            missing = sorted(u for u in uids if u not in doc_text)
            with self.subTest(doc=doc_name):
                assert not missing, (
                    f"{doc_name} documents {len(uids) - len(missing)} of "
                    f"{len(uids)} dashboards; missing: {missing}"
                )


if __name__ == "__main__":
    unittest.main()
