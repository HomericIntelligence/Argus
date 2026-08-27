"""Regression tests for the deterministic lint dependency audit input."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import tomllib
import yaml

REPO_ROOT = Path(__file__).parent.parent
AUDIT_REQUIREMENTS = REPO_ROOT / "requirements-lint.txt"


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _audit_pins() -> dict[str, str]:
    assert AUDIT_REQUIREMENTS.is_file(), "requirements-lint.txt is missing"
    pins: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        AUDIT_REQUIREMENTS.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)", line)
        assert match is not None, (
            f"requirements-lint.txt:{line_number} must be an exact name==version pin"
        )
        name = _canonical_name(match.group(1))
        assert name not in pins, f"duplicate audit requirement: {name}"
        pins[name] = match.group(2)
    return pins


def _lint_pypi_versions_by_platform() -> dict[str, dict[str, str]]:
    lock = yaml.safe_load((REPO_ROOT / "pixi.lock").read_text(encoding="utf-8"))
    package_by_url = {
        package["pypi"]: (
            _canonical_name(package["name"]),
            str(package["version"]),
        )
        for package in lock["packages"]
        if "pypi" in package
    }

    versions_by_platform: dict[str, dict[str, str]] = {}
    for platform, package_refs in lock["environments"]["lint"]["packages"].items():
        versions_by_platform[platform] = {
            package_by_url[ref["pypi"]][0]: package_by_url[ref["pypi"]][1]
            for ref in package_refs
            if "pypi" in ref
        }
    return versions_by_platform


def test_pip_audit_task_targets_the_committed_manifest() -> None:
    with (REPO_ROOT / "pixi.toml").open("rb") as manifest:
        pixi = tomllib.load(manifest)

    task = pixi["feature"]["lint"]["tasks"]["pip-audit"]
    assert shlex.split(task) == ["pip-audit", "-r", "requirements-lint.txt"]


def test_audit_manifest_covers_each_declared_lint_pypi_dependency() -> None:
    with (REPO_ROOT / "pixi.toml").open("rb") as manifest:
        pixi = tomllib.load(manifest)

    declared = {
        _canonical_name(name) for name in pixi["feature"]["lint"]["pypi-dependencies"]
    }
    assert set(_audit_pins()) == declared


def test_audit_manifest_pins_match_every_locked_platform() -> None:
    pins = _audit_pins()
    versions_by_platform = _lint_pypi_versions_by_platform()
    with (REPO_ROOT / "pixi.toml").open("rb") as manifest:
        pixi = tomllib.load(manifest)

    assert set(versions_by_platform) == set(pixi["workspace"]["platforms"])

    for platform, locked_versions in versions_by_platform.items():
        with_platform = {
            name: locked_versions.get(name) for name in pins if name in locked_versions
        }
        assert with_platform == pins, (
            f"audit pins do not match {platform}: {with_platform}"
        )
