"""
Validate Dependabot and Renovate configuration consistency.

Regression guard for issue #309: every Dependabot ``pip`` entry must point at
a directory containing a real pip manifest, otherwise Dependabot silently
no-ops. Also ensures Renovate's ``pip_requirements`` manager stays disabled so
the two bots do not compete over the same manifest.

Uses only stdlib plus yaml (already a pixi dependency).
"""

import json
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent


def _has_pip_manifest(directory: Path) -> bool:
    """Return True if *directory* contains a pip manifest Dependabot can scan."""
    if not directory.is_dir():
        return False
    if any(directory.glob("requirements*.txt")):
        return True
    pyproject = directory / "pyproject.toml"
    if pyproject.is_file():
        content = pyproject.read_text(encoding="utf-8")
        if "[project]" in content:
            return True
    return False


class TestDependabotConfig(unittest.TestCase):
    def setUp(self):
        with (REPO_ROOT / ".github" / "dependabot.yml").open() as f:
            self.config = yaml.safe_load(f)

    def test_parses_without_error(self):
        assert self.config is not None
        assert self.config["version"] == 2

    def test_has_updates(self):
        assert isinstance(self.config["updates"], list)
        assert len(self.config["updates"]) > 0

    def test_every_referenced_directory_exists(self):
        for entry in self.config["updates"]:
            directory = entry.get("directory", "")
            path = REPO_ROOT / directory.lstrip("/")
            assert path.is_dir(), (
                f"dependabot entry {entry['package-ecosystem']!r} references "
                f"non-existent directory {directory!r}"
            )

    def test_every_pip_entry_points_at_a_pip_manifest(self):
        pip_entries = [
            e for e in self.config["updates"] if e["package-ecosystem"] == "pip"
        ]
        assert len(pip_entries) > 0, "expected at least one pip ecosystem entry"
        for entry in pip_entries:
            directory = REPO_ROOT / entry["directory"].lstrip("/")
            assert _has_pip_manifest(directory), (
                f"pip ecosystem entry points at {entry['directory']!r}, which "
                f"contains no requirements*.txt or [project] pyproject.toml "
                f"(issue #309 regression)"
            )


class TestRenovateConfig(unittest.TestCase):
    def setUp(self):
        with (REPO_ROOT / "renovate.json").open() as f:
            self.config = json.load(f)

    def test_parses_without_error(self):
        assert self.config is not None

    def test_pip_requirements_manager_disabled(self):
        disabled = self.config.get("disabledManagers", [])
        assert "pip_requirements" in disabled, (
            "renovate.json must disable the pip_requirements manager so it "
            "does not compete with Dependabot over jetstream-consumer/"
            "requirements.txt (issue #309)"
        )


if __name__ == "__main__":
    unittest.main()
