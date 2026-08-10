"""
Tests for issue #130: htpasswd file must not be tracked in git;
secrets/htpasswd must be generated at runtime from environment variables.
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GEN_SCRIPT = REPO_ROOT / "scripts" / "gen-htpasswd.sh"
SECRETS_DIR = REPO_ROOT / "secrets"
GITIGNORE = REPO_ROOT / ".gitignore"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
JUSTFILE = REPO_ROOT / "justfile"
COMMITTED_HTPASSWD = REPO_ROOT / "configs" / "nginx" / "htpasswd"
HTPASSWD_EXAMPLE = REPO_ROOT / "configs" / "nginx" / "htpasswd.example"
GITLEAKS_TOML = REPO_ROOT / ".gitleaks.toml"


class TestHtpasswdNotCommitted(unittest.TestCase):
    def test_htpasswd_not_tracked_by_git(self) -> None:
        result = subprocess.run(
            ["git", "ls-files", "configs/nginx/htpasswd"],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.stdout.strip(), "",
                         "configs/nginx/htpasswd must not be tracked in git")

    def test_htpasswd_file_not_present_on_disk(self) -> None:
        self.assertFalse(
            COMMITTED_HTPASSWD.exists(),
            "configs/nginx/htpasswd must not exist on disk (only secrets/htpasswd at runtime)",
        )

    def test_htpasswd_example_exists(self) -> None:
        self.assertTrue(
            HTPASSWD_EXAMPLE.exists(),
            "configs/nginx/htpasswd.example must exist as a documentation placeholder",
        )

    def test_htpasswd_example_contains_no_real_hash(self) -> None:
        content = HTPASSWD_EXAMPLE.read_text()
        self.assertNotIn("$apr1$IvtTVmT2$", content,
                         "htpasswd.example must not contain the old leaked hash")


class TestGitignore(unittest.TestCase):
    def setUp(self) -> None:
        self.content = GITIGNORE.read_text()

    def test_secrets_dir_is_ignored(self) -> None:
        self.assertIn("secrets/", self.content)

    def test_nginx_htpasswd_is_ignored(self) -> None:
        self.assertIn("configs/nginx/htpasswd", self.content)


class TestEnvExample(unittest.TestCase):
    def setUp(self) -> None:
        self.content = ENV_EXAMPLE.read_text()

    def test_loki_auth_user_present(self) -> None:
        self.assertIn("LOKI_AUTH_USER=", self.content)

    def test_loki_auth_password_present(self) -> None:
        self.assertIn("LOKI_AUTH_PASSWORD=", self.content)


class TestDockerCompose(unittest.TestCase):
    def setUp(self) -> None:
        self.content = DOCKER_COMPOSE.read_text()

    def test_volume_mount_uses_secrets_dir(self) -> None:
        self.assertIn("./secrets/htpasswd:/etc/nginx/htpasswd:ro", self.content)

    def test_old_committed_path_not_referenced(self) -> None:
        self.assertNotIn("configs/nginx/htpasswd", self.content)


class TestJustfile(unittest.TestCase):
    def setUp(self) -> None:
        self.content = JUSTFILE.read_text()

    def test_gen_htpasswd_recipe_exists(self) -> None:
        self.assertIn("gen-htpasswd:", self.content)

    def test_start_depends_on_gen_htpasswd(self) -> None:
        self.assertRegex(self.content, r"start:\s+gen-htpasswd")

    def test_restart_depends_on_gen_htpasswd(self) -> None:
        self.assertRegex(self.content, r"restart:\s+gen-htpasswd")


class TestGenScript(unittest.TestCase):
    def test_script_exists(self) -> None:
        self.assertTrue(GEN_SCRIPT.exists(), "scripts/gen-htpasswd.sh must exist")

    def test_script_is_executable(self) -> None:
        mode = GEN_SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "scripts/gen-htpasswd.sh must be executable")

    def test_script_generates_htpasswd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                **os.environ,
                "LOKI_AUTH_USER": "testuser",
                "LOKI_AUTH_PASSWORD": "testpass123",
            }
            result = subprocess.run(
                [str(GEN_SCRIPT)],
                capture_output=True,
                text=True,
                check=False,
                env=env,
                cwd=tmpdir,
            )
            # Script uses REPO_ROOT derived from its own path, so output goes to
            # the real secrets/ dir. Run it and check the output file.
            secrets_htpasswd = REPO_ROOT / "secrets" / "htpasswd"
            self.assertEqual(result.returncode, 0,
                             f"gen-htpasswd.sh failed: {result.stderr}")
            self.assertTrue(secrets_htpasswd.exists(),
                            "secrets/htpasswd must be created by gen-htpasswd.sh")

    def test_generated_htpasswd_format(self) -> None:
        env = {
            **os.environ,
            "LOKI_AUTH_USER": "myuser",
            "LOKI_AUTH_PASSWORD": "mypassword",
        }
        result = subprocess.run(
            [str(GEN_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0,
                         f"gen-htpasswd.sh failed: {result.stderr}")

        secrets_htpasswd = REPO_ROOT / "secrets" / "htpasswd"
        content = secrets_htpasswd.read_text().strip()
        # Format: user:$apr1$<salt>$<hash>
        self.assertTrue(content.startswith("myuser:$apr1$"),
                        f"Generated htpasswd has unexpected format: {content}")

    def test_script_fails_without_user_var(self) -> None:
        env = {k: v for k, v in os.environ.items()
               if k not in ("LOKI_AUTH_USER", "LOKI_AUTH_PASSWORD")}
        env["LOKI_AUTH_PASSWORD"] = "somepass"
        result = subprocess.run(
            [str(GEN_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertNotEqual(result.returncode, 0,
                            "Script must exit non-zero when LOKI_AUTH_USER is unset")

    def test_script_fails_without_password_var(self) -> None:
        env = {k: v for k, v in os.environ.items()
               if k not in ("LOKI_AUTH_USER", "LOKI_AUTH_PASSWORD")}
        env["LOKI_AUTH_USER"] = "loki"
        result = subprocess.run(
            [str(GEN_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertNotEqual(result.returncode, 0,
                            "Script must exit non-zero when LOKI_AUTH_PASSWORD is unset")

    def test_generated_file_permissions(self) -> None:
        env = {
            **os.environ,
            "LOKI_AUTH_USER": "loki",
            "LOKI_AUTH_PASSWORD": "securepassword",
        }
        subprocess.run([str(GEN_SCRIPT)], env=env, check=True,
                       capture_output=True)
        secrets_htpasswd = REPO_ROOT / "secrets" / "htpasswd"
        mode = secrets_htpasswd.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600,
                         f"secrets/htpasswd must be chmod 600, got {oct(mode)}")


class TestGitleaksConfig(unittest.TestCase):
    def test_gitleaks_toml_exists(self) -> None:
        self.assertTrue(GITLEAKS_TOML.exists(), ".gitleaks.toml must exist")

    def test_gitleaks_extends_default_ruleset(self) -> None:
        content = GITLEAKS_TOML.read_text()
        self.assertIn("useDefault = true", content)

    def test_gitleaks_has_htpasswd_rule(self) -> None:
        content = GITLEAKS_TOML.read_text()
        self.assertIn("htpasswd", content)

    def test_gitleaks_allowlist_excludes_example(self) -> None:
        content = GITLEAKS_TOML.read_text()
        self.assertIn("htpasswd.example", content,
                      ".gitleaks.toml must allowlist htpasswd.example")


if __name__ == "__main__":
    unittest.main()
