from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "_required.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
MERGE_QUEUE_RUNBOOK = ROOT / "docs" / "ci" / "merge-queue.md"

# Live required contexts on main, captured from the active repository rulesets
# for issue #550. Keep this map aligned with the supplying workflow so a trigger
# edit cannot silently strand a required context on merge-group SHAs.
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


def test_required_workflows_support_merge_queue_events() -> None:
    for path in REQUIRED_CONTEXTS:
        triggers = _load_workflow(path)["on"]

        for event, expected_config in EXISTING_TRIGGERS[path].items():
            assert triggers.get(event) == expected_config, (
                f"{path.name} changed its existing {event} behavior"
            )
        assert triggers.get("merge_group") == {"types": ["checks_requested"]}, (
            f"{path.name} must run for merge_group/checks_requested"
        )


def test_live_required_contexts_still_have_supplying_jobs() -> None:
    for path, expected_contexts in REQUIRED_CONTEXTS.items():
        workflow = _load_workflow(path)
        emitted_contexts = {
            job.get("name", job_id) for job_id, job in workflow["jobs"].items()
        }

        missing = expected_contexts - emitted_contexts
        assert not missing, (
            f"{path.name} no longer emits required contexts: {sorted(missing)}"
        )


def test_merge_queue_activation_runbook_records_approved_policy() -> None:
    content = MERGE_QUEUE_RUNBOOK.read_text()

    for required_value in (
        '"type": "merge_queue"',
        '"check_response_timeout_minutes": 60',
        '"grouping_strategy": "ALLGREEN"',
        '"max_entries_to_build": 10',
        '"max_entries_to_merge": 5',
        '"merge_method": "SQUASH"',
        '"min_entries_to_merge": 1',
        '"min_entries_to_merge_wait_minutes": 5',
    ):
        assert required_value in content

    assert "Do not activate before" in content
    assert "Odysseus#386" in content
    assert "POST /repos/HomericIntelligence/Argus/rulesets" in content
    assert '"actor_id": 5' in content
    assert '"actor_type": "RepositoryRole"' in content
    assert '"bypass_mode": "pull_request"' in content
