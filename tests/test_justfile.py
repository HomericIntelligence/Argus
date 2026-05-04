"""
Assert that the justfile contains no hardcoded Grafana credentials.
"""
import re
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


if __name__ == "__main__":
    unittest.main()
