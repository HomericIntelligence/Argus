"""
Validate that all container services run as non-root users and have security
hardening fields set in docker-compose.yml and exporter/Dockerfile.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "exporter" / "Dockerfile"

# Services that must have non-root user directives (debug-shell is dev-only, excluded)
HARDENED_SERVICES = {
    "prometheus": "65534:65534",
    "loki": "10001:10001",
    "loki-proxy": "101:101",
    "promtail": "65534:65534",
    "grafana": "472:472",
    "argus-exporter": "1000:1000",
}


def _load_compose() -> dict:
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


class TestDockerComposeNonRoot(unittest.TestCase):
    """Each production service must declare a non-root user."""

    def setUp(self) -> None:
        self.compose = _load_compose()
        self.services: dict = self.compose.get("services", {})

    def test_compose_file_exists(self) -> None:
        self.assertTrue(COMPOSE_FILE.exists(), "docker-compose.yml not found")

    def test_all_hardened_services_present(self) -> None:
        for svc in HARDENED_SERVICES:
            self.assertIn(svc, self.services, f"Service '{svc}' not found in docker-compose.yml")

    def test_each_service_has_user_field(self) -> None:
        for svc in HARDENED_SERVICES:
            self.assertIn("user", self.services[svc], f"Service '{svc}' missing 'user' field")

    def test_each_service_user_is_non_root(self) -> None:
        for svc in HARDENED_SERVICES:
            user_val = str(self.services[svc].get("user", ""))
            uid = user_val.split(":")[0]
            self.assertNotEqual(uid, "0", f"Service '{svc}' runs as root (UID 0)")
            self.assertNotEqual(uid, "", f"Service '{svc}' has empty user field")

    def test_each_service_user_matches_expected_uid(self) -> None:
        for svc, expected_user in HARDENED_SERVICES.items():
            actual = str(self.services[svc].get("user", ""))
            self.assertEqual(
                actual,
                expected_user,
                f"Service '{svc}': expected user '{expected_user}', got '{actual}'",
            )

    def test_each_service_has_cap_drop_all(self) -> None:
        for svc in HARDENED_SERVICES:
            cap_drop = self.services[svc].get("cap_drop", [])
            self.assertIn(
                "ALL",
                cap_drop,
                f"Service '{svc}' missing cap_drop: [ALL]",
            )

    def test_each_service_has_no_new_privileges(self) -> None:
        for svc in HARDENED_SERVICES:
            security_opt = self.services[svc].get("security_opt", [])
            self.assertIn(
                "no-new-privileges:true",
                security_opt,
                f"Service '{svc}' missing security_opt: no-new-privileges:true",
            )

    def test_debug_shell_not_hardened(self) -> None:
        """dev-only debug-shell is excluded from hardening requirements."""
        self.assertIn("debug-shell", self.services, "debug-shell service should still exist")
        debug = self.services["debug-shell"]
        self.assertIn("profiles", debug, "debug-shell must remain dev-only via profiles")


class TestDockerfileNonRoot(unittest.TestCase):
    """exporter/Dockerfile must create a non-root user and switch to it."""

    def setUp(self) -> None:
        self.dockerfile_text = DOCKERFILE.read_text()

    def test_dockerfile_exists(self) -> None:
        self.assertTrue(DOCKERFILE.exists(), "exporter/Dockerfile not found")

    def test_has_groupadd(self) -> None:
        self.assertRegex(
            self.dockerfile_text,
            r"groupadd",
            "Dockerfile must create a group with groupadd",
        )

    def test_has_useradd(self) -> None:
        self.assertRegex(
            self.dockerfile_text,
            r"useradd",
            "Dockerfile must create a user with useradd",
        )

    def test_user_directive_present(self) -> None:
        self.assertRegex(
            self.dockerfile_text,
            r"(?m)^USER\s+\S+",
            "Dockerfile must have a USER directive",
        )

    def test_user_directive_is_not_root(self) -> None:
        matches = re.findall(r"(?m)^USER\s+(\S+)", self.dockerfile_text)
        self.assertTrue(matches, "No USER directive found in Dockerfile")
        for user in matches:
            self.assertNotIn(user, ("root", "0", "0:0"), f"Dockerfile USER is root: '{user}'")

    def test_copy_uses_chown(self) -> None:
        self.assertRegex(
            self.dockerfile_text,
            r"COPY\s+--chown=",
            "COPY instruction must use --chown to set file ownership for the non-root user",
        )

    def test_user_directive_after_useradd(self) -> None:
        useradd_pos = self.dockerfile_text.find("useradd")
        user_pos = re.search(r"(?m)^USER\s+", self.dockerfile_text)
        self.assertIsNotNone(user_pos, "USER directive not found")
        self.assertGreater(
            user_pos.start(),  # type: ignore[union-attr]
            useradd_pos,
            "USER directive must appear after useradd",
        )

    def test_uid_1000_assigned(self) -> None:
        self.assertRegex(
            self.dockerfile_text,
            r"-u\s+1000",
            "exporter user must be created with UID 1000",
        )


if __name__ == "__main__":
    unittest.main()
