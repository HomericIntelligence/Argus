"""
Tests for backup.sh and restore.sh — verifies that scripts use ${CONTAINER_CMD:-docker}
instead of hardcoded `docker run`, and that the justfile exports CONTAINER_CMD correctly.
"""
import re
import subprocess
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
    bare = re.search(r'(?<!\{CONTAINER_CMD:-)\bdocker\s+run\b', content)
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
    bare = re.search(r'(?<!\{CONTAINER_CMD:-)\bdocker\s+run\b', content)
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
    assert content.count('command -v podman-compose') >= 2, (
        "container_cmd and compose_cmd should both detect podman-compose"
    )


@pytest.mark.parametrize("recipe,flag", [
    ("backup", "CONTAINER_CMD"),
    ("restore", "CONTAINER_CMD"),
])
def test_justfile_recipes_pass_container_cmd(recipe: str, flag: str) -> None:
    """Parametrised check that backup and restore recipes both forward CONTAINER_CMD."""
    content = JUSTFILE.read_text()
    # Find the recipe block and assert flag appears in it
    pattern = rf'{recipe}[^\n]*\n\s+{flag}='
    assert re.search(pattern, content), (
        f"Recipe '{recipe}' does not export {flag}"
    )
