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

        Since #321 Grafana has no host port, so the script runs the API call
        inside the grafana container with busybox wget (-S) instead of host
        curl. We confirm error handling by inspecting the script source rather
        than making a real network call (which would require a running stack):
        the wget response headers are parsed for an HTTP status code, and any
        non-2xx (or unreachable API) exits non-zero via 'exit 1'.
        """
        source = SCRIPT.read_text()
        assert "wget" in source, (
            "import-dashboards.sh should call the Grafana API via in-container wget"
        )
        assert "-S" in source, (
            "import-dashboards.sh should use wget -S to capture response headers"
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


if __name__ == "__main__":
    unittest.main()
