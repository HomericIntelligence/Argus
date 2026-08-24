"""
Assert that the justfile contains no hardcoded Grafana credentials.
"""
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
JUSTFILE = REPO_ROOT / "justfile"


class TestJustfileNoHardcodedCredentials(unittest.TestCase):
    def setUp(self) -> None:
        self.content = JUSTFILE.read_text()

    def test_no_admin_colon_admin(self) -> None:
        """admin:admin must not appear anywhere in the justfile."""
        self.assertNotIn(
            "admin:admin",
            self.content,
            "Hardcoded credential 'admin:admin' found in justfile",
        )

    def test_no_grafana_auth_variable(self) -> None:
        """The GRAFANA_AUTH variable definition must not exist in the justfile."""
        self.assertNotIn(
            "GRAFANA_AUTH",
            self.content,
            "Variable 'GRAFANA_AUTH' still present in justfile",
        )

    def test_dotenv_load_enabled(self) -> None:
        """set dotenv-load must be present so .env is read at recipe time."""
        self.assertIn(
            "set dotenv-load",
            self.content,
            "'set dotenv-load' not found in justfile",
        )

    def test_import_dashboards_uses_gf_admin_password(self) -> None:
        """import-dashboards recipe must reference GF_ADMIN_PASSWORD from env."""
        self.assertIn(
            "GF_ADMIN_PASSWORD",
            self.content,
            "import-dashboards recipe does not reference GF_ADMIN_PASSWORD",
        )

    def test_no_cut_d_colon_credential_extraction(self) -> None:
        """Credential extraction via 'cut -d: -f2' must be gone from the justfile."""
        self.assertNotIn(
            "cut -d:",
            self.content,
            "Credential extraction via 'cut -d:' still present in justfile",
        )


class TestJustfileEnvGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.content = JUSTFILE.read_text()

    def test_start_depends_on_check_env(self) -> None:
        """start recipe must declare the check-env dependency."""
        self.assertIn(
            "start: check-env",
            self.content,
            "start recipe does not depend on check-env",
        )

    def test_restart_depends_on_check_env(self) -> None:
        """restart recipe must declare the check-env dependency."""
        self.assertIn(
            "restart: check-env",
            self.content,
            "restart recipe does not depend on check-env",
        )

    def test_check_env_recipe_present(self) -> None:
        """The check-env guard recipe must exist in the justfile."""
        self.assertIn(
            "\ncheck-env:\n",
            self.content,
            "check-env recipe not found in justfile",
        )

    def test_check_env_suggests_env_example(self) -> None:
        """check-env must reference .env.example in its remediation hint."""
        self.assertIn(
            ".env.example",
            self.content,
            "check-env remediation hint does not mention .env.example",
        )

    def test_check_env_exits_nonzero(self) -> None:
        """check-env body must exit non-zero when .env is missing."""
        start = self.content.index("\ncheck-env:\n")
        end = self.content.find("\n# ", start)
        if end == -1:
            end = len(self.content)
        self.assertIn(
            "exit 1",
            self.content[start:end],
            "check-env recipe does not exit non-zero",
        )


if __name__ == "__main__":
    unittest.main()
