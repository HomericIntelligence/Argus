"""
Regression tests guarding against silent credential fallbacks.

Follow-up to issue #124: scripts/import-dashboards.sh previously carried a
silent ``:-admin`` fallback. These tests sweep scripts/, configs/,
docker-compose.yml, and the justfile for the same anti-pattern and verify
that compose fails fast when GRAFANA_ADMIN_PASSWORD is unset.

Issue: HomericIntelligence/Argus#318
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
JUSTFILE = REPO_ROOT / "justfile"

# check-grafana-password.sh is the *detector* for weak values; it is allowed
# to contain the literal patterns it scans for.
DETECTOR_ALLOWLIST = {"scripts/check-grafana-password.sh"}


class TestNoSilentCredentialFallbacks(unittest.TestCase):
    """Static sweeps over scripts/, configs/, compose, and justfile."""

    def test_no_silent_admin_fallback_in_compose(self) -> None:
        """docker-compose.yml must not fall back to a default credential."""
        content = COMPOSE_FILE.read_text()
        for pattern in (":-admin", ":-changeme", ":-password"):
            self.assertNotIn(
                pattern,
                content,
                f"Silent credential fallback '{pattern}' found in docker-compose.yml",
            )

    def test_no_hardcoded_admin_admin_in_scripts_or_configs(self) -> None:
        """scripts/ and configs/ must not hardcode admin credentials."""
        offenders: list[str] = []
        for base in ("scripts", "configs"):
            for path in (REPO_ROOT / base).rglob("*"):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(REPO_ROOT))
                if rel in DETECTOR_ALLOWLIST:
                    continue
                try:
                    text = path.read_text()
                except UnicodeDecodeError:
                    continue
                if "admin:admin" in text or "password=admin" in text:
                    offenders.append(rel)
        self.assertEqual(
            offenders,
            [],
            f"Hardcoded credentials found in: {offenders}",
        )

    def test_no_unauthenticated_basic_auth_header_literals(self) -> None:
        """scripts/ must not carry literal Basic auth headers."""
        offenders: list[str] = []
        for path in (REPO_ROOT / "scripts").rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if rel in DETECTOR_ALLOWLIST:
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            if "Authorization: Basic" in text:
                offenders.append(rel)
        self.assertEqual(
            offenders,
            [],
            f"Literal 'Authorization: Basic' header found in: {offenders}",
        )

    def test_justfile_has_no_admin_default_for_password_env_vars(self) -> None:
        """Grafana password env vars must not have silent defaults in justfile."""
        content = JUSTFILE.read_text()
        for var in ("GF_ADMIN_PASSWORD", "GRAFANA_ADMIN_PASSWORD"):
            self.assertNotIn(
                f'env_var_or_default("{var}"',
                content,
                f"Silent default for {var} found in justfile; use env_var()",
            )


def _resolve_compose_cmd() -> list[str] | None:
    """Return a working compose command, or None when none is available."""
    docker = shutil.which("docker")
    if docker is not None:
        probe = subprocess.run(
            [docker, "compose", "version"],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if probe.returncode == 0:
            return [docker, "compose"]
    if shutil.which("podman-compose") is not None:
        return ["podman-compose"]
    return None


COMPOSE_CMD = _resolve_compose_cmd()


@unittest.skipIf(COMPOSE_CMD is None, "no working compose command available")
class TestComposeFailFastBehavior(unittest.TestCase):
    """Functional verification that compose refuses to start without creds."""

    @staticmethod
    def _base_env() -> dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("GRAFANA_ADMIN_PASSWORD", "GF_ADMIN_PASSWORD")
        }
        return env

    def _compose_config(self) -> subprocess.CompletedProcess[str]:
        assert COMPOSE_CMD is not None
        # --env-file /dev/null suppresses .env loading so developer machines
        # with a populated .env cannot mask the fail-fast behavior.
        return subprocess.run(
            [*COMPOSE_CMD, "--env-file", "/dev/null", "config"],
            cwd=REPO_ROOT,
            env=self._base_env(),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def test_compose_failfast_when_grafana_password_unset(self) -> None:
        """compose config exits non-zero and names GRAFANA_ADMIN_PASSWORD."""
        result = self._compose_config()
        self.assertNotEqual(
            result.returncode,
            0,
            "docker compose config succeeded with GRAFANA_ADMIN_PASSWORD unset; "
            "the silent :-admin fallback appears to be back",
        )
        self.assertIn("GRAFANA_ADMIN_PASSWORD", result.stderr)

    def test_compose_config_succeeds_when_password_set(self) -> None:
        """compose config succeeds with a password set and no YAML breakage."""
        env = self._base_env()
        marker = "dummytestvalue318"
        env["GRAFANA_ADMIN_PASSWORD"] = marker
        assert COMPOSE_CMD is not None
        result = subprocess.run(
            [*COMPOSE_CMD, "--env-file", "/dev/null", "config"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"docker compose config failed with password set: {result.stderr}",
        )
        self.assertIn(marker, result.stdout)


if __name__ == "__main__":
    unittest.main()
