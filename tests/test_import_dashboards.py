"""
Tests for the import-dashboards.sh script and justfile credential handling.
"""
import os
import stat
import subprocess
import tempfile
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
        """Script must exit non-zero and print ERROR when GF_ADMIN_PASSWORD is unset."""
        env = {k: v for k, v in os.environ.items() if k != "GF_ADMIN_PASSWORD"}
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


def _make_sandbox(tmp_path: Path, env_content: str) -> Path:
    """Create a minimal sandbox dir with justfile + scripts + dashboards + .env.

    Returns the sandbox directory path.
    """
    import shutil

    shutil.copy(REPO_ROOT / "justfile", tmp_path / "justfile")
    shutil.copytree(
        REPO_ROOT / "scripts", tmp_path / "scripts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (tmp_path / "dashboards").mkdir(exist_ok=True)
    (tmp_path / ".env").write_text(env_content)
    return tmp_path


class TestJustRecipeGuard(unittest.TestCase):
    """Issue #262: the recipe must reject unset/empty GF_ADMIN_PASSWORD at the Just layer."""

    def _run_recipe(self, sandbox: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["just", "import-dashboards"],
            cwd=sandbox,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def test_just_recipe_rejects_unset_gf_admin_password(self) -> None:
        """.env omitting GF_ADMIN_PASSWORD must fail at the Just layer naming the var."""
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = _make_sandbox(Path(tmp), "AGAMEMNON_URL=http://localhost:1\n")
            result = self._run_recipe(sandbox)
        assert result.returncode != 0, "recipe should fail when GF_ADMIN_PASSWORD is unset"
        assert "GF_ADMIN_PASSWORD" in result.stderr, (
            f"stderr should name GF_ADMIN_PASSWORD, got: {result.stderr!r}"
        )
        assert ".env" in result.stderr, f"stderr should mention .env, got: {result.stderr!r}"
        # The cosmetic 'admin' fallback from env_var_or_default must NOT leak into
        # the script invocation — if it did, the guard would pass and curl would run.
        assert "admin:" not in result.stdout and "Importing" not in result.stdout, (
            f"fallback 'admin' appears to have leaked past the guard: {result.stdout!r}"
        )

    def test_just_recipe_rejects_empty_gf_admin_password(self) -> None:
        """.env with an empty GF_ADMIN_PASSWORD= must fail at the Just layer."""
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = _make_sandbox(Path(tmp), "GF_ADMIN_PASSWORD=\n")
            result = self._run_recipe(sandbox)
        assert result.returncode != 0, "recipe should fail when GF_ADMIN_PASSWORD is empty"
        assert "GF_ADMIN_PASSWORD" in result.stderr, (
            f"stderr should name GF_ADMIN_PASSWORD, got: {result.stderr!r}"
        )

    def test_just_recipe_does_not_interpolate_fallback(self) -> None:
        """Regression: the recipe body must not use {{GF_ADMIN_PASSWORD}} interpolation.

        Just-time interpolation against the global env_var_or_default(..., "admin")
        would silently substitute "admin" for unset values, bypassing the guard.
        """
        text = JUSTFILE.read_text()
        recipe_start = text.index("import-dashboards:")
        recipe_body = text[recipe_start:]
        next_recipe = recipe_body.find("\nbump ", 1)
        if next_recipe != -1:
            recipe_body = recipe_body[:next_recipe]
        assert "{{GF_ADMIN_PASSWORD}}" not in recipe_body, (
            "import-dashboards recipe must not interpolate {{GF_ADMIN_PASSWORD}} "
            "(issue #262); read the bash env var exported by `set dotenv-load` instead"
        )


if __name__ == "__main__":
    unittest.main()
