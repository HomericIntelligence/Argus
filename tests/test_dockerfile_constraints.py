"""
Static regression test: assert the exporter Dockerfile uses a stable Python version.
Guards against Dependabot auto-merging pre-release Python bumps (e.g. 3.13+).
Uses only stdlib: re, pathlib, unittest.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOCKERFILE = REPO_ROOT / "exporter" / "Dockerfile"

_MIN_VERSION = (3, 11)
_MAX_VERSION = (3, 12)


class TestDockerfileConstraints(unittest.TestCase):
    def test_python_base_image_version_is_stable(self) -> None:
        content = DOCKERFILE.read_text()
        match = re.search(r"FROM python:(\d+)\.(\d+)", content)
        assert match is not None, "Could not find a FROM python:X.Y line in exporter/Dockerfile"
        version = (int(match.group(1)), int(match.group(2)))
        assert version >= _MIN_VERSION, (
            f"Python base image {version} is below minimum stable version {_MIN_VERSION}"
        )
        assert version <= _MAX_VERSION, (
            f"Python base image {version} exceeds approved stable ceiling {_MAX_VERSION}; "
            "bump _MAX_VERSION intentionally after verifying the release is stable"
        )


if __name__ == "__main__":
    unittest.main()
