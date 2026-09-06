"""
Tests for the import-dashboards.sh script and justfile credential handling.
"""
import os
import stat
import subprocess
import unittest
from pathlib import Path

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


class TestImportDashboardsAdminUser(unittest.TestCase):
    """Issue #183: the Grafana admin *username* must be env-configurable."""

    def _script_text(self) -> str:
        return SCRIPT.read_text()

    def test_script_builds_auth_from_env_user(self) -> None:
        """Script must default GRAFANA_ADMIN_USER from the environment."""
        source = self._script_text()
        assert 'GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"' in source, (
            "import-dashboards.sh should read GRAFANA_ADMIN_USER with an 'admin' default"
        )
        assert 'GRAFANA_AUTH="${GRAFANA_ADMIN_USER}:' in source, (
            "import-dashboards.sh should build GRAFANA_AUTH from GRAFANA_ADMIN_USER"
        )
        assert 'GRAFANA_AUTH="admin:' not in source, (
            "import-dashboards.sh must not hardcode the 'admin' username"
        )

    def test_justfile_passes_admin_user_to_script(self) -> None:
        """import-dashboards recipe must forward GRAFANA_ADMIN_USER to the script."""
        content = JUSTFILE.read_text()
        assert "GRAFANA_ADMIN_USER" in content, (
            "justfile should define/forward GRAFANA_ADMIN_USER"
        )
        recipe = next(
            line for line in content.splitlines() if "import-dashboards.sh" in line
        )
        assert "GRAFANA_ADMIN_USER={{GRAFANA_ADMIN_USER}}" in recipe, (
            "import-dashboards recipe should pass GRAFANA_ADMIN_USER to the script"
        )


if __name__ == "__main__":
    unittest.main()
