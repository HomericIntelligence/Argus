"""
Tests for scripts/bump-version.sh empty-changelog handling (issue #399).

Verifies that running the bump script when no commits exist since the last
v* tag aborts by default with a warning (leaving the tree untouched), and
that --allow-empty / BUMP_ALLOW_EMPTY=1 insert a placeholder section instead.

The throwaway repos created here must be built with a hermetic git
environment; see HERMETIC_GIT_ENV below for why inheriting the developer's
ambient git configuration can hang the whole suite.
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

# Git configuration forced for every `git` invocation these tests make.
#
# The tests shell out to a real `git`, so by default they inherit the
# contributor's `~/.gitconfig`. That is not hypothetical: with a global
# `tag.gpgsign = true` (a common setting for signing releases) the fixture's
# `git tag v0.1.0` stops being a lightweight tag and becomes an annotated tag
# that needs a message, so git launches $EDITOR. pytest hands children a
# non-TTY stdin, so a terminal editor such as vim blocks forever and the suite
# hangs instead of failing. `commit.gpgsign = true` is the same hazard for the
# fixture's commits and for bump-version.sh's own commit.
#
# Pointing GIT_CONFIG_GLOBAL at os.devnull and setting GIT_CONFIG_NOSYSTEM
# drops the ambient config entirely; GIT_EDITOR=false and
# GIT_TERMINAL_PROMPT=0 turn any residual prompt into a loud failure rather
# than a block. A hang becomes a visible test failure.
HERMETIC_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_EDITOR": "false",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git_env(**overrides: str) -> dict[str, str]:
    """Environment for a `git`/script subprocess, hermetic by default.

    HERMETIC_GIT_ENV is applied last so its keys cannot be overridden, even by
    a hostile value inherited from os.environ or passed in by a caller.
    """
    env = dict(os.environ)
    env.update(overrides)
    env.update(HERMETIC_GIT_ENV)
    return env


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
    )


def _seed_repo(repo: Path) -> Path:
    """Create a throwaway git repo seeded at v0.1.0 with no commits since the tag."""
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPTS_DIR / "bump-version.sh", repo / "scripts" / "bump-version.sh")
    shutil.copy(
        SCRIPTS_DIR / "generate-changelog.sh",
        repo / "scripts" / "generate-changelog.sh",
    )
    (repo / "pixi.toml").write_text(PIXI_TOML_TEMPLATE.format(version="0.1.0"))
    (repo / "CHANGELOG.md").write_text(CHANGELOG_TEMPLATE)

    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "feat: initial release")
    _run_git(repo, "tag", "v0.1.0")
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo seeded at v0.1.0 with no commits since the tag."""
    return _seed_repo(tmp_path)


def run_bump(repo: Path, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run bump-version.sh inside the throwaway repo."""
    env = _git_env() if not extra_env else _git_env(**extra_env)
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
            env=_git_env(),
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


class TestHermeticGitEnvironment:
    """Regression guard for the suite hang caused by ambient git config.

    A global `tag.gpgsign = true` turned the fixture's `git tag v0.1.0` into an
    annotated tag, so git opened $EDITOR and vim blocked forever on pytest's
    non-TTY stdin. These tests plant exactly that hostile configuration plus an
    editor script, then assert the fixture still builds a lightweight tag
    without ever invoking the editor.

    The planted editor exits immediately (rather than sleeping) so that if the
    hardening is ever removed this fails loudly instead of hanging again.
    """

    @staticmethod
    def _plant_hostile_config(tmp_path: Path) -> dict[str, str]:
        """A global config forcing annotated tags, plus a non-blocking editor."""
        global_cfg = tmp_path / "hostile-gitconfig"
        global_cfg.write_text(
            "[tag]\n\tgpgsign = true\n[commit]\n\tgpgsign = true\n"
        )
        editor = tmp_path / "hostile-editor.sh"
        editor.write_text(
            "#!/bin/sh\n"
            f"touch {tmp_path / 'editor-was-invoked'}\n"
            "echo 'git must not invoke an editor in these tests' >&2\n"
            "exit 1\n"
        )
        editor.chmod(0o755)
        return {
            "GIT_CONFIG_GLOBAL": str(global_cfg),
            "GIT_EDITOR": str(editor),
            "EDITOR": str(editor),
        }

    def test_hostile_config_does_not_reach_the_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # monkeypatch keeps the hostile values scoped to this test.
        for key, value in self._plant_hostile_config(tmp_path).items():
            monkeypatch.setenv(key, value)

        repo = _seed_repo(tmp_path / "repo")

        # A lightweight tag is a `commit` object. An annotated tag would be a
        # `tag` object and could only have been written via the editor.
        kind = subprocess.run(
            ["git", "cat-file", "-t", "v0.1.0"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            env=_git_env(),
        )
        assert kind.stdout.strip() == "commit"
        assert not (tmp_path / "editor-was-invoked").exists()

    def test_hermetic_env_overrides_ambient_values(self) -> None:
        env = _git_env(
            GIT_CONFIG_GLOBAL="/nonexistent/ambient", LOKI_AUTH_USER="u"
        )
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert env["GIT_EDITOR"] == "false"
        # Overrides for keys that are not hermetic still apply.
        assert env["LOKI_AUTH_USER"] == "u"
