"""
Tests for scripts/check-env-docs.sh — the .env.example <-> AGENTS.md drift gate.

The gate fails (exit 1) when a variable exists in .env.example but is not
documented in AGENTS.md's Environment Variables region, or when AGENTS.md
documents a variable that has no .env.example entry. Exit 2 signals missing
input files; exit 0 means no drift.
"""
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-env-docs.sh"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    """Run check-env-docs.sh with the given positional arguments."""
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def make_fixture_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the real .env.example and AGENTS.md into tmp_path as fixtures."""
    env_copy = tmp_path / ".env.example"
    doc_copy = tmp_path / "AGENTS.md"
    shutil.copy(REPO_ROOT / ".env.example", env_copy)
    shutil.copy(REPO_ROOT / "AGENTS.md", doc_copy)
    return env_copy, doc_copy


class TestLiveRepo:
    def test_live_repo_has_no_drift(self) -> None:
        # Consistency of these two files IS the invariant being enforced, so
        # asserting it against the live repo is the point of the gate.
        result = run_script()
        assert result.returncode == 0, result.stderr
        assert "OK:" in result.stdout

    def test_ok_sentinel_names_no_variables(self) -> None:
        # The success line must not enumerate variable names (token hygiene).
        result = run_script()
        assert result.returncode == 0
        assert "=" not in result.stdout


class TestMissingInputs:
    def test_missing_env_example_exits_2(self, tmp_path: Path) -> None:
        _, doc_copy = make_fixture_repo(tmp_path)
        result = run_script(str(tmp_path / "does-not-exist"), str(doc_copy))
        assert result.returncode == 2
        assert "not found" in result.stderr

    def test_missing_doc_file_exits_2(self, tmp_path: Path) -> None:
        env_copy, _ = make_fixture_repo(tmp_path)
        result = run_script(str(env_copy), str(tmp_path / "does-not-exist"))
        assert result.returncode == 2
        assert "not found" in result.stderr


class TestDriftDetection:
    def test_detects_env_var_missing_from_docs(self, tmp_path: Path) -> None:
        env_copy, doc_copy = make_fixture_repo(tmp_path)
        env_copy.write_text(env_copy.read_text() + "\nUNDOCUMENTED_TEST_VAR=x\n")
        result = run_script(str(env_copy), str(doc_copy))
        assert result.returncode == 1
        assert "UNDOCUMENTED_TEST_VAR" in result.stderr
        assert "undocumented" in result.stderr

    def test_detects_doc_var_missing_from_env(self, tmp_path: Path) -> None:
        env_copy, doc_copy = make_fixture_repo(tmp_path)
        text = doc_copy.read_text()
        marker = "## Environment Variables\n"
        doc_copy.write_text(
            text.replace(marker, marker + "\nSee `IMAGINARY_DOC_VAR` below.\n", 1)
        )
        result = run_script(str(env_copy), str(doc_copy))
        assert result.returncode == 1
        assert "IMAGINARY_DOC_VAR" in result.stderr
        assert "absent from" in result.stderr

    def test_reports_both_directions_at_once(self, tmp_path: Path) -> None:
        env_copy, doc_copy = make_fixture_repo(tmp_path)
        env_copy.write_text(env_copy.read_text() + "\nNEW_ENV_VAR=x\n")
        text = doc_copy.read_text()
        marker = "## Environment Variables\n"
        doc_copy.write_text(
            text.replace(marker, marker + "\nSee `NEW_DOC_VAR` below.\n", 1)
        )
        result = run_script(str(env_copy), str(doc_copy))
        assert result.returncode == 1
        assert "NEW_ENV_VAR" in result.stderr
        assert "NEW_DOC_VAR" in result.stderr


class TestAllowlists:
    def test_commented_env_entry_counts_as_defined(self, tmp_path: Path) -> None:
        # "# FOO=..." is documented-by-comment, same contract as
        # scripts/check-env-example.sh.
        env_copy, doc_copy = make_fixture_repo(tmp_path)
        text = doc_copy.read_text()
        marker = "## Environment Variables\n"
        doc_copy.write_text(text.replace(marker, marker + "\n- `COMMENTED_VAR` — optional knob.\n", 1))
        env_copy.write_text(env_copy.read_text() + "\n# COMMENTED_VAR=\n")
        result = run_script(str(env_copy), str(doc_copy))
        assert result.returncode == 0, result.stderr

    def test_doc_only_allowlist_suppresses_reverse_drift(self, tmp_path: Path) -> None:
        # HOSTNAME is mentioned in AGENTS.md but intentionally absent from
        # .env.example; the allowlist must keep this combination green.
        env_copy, doc_copy = make_fixture_repo(tmp_path)
        text = doc_copy.read_text()
        marker = "## Environment Variables\n"
        doc_copy.write_text(
            text.replace(marker, marker + "\nMentions `HOSTNAME` and `CONTAINER_CMD`.\n", 1),
        )
        result = run_script(str(env_copy), str(doc_copy))
        assert result.returncode == 0, result.stderr

    def test_atlas_prefix_skipped_in_both_directions(self, tmp_path: Path) -> None:
        # ATLAS_* vars live in dashboard/README.md, not AGENTS.md; removing an
        # ATLAS_ row from .env.example must not flip the gate either direction.
        env_copy, doc_copy = make_fixture_repo(tmp_path)
        lines = [
            line
            for line in env_copy.read_text().splitlines(keepends=True)
            if not line.startswith("ATLAS_AUTH_MODE=")
        ]
        env_copy.write_text("".join(lines))
        result = run_script(str(env_copy), str(doc_copy))
        assert result.returncode == 0, result.stderr


class TestExtractionRegion:
    def test_vars_outside_doc_region_are_ignored(self, tmp_path: Path) -> None:
        # A backticked var mentioned after the "## Scrape Targets" heading is
        # outside the extraction region and must not trigger reverse drift.
        env_copy, doc_copy = make_fixture_repo(tmp_path)
        doc_copy.write_text(
            doc_copy.read_text() + "\nProse mentioning `AFTER_REGION_VAR` at the end.\n"
        )
        result = run_script(str(env_copy), str(doc_copy))
        assert result.returncode == 0, result.stderr
