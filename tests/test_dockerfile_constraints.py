"""
Static regression test: assert every Python service Dockerfile uses a stable
Python version. Guards against Dependabot auto-merging pre-release Python
bumps (e.g. 3.13+). Uses only stdlib: re, pathlib, unittest.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOCKERFILES = [
    REPO_ROOT / "exporter" / "Dockerfile",
    REPO_ROOT / "jetstream-consumer" / "Dockerfile",
]

_MIN_VERSION = (3, 11)
# Approved Python version ceiling. Advance this only after the next minor is
# added to the CI validation set (.github/workflows/ci.yml setup-python
# version) AND the base image builds and passes the full test suite with it.
# Process: bump _MAX_VERSION in the same PR that bumps the FROM line in
# exporter/Dockerfile so the test stays a single source of truth.
_MAX_VERSION = (3, 13)


class TestDockerfileConstraints(unittest.TestCase):
    def test_python_base_image_version_is_stable(self) -> None:
        for dockerfile in DOCKERFILES:
            with self.subTest(dockerfile=str(dockerfile.relative_to(REPO_ROOT))):
                assert dockerfile.exists(), f"Missing Dockerfile: {dockerfile}"
                content = dockerfile.read_text()
                match = re.search(r"FROM python:(\d+)\.(\d+)", content)
                assert match is not None, (
                    f"Could not find a FROM python:X.Y line in {dockerfile}"
                )
                version = (int(match.group(1)), int(match.group(2)))
                assert version >= _MIN_VERSION, (
                    f"Python base image {version} in {dockerfile} is below "
                    f"minimum stable version {_MIN_VERSION}"
                )
                assert version <= _MAX_VERSION, (
                    f"Python base image {version} in {dockerfile} exceeds "
                    f"approved stable ceiling {_MAX_VERSION}; bump "
                    "_MAX_VERSION intentionally after verifying the release "
                    "is stable"
                )


if __name__ == "__main__":
    unittest.main()
