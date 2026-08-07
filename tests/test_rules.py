"""
Validate that Prometheus alerting and recording rules YAML files parse correctly
and have required fields.
Uses only stdlib: yaml, pathlib, unittest.
"""
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
RULES_DIR = REPO_ROOT / "rules"


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


class TestRulesDirectoryExists(unittest.TestCase):
    def test_rules_directory_exists(self):
        assert RULES_DIR.is_dir(), f"rules/ directory not found at {RULES_DIR}"

    def test_agent_alerts_file_exists(self):
        assert (RULES_DIR / "agent-alerts.yml").is_file()

    def test_recording_rules_file_exists(self):
        assert (RULES_DIR / "recording-rules.yml").is_file()


class TestAgentAlertsRules(unittest.TestCase):
    def setUp(self):
        self.rules = load_yaml(RULES_DIR / "agent-alerts.yml")

    def test_parses_without_error(self):
        assert self.rules is not None

    def test_has_groups_key(self):
        assert "groups" in self.rules

    def test_groups_is_list(self):
        assert isinstance(self.rules["groups"], list)

    def test_groups_not_empty(self):
        assert len(self.rules["groups"]) > 0

    def test_each_group_has_name(self):
        for group in self.rules["groups"]:
            assert "name" in group, f"Group missing 'name': {group}"

    def test_each_group_has_rules(self):
        for group in self.rules["groups"]:
            assert "rules" in group, f"Group '{group.get('name')}' missing 'rules'"

    def test_each_group_rules_is_list(self):
        for group in self.rules["groups"]:
            assert isinstance(group["rules"], list), \
                f"Group '{group.get('name')}' 'rules' must be a list"

    def test_each_alert_has_alert_name(self):
        for group in self.rules["groups"]:
            for rule in group["rules"]:
                if "alert" in rule:
                    assert isinstance(rule["alert"], str), \
                        f"Alert name must be a string: {rule}"
                    assert rule["alert"].strip() != "", \
                        f"Alert name must not be empty: {rule}"

    def test_each_alert_has_expr(self):
        for group in self.rules["groups"]:
            for rule in group["rules"]:
                if "alert" in rule:
                    assert "expr" in rule, \
                        f"Alert '{rule.get('alert')}' missing 'expr'"

    def test_each_alert_has_labels(self):
        for group in self.rules["groups"]:
            for rule in group["rules"]:
                if "alert" in rule:
                    assert "labels" in rule, \
                        f"Alert '{rule.get('alert')}' missing 'labels'"

    def test_each_alert_severity_is_valid(self):
        valid_severities = {"critical", "warning", "info"}
        for group in self.rules["groups"]:
            for rule in group["rules"]:
                if "alert" in rule and "labels" in rule:
                    severity = rule["labels"].get("severity")
                    if severity is not None:
                        assert severity in valid_severities, \
                            f"Alert '{rule.get('alert')}' has invalid severity '{severity}'"

    def test_each_alert_has_annotations(self):
        for group in self.rules["groups"]:
            for rule in group["rules"]:
                if "alert" in rule:
                    assert "annotations" in rule, \
                        f"Alert '{rule.get('alert')}' missing 'annotations'"

    def test_each_alert_annotation_has_summary(self):
        for group in self.rules["groups"]:
            for rule in group["rules"]:
                if "alert" in rule and "annotations" in rule:
                    assert "summary" in rule["annotations"], \
                        f"Alert '{rule.get('alert')}' annotations missing 'summary'"


class TestRecordingRules(unittest.TestCase):
    def setUp(self):
        self.rules = load_yaml(RULES_DIR / "recording-rules.yml")

    def test_parses_without_error(self):
        assert self.rules is not None

    def test_has_groups_key(self):
        assert "groups" in self.rules

    def test_groups_is_list(self):
        assert isinstance(self.rules["groups"], list)

    def test_groups_not_empty(self):
        assert len(self.rules["groups"]) > 0

    def test_each_group_has_name(self):
        for group in self.rules["groups"]:
            assert "name" in group, f"Group missing 'name': {group}"

    def test_each_group_has_rules(self):
        for group in self.rules["groups"]:
            assert "rules" in group, f"Group '{group.get('name')}' missing 'rules'"

    def test_each_recording_rule_has_record_and_expr(self):
        for group in self.rules["groups"]:
            for rule in group["rules"]:
                if "record" in rule:
                    assert "expr" in rule, \
                        f"Recording rule '{rule.get('record')}' missing 'expr'"
                    assert isinstance(rule["record"], str), \
                        f"Recording rule 'record' must be a string: {rule}"
                    assert rule["record"].strip() != "", \
                        f"Recording rule 'record' must not be empty: {rule}"

    def test_recording_rule_names_follow_convention(self):
        """Recording rule names should follow the Prometheus naming convention: level:metric:operation."""
        for group in self.rules["groups"]:
            for rule in group["rules"]:
                if "record" in rule:
                    name = rule["record"]
                    colon_count = name.count(":")
                    assert colon_count >= 1, \
                        f"Recording rule '{name}' should follow level:metric:operation convention"


class TestAllRulesFiles(unittest.TestCase):
    """Generic test that all YAML files in rules/ parse and have 'groups'."""

    def test_all_yml_files_parse_and_have_groups(self):
        yml_files = sorted(RULES_DIR.glob("*.yml"))
        assert len(yml_files) > 0, "No .yml files found in rules/"
        for path in yml_files:
            with self.subTest(rules_file=path.name):
                data = load_yaml(path)
                assert data is not None, f"{path.name} parsed as None"
                assert "groups" in data, f"{path.name} missing top-level 'groups' key"
                assert isinstance(data["groups"], list), \
                    f"{path.name}: 'groups' must be a list"


if __name__ == "__main__":
    unittest.main()
