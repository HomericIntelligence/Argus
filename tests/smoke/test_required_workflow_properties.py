from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "_required.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

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
