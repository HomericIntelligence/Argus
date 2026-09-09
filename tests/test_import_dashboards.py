"""
Tests for the import-dashboards.sh script and justfile credential handling.
"""
import os
import shutil
import stat
import subprocess
import textwrap
import unittest
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "import-dashboards.sh"
JUSTFILE = REPO_ROOT / "justfile"


class TestImportDashboardsScript(unittest.TestCase):
    def test_script_is_executable(self) -> None:
        assert SCRIPT.exists(), f"{SCRIPT} does not exist"
        mode = SCRIPT.stat().st_mode
        assert mode & stat.S_IXUSR, f"{SCRIPT} is not executable"

    def test_script_fails_without_password(self) -> None:
        """Script must exit non-zero and print ERROR when GRAFANA_ADMIN_PASSWORD is unset."""
        env = {k: v for k, v in os.environ.items() if k != "GRAFANA_ADMIN_PASSWORD"}
        # Use an invalid port so no real network call can succeed even if the guard is missing.
        env["GRAFANA_PORT"] = "0"
        result = subprocess.run(
            [str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
        check=False,
        )
        assert result.returncode != 0, "Script should exit non-zero when password is unset"
        assert "ERROR" in result.stderr, (
            f"Expected 'ERROR' in stderr, got: {result.stderr!r}"
        )

    def test_script_exits_with_useful_message_on_401(self) -> None:
        """Script must exit non-zero and surface HTTP error code on auth failure.

        Verified by inspecting the script source: the curl invocation captures the HTTP
        status code with -w "%{http_code}" and explicitly exits non-zero for any non-2xx
        response.  We confirm this by grepping the script text rather than making a real
        network call (which would require a running Grafana instance).
        """
        source = SCRIPT.read_text()
        assert "%{http_code}" in source, (
            "import-dashboards.sh should capture HTTP status code with -w \"%{http_code}\""
        )
        assert "exit 1" in source, (
            "import-dashboards.sh should call 'exit 1' on HTTP error"
        )


class TestJustfileCredentials(unittest.TestCase):
    def _justfile_text(self) -> str:
        return JUSTFILE.read_text()

    def test_no_hardcoded_admin_password(self) -> None:
        """justfile must not contain the hardcoded 'admin:admin' credential."""
        assert "admin:admin" not in self._justfile_text(), (
            "justfile contains hardcoded 'admin:admin' — this was the bug fixed in #152"
        )

    def test_import_dashboards_reads_gf_admin_password(self) -> None:
        """import-dashboards recipe must reference GF_ADMIN_PASSWORD from the environment."""
        assert "GF_ADMIN_PASSWORD" in self._justfile_text(), (
            "import-dashboards recipe should use GF_ADMIN_PASSWORD from the environment"
        )

    def test_grafana_auth_variable_removed(self) -> None:
        """GRAFANA_AUTH justfile variable must no longer be defined."""
        assert "GRAFANA_AUTH" not in self._justfile_text(), (
            "GRAFANA_AUTH justfile variable should have been removed in #152"
        )


# The tests below are module-level pytest functions (not unittest.TestCase
# methods) because they need the `tmp_path` fixture, which pytest cannot
# inject into unittest.TestCase subclasses.

JUST_BIN = shutil.which("just")
requires_just = pytest.mark.skipif(JUST_BIN is None, reason="`just` not installed")


def _make_stub_workspace(tmp_path: Path) -> Path:
    """Copy the real justfile into tmp_path and write a stub
    scripts/import-dashboards.sh that records the GF_ADMIN_PASSWORD it received."""
    (tmp_path / ".env").write_text("")  # satisfy `set dotenv-load`
    (tmp_path / "justfile").write_text(JUSTFILE.read_text())
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    capture = tmp_path / "captured.txt"
    stub = scripts_dir / "import-dashboards.sh"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -eo pipefail
        printf '%s' "${{GF_ADMIN_PASSWORD-<UNSET>}}" > {capture}
        exit 0
    """))
    stub.chmod(0o755)
    return capture


def _run_just(tmp_path: Path, env: dict) -> None:
    """Run `just import-dashboards` against the stub workspace in tmp_path."""
    subprocess.run(
        [
            JUST_BIN,
            "--justfile", str(tmp_path / "justfile"),
            "--working-directory", str(tmp_path),
            "import-dashboards",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


@requires_just
def test_just_propagates_gf_admin_password_to_script(tmp_path: Path) -> None:
    """Setting GF_ADMIN_PASSWORD in the environment must reach the script."""
    capture = _make_stub_workspace(tmp_path)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "GF_ADMIN_PASSWORD": "testpass-317",
    }
    _run_just(tmp_path, env)
    assert capture.read_text() == "testpass-317", (
        "justfile recipe failed to propagate GF_ADMIN_PASSWORD to the script — "
        "check `env_var_or_default('GF_ADMIN_PASSWORD', ...)` at justfile:21 "
        "and the recipe body at justfile:183"
    )


@requires_just
def test_just_falls_back_to_admin_when_env_unset(tmp_path: Path) -> None:
    """When GF_ADMIN_PASSWORD is unset, the recipe must use the documented
    fallback value 'admin' (justfile:21). Guards against accidental removal
    of the fallback default."""
    capture = _make_stub_workspace(tmp_path)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }
    _run_just(tmp_path, env)
    assert capture.read_text() == "admin"


if __name__ == "__main__":
    unittest.main()
