"""
Tests for the justfile rotate-htpasswd alias (issue #227).

Verifies that `rotate-htpasswd` is defined as an alias for `gen-htpasswd`,
that its target recipe still exists, and that it is discoverable via
`just --list` when the just binary is available.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
JUSTFILE = REPO_ROOT / "justfile"


def _justfile_content() -> str:
    return JUSTFILE.read_text()


def test_rotate_htpasswd_alias_defined() -> None:
    """The alias statement must exist exactly as written."""
    content = _justfile_content()
    assert re.search(r"(?m)^alias rotate-htpasswd := gen-htpasswd$", content)


def test_alias_target_recipe_exists() -> None:
    """The alias target recipe gen-htpasswd must still be defined."""
    content = _justfile_content()
    assert re.search(r"(?m)^gen-htpasswd:", content)


def test_gen_htpasswd_docstring_mentions_rotation() -> None:
    """The gen-htpasswd doc comment must describe both generation and rotation."""
    content = _justfile_content()
    assert "Generate or rotate configs/nginx/htpasswd" in content


@pytest.mark.skipif(shutil.which("just") is None, reason="just binary not on PATH")
def test_alias_listed_by_just_list() -> None:
    """`just --list` must surface rotate-htpasswd for discoverability."""
    result = subprocess.run(
        ["just", "--list"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "rotate-htpasswd" in result.stdout


@pytest.mark.skipif(shutil.which("just") is None, reason="just binary not on PATH")
def test_alias_dry_run_dispatches_to_gen_htpasswd() -> None:
    """Dry-running the alias must print the gen-htpasswd recipe body."""
    result = subprocess.run(
        ["just", "-n", "rotate-htpasswd"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    # `just -n` prints shebang recipe bodies to stderr
    combined = result.stdout + result.stderr
    assert "htpasswd -nbB loki" in combined
