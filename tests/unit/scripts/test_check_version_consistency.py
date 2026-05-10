"""Unit tests for scripts/check-version-consistency.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load the hyphen-named script as a module via importlib
REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = REPO_ROOT / "scripts" / "check-version-consistency.py"
_spec = importlib.util.spec_from_file_location("check_version_consistency", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

check = _mod.check
has_versioned_header = _mod.has_versioned_header
load_version = _mod.load_version
versioned_sections = _mod.versioned_sections


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_pixi(tmp_path: Path, version: str) -> Path:
    """Write a minimal pixi.toml with the given version."""
    content = f'[project]\nname = "test-project"\nversion = "{version}"\n'
    p = tmp_path / "pixi.toml"
    p.write_text(content)
    return p


def _write_changelog(tmp_path: Path, body: str) -> Path:
    """Write a CHANGELOG.md with the given body."""
    p = tmp_path / "CHANGELOG.md"
    p.write_text(body)
    return p


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Return a tmp_path wired up as a fake repo root (pixi.toml + CHANGELOG)."""
    return tmp_path


# ---------------------------------------------------------------------------
# load_version
# ---------------------------------------------------------------------------

def test_load_version_reads_project_version(tmp_path: Path) -> None:
    p = _write_pixi(tmp_path, "1.2.3")
    assert load_version(p) == "1.2.3"


def test_load_version_missing_project_raises(tmp_path: Path) -> None:
    p = tmp_path / "pixi.toml"
    p.write_text('[dependencies]\nfoo = ">=1.0"\n')
    with pytest.raises(KeyError):
        load_version(p)


# ---------------------------------------------------------------------------
# versioned_sections
# ---------------------------------------------------------------------------

CHANGELOG_WITH_SECTIONS = """\
# Changelog

## [Unreleased]

## [1.2.0] - 2026-01-01

### Added
- something

## [1.1.0] - 2025-12-01

[Unreleased]: https://github.com/example/repo/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/example/repo/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/example/repo/releases/tag/v1.1.0
"""

def test_versioned_sections_extracts_all(tmp_path: Path) -> None:
    cl = _write_changelog(tmp_path, CHANGELOG_WITH_SECTIONS)
    assert versioned_sections(cl) == ["1.2.0", "1.1.0"]


def test_versioned_sections_empty_changelog(tmp_path: Path) -> None:
    cl = _write_changelog(tmp_path, "# Changelog\n\n## [Unreleased]\n")
    assert versioned_sections(cl) == []


# ---------------------------------------------------------------------------
# has_versioned_header
# ---------------------------------------------------------------------------

def test_has_versioned_header_found(tmp_path: Path) -> None:
    cl = _write_changelog(tmp_path, CHANGELOG_WITH_SECTIONS)
    assert has_versioned_header("1.2.0", cl) is True


def test_has_versioned_header_not_found(tmp_path: Path) -> None:
    cl = _write_changelog(tmp_path, CHANGELOG_WITH_SECTIONS)
    assert has_versioned_header("9.9.9", cl) is False


# ---------------------------------------------------------------------------
# check() — valid states
# ---------------------------------------------------------------------------

def test_check_version_present_as_released_section(repo: Path) -> None:
    """pixi.toml version appears as a released ## [x.y.z] section — valid."""
    _write_pixi(repo, "1.2.0")
    _write_changelog(repo, CHANGELOG_WITH_SECTIONS)
    assert check(repo) == 0


def test_check_unreleased_state_version_matches_latest_section(repo: Path) -> None:
    """pixi.toml version matches the latest versioned section — still pre-release, valid."""
    _write_pixi(repo, "1.2.0")
    _write_changelog(repo, CHANGELOG_WITH_SECTIONS)
    assert check(repo) == 0


def test_check_pre_first_release_no_sections(repo: Path) -> None:
    """No versioned sections in CHANGELOG yet — pre-first-release, valid."""
    _write_pixi(repo, "0.1.0")
    _write_changelog(repo, "# Changelog\n\n## [Unreleased]\n")
    assert check(repo) == 0


# ---------------------------------------------------------------------------
# check() — invalid states
# ---------------------------------------------------------------------------

def test_check_version_bumped_but_changelog_not_updated(repo: Path) -> None:
    """pixi.toml version bumped to 1.3.0 but CHANGELOG only has 1.2.0 — invalid."""
    _write_pixi(repo, "1.3.0")
    _write_changelog(repo, CHANGELOG_WITH_SECTIONS)
    assert check(repo) == 1


def test_check_version_skipped_ahead(repo: Path) -> None:
    """pixi.toml jumps to 2.0.0 with no CHANGELOG section — invalid."""
    _write_pixi(repo, "2.0.0")
    _write_changelog(repo, CHANGELOG_WITH_SECTIONS)
    assert check(repo) == 1


# ---------------------------------------------------------------------------
# check() — missing files
# ---------------------------------------------------------------------------

def test_check_missing_pixi_toml(repo: Path) -> None:
    _write_changelog(repo, "# Changelog\n\n## [Unreleased]\n")
    # Do NOT write pixi.toml
    assert check(repo) == 1


def test_check_missing_changelog(repo: Path) -> None:
    _write_pixi(repo, "0.1.0")
    # Do NOT write CHANGELOG.md
    assert check(repo) == 1


# ---------------------------------------------------------------------------
# Parametrized edge cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("version,changelog_body,expected", [
    # Exact match as latest section
    (
        "0.1.0",
        "## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n\n### Added\n- init\n",
        0,
    ),
    # No sections pre-release
    (
        "0.1.0",
        "## [Unreleased]\n",
        0,
    ),
    # Version bumped to 0.2.0, changelog only has 0.1.0
    (
        "0.2.0",
        "## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n\n### Added\n- init\n",
        1,
    ),
])
def test_check_parametrized(
    repo: Path,
    version: str,
    changelog_body: str,
    expected: int,
) -> None:
    _write_pixi(repo, version)
    _write_changelog(repo, changelog_body)
    assert check(repo) == expected
