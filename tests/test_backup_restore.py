"""
Tests for backup.sh and restore.sh — verifies that scripts use ${CONTAINER_CMD:-docker}
instead of hardcoded `docker run`, and that the justfile exports CONTAINER_CMD correctly.
"""

import os
import re
import shlex
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
JUSTFILE = REPO_ROOT / "justfile"


def script_content(name: str) -> str:
    return (SCRIPTS_DIR / name).read_text()


# ---------------------------------------------------------------------------
# backup.sh
# ---------------------------------------------------------------------------


def test_backup_sh_no_bare_docker_run() -> None:
    """backup.sh must not contain a hardcoded `docker run` invocation."""
    content = script_content("backup.sh")
    # Allow 'docker' inside ${CONTAINER_CMD:-docker} but not as a standalone command
    bare = re.search(r"(?<!\{CONTAINER_CMD:-)\bdocker\s+run\b", content)
    assert bare is None, "backup.sh still contains a hardcoded 'docker run'"


def test_backup_sh_uses_container_cmd() -> None:
    """backup.sh must use ${CONTAINER_CMD:-docker} run."""
    content = script_content("backup.sh")
    assert "${CONTAINER_CMD:-docker} run" in content


def test_backup_sh_syntax() -> None:
    """backup.sh must pass bash -n syntax check."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPTS_DIR / "backup.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# restore.sh
# ---------------------------------------------------------------------------


def test_restore_sh_no_bare_docker_run() -> None:
    """restore.sh must not contain a hardcoded `docker run` invocation."""
    content = script_content("restore.sh")
    bare = re.search(r"(?<!\{CONTAINER_CMD:-)\bdocker\s+run\b", content)
    assert bare is None, "restore.sh still contains a hardcoded 'docker run'"


def test_restore_sh_uses_container_cmd() -> None:
    """restore.sh must use ${CONTAINER_CMD:-docker} run."""
    content = script_content("restore.sh")
    assert "${CONTAINER_CMD:-docker} run" in content


def test_restore_sh_syntax() -> None:
    """restore.sh must pass bash -n syntax check."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPTS_DIR / "restore.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# justfile
# ---------------------------------------------------------------------------


def test_justfile_defines_container_cmd() -> None:
    """justfile must define a container_cmd variable."""
    content = JUSTFILE.read_text()
    assert "container_cmd" in content


def test_justfile_backup_exports_container_cmd() -> None:
    """The backup recipe must pass CONTAINER_CMD={{container_cmd}} to the script."""
    content = JUSTFILE.read_text()
    assert "CONTAINER_CMD={{container_cmd}} ./scripts/backup.sh" in content


def test_justfile_restore_exports_container_cmd() -> None:
    """The restore recipe must pass CONTAINER_CMD={{container_cmd}} to the script."""
    content = JUSTFILE.read_text()
    assert "CONTAINER_CMD={{container_cmd}} ./scripts/restore.sh" in content


def test_justfile_container_cmd_matches_compose_cmd_runtime() -> None:
    """container_cmd and compose_cmd must resolve to the same runtime (podman or docker)."""
    content = JUSTFILE.read_text()
    # Both must key off the same condition (podman-compose presence)
    assert content.count("command -v podman-compose") >= 2, (
        "container_cmd and compose_cmd should both detect podman-compose"
    )


@pytest.mark.parametrize(
    "recipe,flag",
    [
        ("backup", "CONTAINER_CMD"),
        ("restore", "CONTAINER_CMD"),
    ],
)
def test_justfile_recipes_pass_container_cmd(recipe: str, flag: str) -> None:
    """Parametrised check that backup and restore recipes both forward CONTAINER_CMD."""
    content = JUSTFILE.read_text()
    # Find the recipe block and assert flag appears in it
    pattern = rf"{recipe}[^\n]*\n\s+{flag}="
    assert re.search(pattern, content), f"Recipe '{recipe}' does not export {flag}"


# ---------------------------------------------------------------------------
# Integration: runtime CONTAINER_CMD resolution
# ---------------------------------------------------------------------------


@dataclass
class StubEnv:
    """A hermetic stub environment: stub binaries on a scrubbed PATH plus
    copies of the real scripts so `$(dirname "$0")/..` resolves into tmp."""

    bin_dir: Path
    calls_log: Path
    script_dir: Path

    def env(self, **extra: str) -> dict[str, str]:
        e = {"PATH": f"{self.bin_dir}:/usr/bin:/bin", "HOME": str(self.bin_dir.parent)}
        e.update(extra)
        return e

    def calls(self) -> list[str]:
        return (
            self.calls_log.read_text().splitlines() if self.calls_log.exists() else []
        )


def _make_stub(path: Path, name: str, calls_log: Path) -> None:
    body = (
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"{name} $*\" >> {shlex.quote(str(calls_log))}\n"
        "exit 0\n"
    )
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def make_stub_env(tmp_path: Path) -> StubEnv:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_log = tmp_path / "calls.log"
    for name in ("podman", "docker", "somefake"):
        _make_stub(bin_dir / name, name, calls_log)
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    for sh in ("backup.sh", "restore.sh"):
        shutil.copy2(SCRIPTS_DIR / sh, script_dir / sh)
    return StubEnv(bin_dir=bin_dir, calls_log=calls_log, script_dir=script_dir)


def _run(
    stub: StubEnv, script: str, *args: str, **env_overrides: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(stub.script_dir / script), *args],
        env=stub.env(**env_overrides),
        capture_output=True,
        text=True,
        check=False,
    )


def _matches(calls: list[str], pattern: str) -> list[str]:
    return [c for c in calls if re.fullmatch(pattern, c)]


def _count(calls: list[str], pattern: str) -> int:
    return len(_matches(calls, pattern))


def test_backup_runtime_uses_podman_when_container_cmd_set(tmp_path: Path) -> None:
    stub = make_stub_env(tmp_path)
    assert shutil.which("podman", path=stub.env()["PATH"]) == str(
        stub.bin_dir / "podman"
    )
    result = _run(stub, "backup.sh", CONTAINER_CMD="podman")
    assert result.returncode == 0, (result.stdout, result.stderr)
    calls = stub.calls()
    assert _count(calls, r"podman run .*") == 2, calls
    assert _count(calls, r"docker run .*") == 0, calls


def test_backup_runtime_falls_back_to_docker_when_container_cmd_unset(
    tmp_path: Path,
) -> None:
    stub = make_stub_env(tmp_path)
    result = _run(stub, "backup.sh")
    assert result.returncode == 0, (result.stdout, result.stderr)
    calls = stub.calls()
    assert _count(calls, r"docker run .*") == 2, calls
    assert _count(calls, r"podman run .*") == 0, calls


def test_backup_runtime_honours_arbitrary_container_cmd(tmp_path: Path) -> None:
    stub = make_stub_env(tmp_path)
    result = _run(stub, "backup.sh", CONTAINER_CMD="somefake")
    assert result.returncode == 0, (result.stdout, result.stderr)
    calls = stub.calls()
    assert _count(calls, r"somefake run .*") == 2, calls


def test_backup_runtime_does_not_pollute_repo_backups(tmp_path: Path) -> None:
    backups_dir = REPO_ROOT / "backups"
    before = sorted(os.listdir(backups_dir)) if backups_dir.exists() else None
    stub = make_stub_env(tmp_path)
    result = _run(stub, "backup.sh")
    assert result.returncode == 0, (result.stdout, result.stderr)
    after = sorted(os.listdir(backups_dir)) if backups_dir.exists() else None
    assert after == before


def test_restore_runtime_uses_podman_when_container_cmd_set(tmp_path: Path) -> None:
    stub = make_stub_env(tmp_path)
    fake = tmp_path / "fake.tar.gz"
    fake.write_bytes(b"stub")
    result = _run(stub, "restore.sh", "unknown_vol", str(fake), CONTAINER_CMD="podman")
    assert result.returncode == 0, (result.stdout, result.stderr)
    calls = stub.calls()
    assert _count(calls, r"podman run .*") == 1, calls
    assert _count(calls, r"docker run .*") == 0, calls
    assert _count(calls, r"docker compose .*") == 0, calls


def test_restore_runtime_falls_back_to_docker_when_container_cmd_unset(
    tmp_path: Path,
) -> None:
    stub = make_stub_env(tmp_path)
    fake = tmp_path / "fake.tar.gz"
    fake.write_bytes(b"stub")
    result = _run(stub, "restore.sh", "unknown_vol", str(fake))
    assert result.returncode == 0, (result.stdout, result.stderr)
    calls = stub.calls()
    assert _count(calls, r"docker run .*") == 1, calls
    assert _count(calls, r"podman run .*") == 0, calls


def test_restore_runtime_rejects_missing_backup_file(tmp_path: Path) -> None:
    stub = make_stub_env(tmp_path)
    missing = tmp_path / "does_not_exist.tar.gz"
    result = _run(stub, "restore.sh", "unknown_vol", str(missing))
    assert result.returncode == 1, (result.stdout, result.stderr)
    calls = stub.calls() if stub.calls_log.exists() else []
    assert _count(calls, r"\w+ run .*") == 0, calls


RESTORE_SRC = (SCRIPTS_DIR / "restore.sh").read_text()


def test_restore_sh_compose_branch_uses_hardcoded_docker_known_limitation(
    tmp_path: Path,
) -> None:
    # KNOWN LIMITATION: restore.sh hardcodes `docker compose` in the stop line and
    # the EXIT trap instead of `${CONTAINER_CMD:-docker} compose`. This test documents
    # current behavior so a partial or full fix triggers a visible failure. If
    # restore.sh is fixed to route compose through CONTAINER_CMD, flip the
    # docker/podman expectations on the four assertions below.
    assert "[prometheus_data]=prometheus" in RESTORE_SRC, (
        "VOLUME_TO_SERVICE map changed — update this test"
    )
    stub = make_stub_env(tmp_path)
    fake = tmp_path / "fake.tar.gz"
    fake.write_bytes(b"stub")
    result = _run(
        stub, "restore.sh", "prometheus_data", str(fake), CONTAINER_CMD="podman"
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    calls = stub.calls()
    assert (
        _count(calls, r"docker compose --project-directory .* stop prometheus") == 1
    ), calls
    assert (
        _count(calls, r"docker compose --project-directory .* start prometheus") == 1
    ), calls
    assert _count(calls, r"podman run .*") == 1, calls
    assert _count(calls, r"podman compose .*") == 0, calls
