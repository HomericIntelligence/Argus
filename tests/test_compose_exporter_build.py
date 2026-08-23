"""
Static regression test: assert the argus-exporter compose service is built
from exporter/Dockerfile instead of running a stock python image with a
bind-mounted source file (issue #212). Uses yaml.safe_load (PyYAML is a
project dependency in pixi.toml).
"""
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
SERVICE_NAME = "argus-exporter"


class TestComposeExporterBuild(unittest.TestCase):
    """Guards the compose wiring that builds the exporter image."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = yaml.safe_load(COMPOSE_FILE.read_text())
        cls.service = cls.compose["services"][SERVICE_NAME]

    def test_service_has_build_block(self) -> None:
        build = self.service.get("build")
        self.assertIsInstance(build, dict, "argus-exporter must declare a build: block")
        self.assertEqual(build.get("context"), "./exporter")
        self.assertEqual(build.get("dockerfile"), "Dockerfile")

    def test_image_is_not_stock_python(self) -> None:
        image = self.service.get("image", "")
        self.assertFalse(
            str(image).startswith("python:"),
            f"argus-exporter must not run a stock python image, got: {image}",
        )

    def test_no_command_override(self) -> None:
        self.assertNotIn(
            "command",
            self.service,
            "argus-exporter must not override the Dockerfile CMD",
        )

    def test_source_file_not_bind_mounted(self) -> None:
        mounts = self.service.get("volumes") or []
        for mount in mounts:
            source = str(mount).split(":")[0]
            self.assertNotIn(
                "exporter.py",
                source,
                f"argus-exporter must not bind-mount exporter source: {mount}",
            )


if __name__ == "__main__":
    unittest.main()
