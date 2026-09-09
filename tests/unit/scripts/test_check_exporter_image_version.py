"""Unit tests for scripts/check-exporter-image-version.py and
scripts/bump-exporter-version.sh (Refs #393)."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = REPO_ROOT / "scripts" / "check-exporter-image-version.py"
_spec = importlib.util.spec_from_file_location("check_exporter_image_version", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

load_version = _mod.load_version
compose_tag = _mod.compose_tag
workflow_is_wired = _mod.workflow_is_wired
check = _mod.check

COMPOSE_TEMPLATE = """\
services:
  argus-exporter:
    image: {image_line}
"""

WORKFLOW_OK = """\
      - name: Read exporter version
        run: |
          VERSION="$(tr -d '[:space:]' < exporter/VERSION)"
      - name: Build & push
        tags: |
          ${{ steps.image.outputs.name }}:latest
          ${{ steps.image.outputs.name }}:v${{ steps.version.outputs.version }}
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_repo(
    tmp_path: Path,
    version: str = "0.1.0",
    image_line: str = "${EXPORTER_IMAGE:-ghcr.io/homericintelligence/argus-exporter:v0.1.0}",
    workflow: str | None = WORKFLOW_OK,
    with_version: bool = True,
    with_compose: bool = True,
) -> Path:
    """Build a fake repo root with the three files the checker consumes."""
    if with_version:
        (tmp_path / "exporter").mkdir()
        (tmp_path / "exporter" / "VERSION").write_text(f"{version}\n")
    if with_compose:
        (tmp_path / "docker-compose.yml").write_text(
            COMPOSE_TEMPLATE.format(image_line=image_line)
        )
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    if workflow is not None:
        (wf_dir / "publish-exporter-image.yml").write_text(workflow)
    return tmp_path


# ---------------------------------------------------------------------------
# load_version
# ---------------------------------------------------------------------------


def test_load_version_reads_semver(tmp_path: Path) -> None:
    f = tmp_path / "VERSION"
    f.write_text("1.2.3\n")
    assert load_version(f) == "1.2.3"


@pytest.mark.parametrize("bad", ["", "v1.2.3", "1.2", "1.2.3.4", "abc"])
def test_load_version_rejects_malformed(tmp_path: Path, bad: str) -> None:
    f = tmp_path / "VERSION"
    f.write_text(f"{bad}\n")
    with pytest.raises(ValueError):
        load_version(f)


# ---------------------------------------------------------------------------
# compose_tag
# ---------------------------------------------------------------------------


def test_compose_tag_strips_v_prefix(tmp_path: Path) -> None:
    f = tmp_path / "docker-compose.yml"
    f.write_text(
        COMPOSE_TEMPLATE.format(
            image_line="${EXPORTER_IMAGE:-ghcr.io/homericintelligence/argus-exporter:v1.2.3}"
        )
    )
    assert compose_tag(f) == "1.2.3"


def test_compose_tag_missing_default_raises(tmp_path: Path) -> None:
    f = tmp_path / "docker-compose.yml"
    f.write_text(COMPOSE_TEMPLATE.format(image_line="python:3.11-slim"))
    with pytest.raises(ValueError):
        compose_tag(f)


def test_compose_tag_malformed_ref_raises(tmp_path: Path) -> None:
    f = tmp_path / "docker-compose.yml"
    f.write_text(COMPOSE_TEMPLATE.format(image_line="${EXPORTER_IMAGE:-not-an-image}"))
    with pytest.raises(ValueError):
        compose_tag(f)


# ---------------------------------------------------------------------------
# workflow_is_wired
# ---------------------------------------------------------------------------


def test_workflow_wired_returns_no_errors(tmp_path: Path) -> None:
    f = tmp_path / "publish.yml"
    f.write_text(WORKFLOW_OK)
    assert workflow_is_wired(f) == []


def test_workflow_missing_version_read(tmp_path: Path) -> None:
    f = tmp_path / "publish.yml"
    f.write_text("tags: |\n  img:v${{ steps.version.outputs.version }}\n")
    errors = workflow_is_wired(f)
    assert len(errors) == 1
    assert "does not read exporter/VERSION" in errors[0]


def test_workflow_missing_semver_tag(tmp_path: Path) -> None:
    f = tmp_path / "publish.yml"
    f.write_text("run: cat exporter/VERSION\ntags: |\n  img:latest\n")
    errors = workflow_is_wired(f)
    assert len(errors) == 1
    assert "tags list" in errors[0]


# ---------------------------------------------------------------------------
# check() — end-to-end on a synthetic repo root
# ---------------------------------------------------------------------------


def test_check_all_agree_exits_zero(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    assert check(repo) == 0


def test_check_missing_files_exit_one(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path, with_version=False, with_compose=False, workflow=None)
    assert check(repo) == 1


@pytest.mark.parametrize(
    "version,image_line",
    [
        (
            "0.2.0",
            "${EXPORTER_IMAGE:-ghcr.io/homericintelligence/argus-exporter:v0.1.0}",
        ),
        (
            "0.1.0",
            "${EXPORTER_IMAGE:-ghcr.io/homericintelligence/argus-exporter:v9.9.9}",
        ),
        (
            "0.1.0",
            "${EXPORTER_IMAGE:-ghcr.io/homericintelligence/argus-exporter:9.9.9}",
        ),
    ],
)
def test_check_version_mismatch_exits_one(
    tmp_path: Path, version: str, image_line: str
) -> None:
    repo = _write_repo(tmp_path, version=version, image_line=image_line)
    assert check(repo) == 1


def test_check_workflow_drift_exits_one(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path, workflow=None)
    assert check(repo) == 1


# ---------------------------------------------------------------------------
# bump-exporter-version.sh — next-version math and exact-once replacement
# ---------------------------------------------------------------------------

BUMP_SCRIPT = REPO_ROOT / "scripts" / "bump-exporter-version.sh"


class TestBumpScriptNextVersion:
    """Exercise the bump script's math against a synthetic git repo tree."""

    def _init_fake_repo(self, tmp_path: Path) -> Path:
        repo = _write_repo(tmp_path)
        # The bump script invokes the checker relative to its git toplevel;
        # mirror a real checkout by copying it into the fake repo.
        scripts_dir = repo / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "check-exporter-image-version.py").write_text(
            _SCRIPT.read_text()
        )
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"], check=True
        )
        subprocess.run(["git", "-C", str(repo), "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "init"], cwd=repo, check=True
        )
        return repo

    @pytest.mark.parametrize(
        "bump_type,current,expected",
        [
            ("patch", "0.1.0", "0.1.1"),
            ("minor", "0.1.0", "0.2.0"),
            ("major", "0.1.0", "1.0.0"),
        ],
    )
    def test_bump_updates_both_files(
        self, tmp_path: Path, bump_type: str, current: str, expected: str
    ) -> None:
        repo = self._init_fake_repo(tmp_path)
        (repo / "exporter" / "VERSION").write_text(f"{current}\n")
        (repo / "docker-compose.yml").write_text(
            COMPOSE_TEMPLATE.format(
                image_line=(
                    "${EXPORTER_IMAGE:-ghcr.io/homericintelligence/"
                    f"argus-exporter:v{current}}}"
                )
            )
        )
        result = subprocess.run(
            ["bash", str(BUMP_SCRIPT), bump_type],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (repo / "exporter" / "VERSION").read_text().strip() == expected
        assert (
            f"argus-exporter:v{expected}" in (repo / "docker-compose.yml").read_text()
        )

    def test_dry_run_leaves_files_untouched(self, tmp_path: Path) -> None:
        repo = self._init_fake_repo(tmp_path)
        result = subprocess.run(
            ["bash", str(BUMP_SCRIPT), "patch", "--dry-run"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (repo / "exporter" / "VERSION").read_text().strip() == "0.1.0"
        assert "argus-exporter:v0.1.0" in (repo / "docker-compose.yml").read_text()

    def test_fails_when_compose_pin_missing(self, tmp_path: Path) -> None:
        repo = self._init_fake_repo(tmp_path)
        (repo / "docker-compose.yml").write_text(
            COMPOSE_TEMPLATE.format(image_line="python:3.11-slim")
        )
        result = subprocess.run(
            ["bash", str(BUMP_SCRIPT), "patch"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "expected exactly one" in result.stderr

    def test_rejects_invalid_bump_type(self, tmp_path: Path) -> None:
        repo = self._init_fake_repo(tmp_path)
        result = subprocess.run(
            ["bash", str(BUMP_SCRIPT), "bogus"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
