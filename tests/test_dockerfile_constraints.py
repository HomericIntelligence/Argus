"""
Static regression tests for the Python service Dockerfiles:

1. Every Python base image uses a stable Python version within the approved
   min/max ceiling. Guards against Dependabot auto-merging pre-release Python
   bumps.
2. Every ``FROM python:`` directive is pinned by sha256 digest so builds are
   reproducible and auditable (issue #313).

Uses only stdlib: re, pathlib, unittest.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOCKERFILES = [
    REPO_ROOT / "exporter" / "Dockerfile",
    REPO_ROOT / "jetstream-consumer" / "Dockerfile",
]

# A digest-pinned Python base-image FROM directive at the start of a line:
#   FROM python:X.Y[-variant]@sha256:<64-hex>
# The version groups are anchored to the tag portion *before* the "@sha256:"
# pin so a floating tag can never satisfy this pattern.
_FROM_PYTHON_PINNED_RE = re.compile(
    r"^FROM\s+python:(\d+)\.(\d+)(?:-[a-z0-9-]+)?@sha256:([0-9a-f]{64})"
    r"(?:\s+AS\s+\w+)?\s*$",
    re.MULTILINE,
)
# Any Python base-image FROM directive at all (pinned or not).
_ANY_PYTHON_FROM_RE = re.compile(r"^FROM\s+python:\S+", re.MULTILINE)

_MIN_VERSION = (3, 11)
# Approved Python version ceiling. Advance this only after the next CPython
# release is GA (https://devguide.python.org/versions/) AND has been
# manually verified to build the exporter image and pass the full test
# suite. Process: bump _MAX_VERSION in the same PR that bumps the FROM
# line in exporter/Dockerfile so the test stays a single source of truth.
# Python 3.14 reached GA on 2025-10-07; widen the ceiling to (3, 14) so
# a manual base-image bump doesn't trip the regression test.
_MAX_VERSION = (3, 14)


class TestDockerfileConstraints(unittest.TestCase):
    def test_python_base_image_version_is_stable(self) -> None:
        for dockerfile in DOCKERFILES:
            with self.subTest(dockerfile=str(dockerfile.relative_to(REPO_ROOT))):
                assert dockerfile.exists(), f"Missing Dockerfile: {dockerfile}"
                content = dockerfile.read_text()
                matches = list(_FROM_PYTHON_PINNED_RE.finditer(content))
                assert matches, (
                    f"Could not find a pinned FROM python:X.Y@sha256:<digest> "
                    f"line in {dockerfile}"
                )
                for match in matches:
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

    def test_python_base_image_is_digest_pinned(self) -> None:
        for dockerfile in DOCKERFILES:
            with self.subTest(dockerfile=str(dockerfile.relative_to(REPO_ROOT))):
                assert dockerfile.exists(), f"Missing Dockerfile: {dockerfile}"
                content = dockerfile.read_text()
                from_lines = _ANY_PYTHON_FROM_RE.findall(content)
                assert from_lines, (
                    f"Could not find a FROM python: line in {dockerfile}"
                )
                for line in from_lines:
                    assert _FROM_PYTHON_PINNED_RE.match(line) is not None, (
                        f"{line.strip()!r} in {dockerfile} pins python by tag "
                        "but not by sha256 digest. Append '@sha256:<digest>' "
                        "to the FROM line; see issue #313."
                    )


if __name__ == "__main__":
    unittest.main()
