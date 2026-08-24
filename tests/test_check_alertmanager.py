"""
Tests for the check-alertmanager health gate (issue #250):
script behavior (skip/arm paths) and justfile wiring.
Uses stdlib (subprocess, unittest, http.server, tempfile) + yaml.
"""
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-alertmanager.sh"
CONTAINER_NAME = "argus-alertmanager"

COMPOSE_WITH_AM = "services:\n  alertmanager:\n    image: prom/alertmanager\n"
COMPOSE_WITHOUT_AM = "services:\n  prometheus:\n    image: prom/prometheus\n"
CLOSED_PORT_URL = "http://127.0.0.1:1"  # nothing listens on port 1


def make_container_cmd(directory: Path, running: bool) -> str:
    """Write a shim CONTAINER_CMD that reports the container running or not."""
    shim = directory / "container-cmd"
    lines = ["#!/bin/sh"]
    if running:
        lines.append(f'echo "{CONTAINER_NAME}"')
    shim.write_text("\n".join(lines) + "\n")
    shim.chmod(0o755)
    return str(shim)


def run_gate(
    compose_text: str,
    url: str,
    container_cmd: str | None = None,
) -> subprocess.CompletedProcess:
    """Run check-alertmanager.sh against a temp compose file and probe URL."""
    env = dict(os.environ)
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
        f.write(compose_text)
        compose_path = f.name
    env["COMPOSE_FILE"] = compose_path
    env["ALERTMANAGER_URL"] = url
    if container_cmd is not None:
        env["CONTAINER_CMD"] = container_cmd
    else:
        env.pop("CONTAINER_CMD", None)
    return subprocess.run(
        [str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class _Healthy(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args) -> None:
        pass


class _Unhealthy(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(503)
        self.end_headers()
        self.wfile.write(b"UNHEALTHY")

    def log_message(self, *args) -> None:
        pass


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class TestCheckAlertmanagerScript(unittest.TestCase):
    """Behavior of scripts/check-alertmanager.sh across skip/arm paths."""

    def test_skips_when_service_absent(self) -> None:
        result = run_gate(COMPOSE_WITHOUT_AM, CLOSED_PORT_URL)
        self.assertEqual(result.returncode, 0)
        self.assertIn("SKIP", result.stdout)
        self.assertIn("no alertmanager service", result.stdout)

    def test_skips_when_container_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shim = make_container_cmd(Path(tmp), running=False)
            result = run_gate(COMPOSE_WITH_AM, CLOSED_PORT_URL, container_cmd=shim)
        self.assertEqual(result.returncode, 0)
        self.assertIn("SKIP", result.stdout)
        self.assertIn("not running", result.stdout)

    def test_arms_and_passes_when_healthy(self) -> None:
        server, url = _serve(_Healthy)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                shim = make_container_cmd(Path(tmp), running=True)
                result = run_gate(COMPOSE_WITH_AM, url, container_cmd=shim)
            self.assertEqual(result.returncode, 0)
            self.assertIn("OK", result.stdout)
        finally:
            server.shutdown()

    def test_arms_and_fails_when_unhealthy(self) -> None:
        server, url = _serve(_Unhealthy)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                shim = make_container_cmd(Path(tmp), running=True)
                result = run_gate(COMPOSE_WITH_AM, url, container_cmd=shim)
            self.assertEqual(result.returncode, 1)
            self.assertIn("FAIL", result.stderr)
        finally:
            server.shutdown()

    def test_detection_tolerates_other_indentation(self) -> None:
        four_space = "services:\n    alertmanager:\n        image: prom/alertmanager\n"
        with tempfile.TemporaryDirectory() as tmp:
            shim = make_container_cmd(Path(tmp), running=True)
            result = run_gate(four_space, CLOSED_PORT_URL, container_cmd=shim)
        # Armed past compose detection, so it must probe (and fail), not skip.
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("no alertmanager service", result.stdout)

    def test_gate_state_matches_real_compose_yaml(self) -> None:
        # Anti-silent-disarm: the grep guard must agree with a real YAML parse
        # of docker-compose.yml. Fails loudly if the service definition drifts
        # into a form the guard misses.
        compose_path = REPO_ROOT / "docker-compose.yml"
        services = yaml.safe_load(compose_path.read_text()).get("services", {})
        result = run_gate(compose_path.read_text(), CLOSED_PORT_URL)
        if "alertmanager" in services:
            self.assertNotIn("no alertmanager service", result.stdout)
        else:
            self.assertEqual(result.returncode, 0)
            self.assertIn("no alertmanager service", result.stdout)


class TestJustfileWiring(unittest.TestCase):
    """The justfile must expose the gate and call it from `validate`."""

    def test_check_alertmanager_recipe_exists(self) -> None:
        out = subprocess.run(
            ["just", "--summary"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertIn("check-alertmanager", out.split())

    def test_validate_body_invokes_check_alertmanager(self) -> None:
        # `just -n` prints only recipe body commands (no comments), so this
        # cannot be satisfied by a doc-comment mention.
        result = subprocess.run(
            ["just", "-n", "validate"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("check-alertmanager", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
