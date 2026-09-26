"""Guard the CODEOWNERS file against coverage regressions.

Issue #371: ``docker-compose.yml``, ``justfile``, and ``pixi.toml`` control the
stack's runtime and the toolchain contributors run, but they were covered only by
the ``*`` catch-all. They must keep an explicit owner entry.

Two failure modes matter here, and both are checked below:

1. A required file silently falls back to the catch-all.
2. A second ``CODEOWNERS`` is added at the repository root. GitHub reads only the
   first file it finds (root, then ``.github/``, then ``docs/``), so a second copy
   disarms the ownership rules in the file that was supposed to gain entries.

CODEOWNERS resolution is "last matching pattern wins", and a bare filename
pattern matches at any depth.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CODEOWNERS = REPO_ROOT / ".github" / "CODEOWNERS"

# Files that drive runtime topology, container flags, or the task runner.
REQUIRED_EXPLICIT_COVERAGE = (
    "docker-compose.yml",
    "justfile",
    "pixi.toml",
)

# Security-sensitive patterns that predate issue #371. They must survive any
# future edit: losing one widens the review gate without any visible change to
# the entry that replaced it.
REQUIRED_RETAINED_PATTERNS = (
    "/configs/prometheus.yml",
    "/rules/",
    "/configs/grafana/",
    "/dashboards/",
    "/configs/loki.yml",
    "/configs/promtail.yml",
    "/.github/",
)

# Paths GitHub consults before .github/CODEOWNERS. Any one of them shadows it.
COMPETING_LOCATIONS = (
    REPO_ROOT / "CODEOWNERS",
    REPO_ROOT / "docs" / "CODEOWNERS",
)

CATCH_ALL = "*"


def _entries() -> list[tuple[str, list[str]]]:
    """Return (pattern, owners) pairs, skipping blanks and comments."""
    entries: list[tuple[str, list[str]]] = []
    for line in CODEOWNERS.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        entries.append((parts[0], parts[1:]))
    return entries


def _matches(pattern: str, filename: str) -> bool:
    """Approximate CODEOWNERS matching for the top-level files under test.

    Only the two forms used in this file are supported: the ``*`` catch-all and a
    bare filename or root-anchored pattern.
    """
    if pattern == CATCH_ALL:
        return True
    pattern = pattern.removeprefix("/")
    return filename == pattern or filename.endswith(f"/{pattern}")


def _resolver() -> dict[str, str]:
    """Map each required file to the pattern that would claim it last."""
    resolved: dict[str, str] = {}
    for filename in REQUIRED_EXPLICIT_COVERAGE:
        winner = None
        for pattern, _owners in _entries():
            if _matches(pattern, filename):
                winner = pattern
        assert winner is not None, (
            f"no CODEOWNERS pattern matches {filename!r}; the catch-all is missing"
        )
        resolved[filename] = winner
    return resolved


def test_codeowners_file_exists() -> None:
    assert CODEOWNERS.exists(), f"{CODEOWNERS} does not exist"


def test_no_competing_codeowners_shadows_this_file() -> None:
    """A root CODEOWNERS overrides .github/CODEOWNERS and disarms it."""
    shadowing = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in COMPETING_LOCATIONS
        if path.exists()
    )
    assert not shadowing, (
        f"these files take precedence over .github/CODEOWNERS: {shadowing}. "
        "GitHub reads only the first file it finds, so every entry here would "
        "stop applying (issue #371)."
    )


def test_operational_files_have_explicit_owners() -> None:
    """Each operational file must be claimed by a non-catch-all pattern."""
    resolved = _resolver()
    falling_back = sorted(f for f, pattern in resolved.items() if pattern == CATCH_ALL)
    assert not falling_back, (
        "these operational files are covered only by the catch-all CODEOWNERS "
        f"entry: {falling_back}. Add explicit entries (issue #371)."
    )


def test_security_sensitive_patterns_are_retained() -> None:
    """Adding coverage must not drop the ownership rules that already existed."""
    patterns = {pattern for pattern, _owners in _entries()}
    missing = sorted(p for p in REQUIRED_RETAINED_PATTERNS if p not in patterns)
    assert not missing, (
        f"CODEOWNERS lost these pre-existing ownership patterns: {missing}. "
        "Issue #371 only adds coverage; it must not remove any."
    )


def test_explicit_entries_have_owners() -> None:
    """A pattern with no owner list is a syntax error and silently ignored."""
    unowned = [pattern for pattern, owners in _entries() if not owners]
    assert not unowned, f"CODEOWNERS patterns with no owners: {unowned}"
