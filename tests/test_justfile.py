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

    def test_agamemnon_url_overridable(self) -> None:
        """AGAMEMNON_URL must accept .env overrides via env_var_or_default."""
        self.assertIn(
            'AGAMEMNON_URL := env_var_or_default("AGAMEMNON_URL"',
            self.content,
            "AGAMEMNON_URL is hardcoded; must use env_var_or_default to honor .env",
        )

    def test_grafana_port_overridable(self) -> None:
        """GRAFANA_PORT must accept .env overrides via env_var_or_default."""
        self.assertIn(
            'GRAFANA_PORT := env_var_or_default("GRAFANA_PORT"',
            self.content,
            "GRAFANA_PORT is hardcoded; must use env_var_or_default to honor .env",
        )


class TestEnvExampleNoDuplicateKeys(unittest.TestCase):
    def test_env_example_has_no_duplicate_keys(self) -> None:
        """.env.example must define each key at most once."""
        env_example = REPO_ROOT / ".env.example"
        keys = []
        for line in env_example.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            keys.append(line.split("=", 1)[0])
        duplicates = {k for k in keys if keys.count(k) > 1}
        self.assertFalse(duplicates, f"Duplicate keys in .env.example: {duplicates}")


if __name__ == "__main__":
    unittest.main()
