#!/usr/bin/env python3
"""Fail-closed consistency checker for the argus-exporter image version.

Asserts three sources agree on the exporter image version (Refs #393):

1. ``exporter/VERSION`` — canonical semver (``X.Y.Z``).
2. ``docker-compose.yml`` — the ``${EXPORTER_IMAGE:-...}`` default tag pinned
   on the ``argus-exporter`` service.
3. ``.github/workflows/publish-exporter-image.yml`` — the workflow must read
   ``exporter/VERSION`` and publish the matching ``:v<semver>`` tag.

Exits 0 with OK lines when everything agrees; exits 1 with actionable stderr
messages when any file is missing, malformed, or disagrees.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

# docker-compose.yml exporter service image line, e.g.
#   image: ${EXPORTER_IMAGE:-ghcr.io/homericintelligence/argus-exporter:v0.1.0}
COMPOSE_IMAGE_RE = re.compile(r"image:\s*\$\{EXPORTER_IMAGE:-(?P<ref>\S+)\}")
IMAGE_REF_RE = re.compile(
    r"^ghcr\.io/(?P<owner>[^/\s]+)/argus-exporter:(?P<tag>v?[0-9]+\.[0-9]+\.[0-9]+)$"
)

# The workflow must read exporter/VERSION and publish a :v<parsed> tag built
# from the version-read step output (`steps.version.outputs.version`).
WORKFLOW_VERSION_FILE = "exporter/VERSION"
WORKFLOW_TAG_RE = re.compile(r":v\$\{\{\s*steps\.version\.outputs\.version\s*\}\}")


def load_version(version_file: Path) -> str:
    """Return the strict-semver string from exporter/VERSION."""
    text = version_file.read_text().strip()
    if not SEMVER_RE.match(text):
        raise ValueError(f"{version_file}: expected X.Y.Z semver, got {text!r}")
    return text


def compose_tag(compose_file: Path) -> str:
    """Return the tag in the compose EXPORTER_IMAGE default (leading 'v' stripped)."""
    match = COMPOSE_IMAGE_RE.search(compose_file.read_text())
    if match is None:
        raise ValueError(
            f"{compose_file}: no ${{EXPORTER_IMAGE:-...}} image default found "
            "on the argus-exporter service"
        )
    ref_match = IMAGE_REF_RE.match(match.group("ref"))
    if ref_match is None:
        raise ValueError(
            f"{compose_file}: EXPORTER_IMAGE default {match.group('ref')!r} is not "
            "a ghcr.io/<owner>/argus-exporter:vX.Y.Z reference"
        )
    return ref_match.group("tag").removeprefix("v")


def workflow_is_wired(workflow_file: Path) -> list[str]:
    """Return error strings if the publish workflow is not wired to VERSION."""
    errors: list[str] = []
    text = workflow_file.read_text()
    if WORKFLOW_VERSION_FILE not in text:
        errors.append(
            f"{workflow_file}: does not read {WORKFLOW_VERSION_FILE} — "
            "add a 'Read exporter version' step"
        )
    if WORKFLOW_TAG_RE.search(text) is None:
        errors.append(
            f"{workflow_file}: tags list does not contain the "
            ":v${{ steps.version.outputs.version }} semver tag"
        )
    return errors


def check(repo_root: Path) -> int:
    """Run all three checks; print errors to stderr and return 0/1."""
    errors: list[str] = []

    version_file = repo_root / "exporter" / "VERSION"
    compose_file = repo_root / "docker-compose.yml"
    workflow_file = repo_root / ".github" / "workflows" / "publish-exporter-image.yml"

    for path in (version_file, compose_file, workflow_file):
        if not path.exists():
            print(f"ERROR: required file not found: {path}", file=sys.stderr)
            errors.append(str(path))

    if errors:
        return 1

    try:
        version = load_version(version_file)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {version_file} declares version {version}")

    try:
        tag = compose_tag(compose_file)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if tag != version:
        print(
            f"ERROR: {compose_file}: exporter image tag v{tag} does not match "
            f"exporter/VERSION {version}",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {compose_file} pins argus-exporter:v{tag}")

    workflow_errors = workflow_is_wired(workflow_file)
    for err in workflow_errors:
        print(f"ERROR: {err}", file=sys.stderr)
    if not workflow_errors:
        print(f"OK: {workflow_file} publishes the :v{version} semver tag")

    return 1 if workflow_errors else 0


def main() -> None:
    """Entry point for the pre-commit hook and CI step."""
    repo_root = Path(__file__).resolve().parent.parent
    sys.exit(check(repo_root))


if __name__ == "__main__":
    main()
