#!/usr/bin/env python3
"""Pre-commit hook: asserts pixi.toml version is reflected in CHANGELOG.md.

Valid states:
  1. CHANGELOG has ## [<version>] header — version has been formally released.
  2. CHANGELOG has ## [Unreleased] with no version bump yet (pixi.toml version
     matches the latest versioned section in CHANGELOG, meaning no bump happened).

Invalid state:
  pixi.toml version does not appear as a versioned header AND differs from all
  existing versioned sections (bumped without updating CHANGELOG).
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


def load_version(pixi_toml: Path) -> str:
    """Return the version string from pixi.toml [project].version or [workspace].version."""
    with pixi_toml.open("rb") as f:
        data = tomllib.load(f)
    section = data.get("workspace") or data.get("project")
    if section is None:
        raise KeyError("pixi.toml has neither [workspace] nor [project] section")
    return section["version"]


def versioned_sections(changelog: Path) -> list[str]:
    """Return all version strings that appear as ## [X.Y.Z] headers in CHANGELOG."""
    pattern = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)
    content = changelog.read_text()
    return pattern.findall(content)


def has_versioned_header(version: str, changelog: Path) -> bool:
    """Return True if CHANGELOG contains a ## [<version>] section header."""
    pattern = re.compile(rf"^## \[{re.escape(version)}\]", re.MULTILINE)
    return not pattern.search(changelog.read_text()) is None  # noqa: SIM103


def check(repo_root: Path) -> int:
    """Run the consistency check. Returns 0 on success, 1 on failure."""
    pixi_toml = repo_root / "pixi.toml"
    changelog = repo_root / "CHANGELOG.md"

    if not pixi_toml.exists():
        print(f"ERROR: {pixi_toml} not found", file=sys.stderr)
        return 1
    if not changelog.exists():
        print(f"ERROR: {changelog} not found", file=sys.stderr)
        return 1

    version = load_version(pixi_toml)
    sections = versioned_sections(changelog)

    # Valid: version appears as a released section
    if version in sections:
        print(f"OK: version {version} found in CHANGELOG.md as a released section.")
        return 0

    # Valid: no version bump yet — pixi.toml still matches (or is behind) the latest
    # released section, meaning we're in an [Unreleased] development state.
    if sections and version == sections[0]:
        print(
            f"OK: version {version} matches latest CHANGELOG section (unreleased state)."
        )
        return 0

    # Also valid if there are no sections at all and version is the initial one —
    # i.e., project hasn't cut its first release yet.
    if not sections:
        print(
            f"OK: no versioned sections in CHANGELOG yet; "
            f"version {version} is pre-first-release."
        )
        return 0

    print(
        f"ERROR: pixi.toml version is {version!r} but CHANGELOG.md has no "
        f"## [{version}] section.\n"
        f"  Found sections: {sections}\n"
        f"  Run 'bash scripts/bump-version.sh <patch|minor|major>' or manually "
        f"add a ## [{version}] section to CHANGELOG.md.",
        file=sys.stderr,
    )
    return 1


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.exit(check(repo_root))


if __name__ == "__main__":
    main()
