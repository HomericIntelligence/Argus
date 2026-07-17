from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "_required.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
MERGE_QUEUE_RUNBOOK = ROOT / "docs" / "ci" / "merge-queue.md"

# These are the 14 contexts currently required for Argus main across its
# existing rulesets. Queue readiness requires every supplying workflow to run
# against the synthetic merge-group SHA without renaming or dropping a job.
REQUIRED_CONTEXTS = {
    WORKFLOW: {
        "lint",
        "unit-tests",
        "integration-tests",
        "security/dependency-scan",
        "security/secrets-scan",
        "config-validate",
        "schema-validation",
        "deps/version-sync",
    },
    CI_WORKFLOW: {
        "Validate configs",
        "test",
        "package",
        "install",
        "release",
        "build",
    },
}
EXISTING_TRIGGERS = {
    WORKFLOW: {
        "pull_request": {"branches": ["main"]},
        "push": {"branches": ["main"]},
    },
    CI_WORKFLOW: {
        "pull_request": {"branches": ["main"]},
        "push": {"branches": ["main", "feature/**", "fix/**", "chore/**"]},
    },
}


def _load_workflow(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def test_required_workflow_exists() -> None:
    assert WORKFLOW.exists(), f"Workflow file not found: {WORKFLOW}"


def test_unit_tests_job_invokes_pytest() -> None:
    content = WORKFLOW.read_text()
    assert "pytest" in content, (
        "unit-tests job in _required.yml does not invoke pytest — "
        "test regressions will reach main undetected"
    )


def test_required_workflows_run_for_queue_checks_without_trigger_regressions() -> None:
    for path in REQUIRED_CONTEXTS:
        triggers = _load_workflow(path)["on"]

        for event, expected_config in EXISTING_TRIGGERS[path].items():
            assert triggers.get(event) == expected_config, (
                f"{path.name} changed its existing {event} behavior"
            )
        assert triggers.get("merge_group") == {"types": ["checks_requested"]}, (
            f"{path.name} must run for merge_group/checks_requested"
        )


def test_all_required_contexts_have_queue_ready_supplying_jobs() -> None:
    observed_contexts: set[str] = set()

    for path, expected_contexts in REQUIRED_CONTEXTS.items():
        workflow = _load_workflow(path)
        assert workflow["on"].get("merge_group") == {"types": ["checks_requested"]}, (
            f"{path.name} cannot supply required contexts to a merge-group SHA"
        )

        emitted_contexts = {
            job.get("name", job_id) for job_id, job in workflow["jobs"].items()
        }
        missing = expected_contexts - emitted_contexts
        assert not missing, (
            f"{path.name} no longer emits required contexts: {sorted(missing)}"
        )
        observed_contexts.update(expected_contexts)

    assert len(observed_contexts) == 14


def test_merge_queue_runbook_uses_durable_cross_repository_references() -> None:
    content = MERGE_QUEUE_RUNBOOK.read_text()
    superseded_reference = "Odysseus" + " #4" + "16"
    superseded_compact_reference = "Odysseus" + "#4" + "16"

    assert superseded_compact_reference not in content
    assert superseded_reference not in content
    assert "https://github.com/HomericIntelligence/Odysseus/issues/386" in content
    assert "https://github.com/HomericIntelligence/Odysseus/pull/417" in content


def test_post_put_runbook_verifies_exact_live_policy_before_smoke() -> None:
    content = MERGE_QUEUE_RUNBOOK.read_text()
    put_index = content.index(
        'gh api --method PUT "repos/${REPO}/rulesets/${QUEUE_ID}"'
    )
    smoke_index = content.index("Then enqueue one representative smoke PR.")
    post_put_verification = content[put_index:smoke_index]

    required_assertions = (
        'gh api "repos/${REPO}/rulesets/${QUEUE_ID}"',
        ".id == ($id | tonumber)",
        ".name == $name",
        '.source_type == "Repository"',
        '.target == "branch"',
        '.enforcement == "active"',
        ".conditions.ref_name.exclude == []",
        '.conditions.ref_name.include == ["refs/heads/main"]',
        "and (.rules | length) == 1",
        '"type": "merge_queue"',
        '"check_response_timeout_minutes": 60',
        '"grouping_strategy": "ALLGREEN"',
        '"max_entries_to_build": 10',
        '"max_entries_to_merge": 5',
        '"merge_method": "SQUASH"',
        '"min_entries_to_merge": 1',
        '"min_entries_to_merge_wait_minutes": 5',
        "jq -S '.bypass_actors' /tmp/argus-baseline.before.json",
        "jq -S '.bypass_actors' /tmp/argus-merge-queue-ruleset.after.json",
        "diff -u /tmp/argus-required-contexts.expected.json",
        "/tmp/argus-required-contexts.after.json",
        "jq -e 'length == 14' /tmp/argus-required-contexts.after.json",
    )
    for assertion in required_assertions:
        assert assertion in post_put_verification, (
            f"post-PUT verification is missing: {assertion}"
        )
