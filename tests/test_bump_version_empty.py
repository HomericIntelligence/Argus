"""
Tests for scripts/bump-version.sh empty-changelog handling (issue #399).

Verifies that running the bump script when no commits exist since the last
v* tag aborts by default with a warning (leaving the tree untouched), and
that --allow-empty / BUMP_ALLOW_EMPTY=1 insert a placeholder section instead.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

PIXI_TOML_TEMPLATE = """\
[workspace]
name = "argus-test"
version = "{version}"
"""

CHANGELOG_TEMPLATE = """\
# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.1.0] - 2026-01-01

### Added

- Initial release

[Unreleased]: https://github.com/test/repo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/test/repo/releases/tag/v0.1.0
"""


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo seeded at v0.1.0 with no commits since the tag."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPTS_DIR / "bump-version.sh", tmp_path / "scripts" / "bump-version.sh")
    shutil.copy(
        SCRIPTS_DIR / "generate-changelog.sh",
        tmp_path / "scripts" / "generate-changelog.sh",
    )
    (tmp_path / "pixi.toml").write_text(PIXI_TOML_TEMPLATE.format(version="0.1.0"))
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG_TEMPLATE)

    _run_git(tmp_path, "init", "-b", "main")
    _run_git(tmp_path, "config", "user.email", "test@example.com")
    _run_git(tmp_path, "config", "user.name", "Test User")
    _run_git(tmp_path, "add", ".")
    _run_git(tmp_path, "commit", "-m", "feat: initial release")
    _run_git(tmp_path, "tag", "v0.1.0")
    return tmp_path


def run_bump(repo: Path, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run bump-version.sh inside the throwaway repo."""
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "scripts/bump-version.sh", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestEmptyChangelogAbortsByDefault:
    def test_aborts_nonzero(self, repo: Path) -> None:
        result = run_bump(repo, "patch")
        assert result.returncode != 0
        assert "no commits since last" in result.stderr

    def test_pixi_toml_unchanged(self, repo: Path) -> None:
        run_bump(repo, "patch")
        assert 'version = "0.1.0"' in (repo / "pixi.toml").read_text()

    def test_changelog_unchanged(self, repo: Path) -> None:
        before = (repo / "CHANGELOG.md").read_bytes()
        run_bump(repo, "patch")
        assert (repo / "CHANGELOG.md").read_bytes() == before

    def test_working_tree_clean_after_abort(self, repo: Path) -> None:
        run_bump(repo, "patch")
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert status.stdout.strip() == ""


class TestAllowEmptyFlag:
    def test_flag_inserts_placeholder(self, repo: Path) -> None:
        result = run_bump(repo, "patch", "--allow-empty")
        assert result.returncode == 0, result.stderr
        changelog = (repo / "CHANGELOG.md").read_text()
        assert "(no changes since last release)" in changelog
        assert "### Changed" in changelog

    def test_flag_bumps_version(self, repo: Path) -> None:
        result = run_bump(repo, "patch", "--allow-empty")
        assert result.returncode == 0, result.stderr
        assert 'version = "0.1.1"' in (repo / "pixi.toml").read_text()

    def test_env_var_equivalent(self, repo: Path) -> None:
        result = run_bump(repo, "patch", extra_env={"BUMP_ALLOW_EMPTY": "1"})
        assert result.returncode == 0, result.stderr
        changelog = (repo / "CHANGELOG.md").read_text()
        assert "(no changes since last release)" in changelog
        assert 'version = "0.1.1"' in (repo / "pixi.toml").read_text()


class TestNonEmptyRegression:
    def test_populated_section_still_works(self, repo: Path) -> None:
        (repo / "feature.txt").write_text("x")
        _run_git(repo, "add", ".")
        _run_git(repo, "commit", "-m", "feat: new thing")

        result = run_bump(repo, "patch")
        assert result.returncode == 0, result.stderr

        changelog = (repo / "CHANGELOG.md").read_text()
        assert "new thing" in changelog
        assert "(no changes since last release)" not in changelog
        assert 'version = "0.1.1"' in (repo / "pixi.toml").read_text()


class TestArgParsing:
    @pytest.mark.parametrize(
        "args",
        [
            (),
            ("--allow-empty",),
            ("bogus",),
            ("patch", "extra"),
        ],
    )
    def test_invalid_usage_exits_nonzero(self, repo: Path, args: tuple[str, ...]) -> None:
        result = run_bump(repo, *args)
        assert result.returncode != 0

    def test_flag_and_type_in_any_order(self, repo: Path) -> None:
        result = run_bump(repo, "--allow-empty", "patch")
        assert result.returncode == 0, result.stderr
        assert "(no changes since last release)" in (repo / "CHANGELOG.md").read_text()


class TestJustfileForwarding:
    def test_justfile_uses_variadic_args(self) -> None:
        content = (REPO_ROOT / "justfile").read_text()
        assert "bump *ARGS:" in content
