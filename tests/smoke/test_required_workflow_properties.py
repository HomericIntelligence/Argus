import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "_required.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RULESET_FIXTURES = ROOT / "tests" / "fixtures" / "rulesets"
BASELINE_RULESET = RULESET_FIXTURES / "homeric-main-baseline.json"
EXTRAS_RULESET = RULESET_FIXTURES / "homeric-main-extras.json"
QUEUE_DISABLED_READBACK = RULESET_FIXTURES / "homeric-main-merge-queue-disabled.json"
QUEUE_ACTIVE_READBACK = RULESET_FIXTURES / "homeric-main-merge-queue-active.json"

# Queue readiness requires every workflow that supplies a required context to
# run against the synthetic merge-group SHA without renaming or dropping a job.
REQUIRED_WORKFLOWS = (WORKFLOW, CI_WORKFLOW)
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


def _load_ruleset(path: Path) -> dict:
    return json.loads(path.read_text())


def _required_contexts(ruleset: dict) -> set[str]:
    return {
        check["context"]
        for rule in ruleset["rules"]
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    }


def test_required_workflow_exists() -> None:
    assert WORKFLOW.exists(), f"Workflow file not found: {WORKFLOW}"


def test_unit_tests_job_invokes_pytest() -> None:
    content = WORKFLOW.read_text()
    assert "pytest" in content, (
        "unit-tests job in _required.yml does not invoke pytest — "
        "test regressions will reach main undetected"
    )


def test_required_workflows_run_for_queue_checks_without_trigger_regressions() -> None:
    for path in REQUIRED_WORKFLOWS:
        triggers = _load_workflow(path)["on"]

        for event, expected_config in EXISTING_TRIGGERS[path].items():
            assert triggers.get(event) == expected_config, (
                f"{path.name} changed its existing {event} behavior"
            )
        assert triggers.get("merge_group") == {"types": ["checks_requested"]}, (
            f"{path.name} must run for merge_group/checks_requested"
        )


def test_all_required_contexts_have_queue_ready_supplying_jobs() -> None:
    baseline_contexts = _required_contexts(_load_ruleset(BASELINE_RULESET))
    extras_contexts = _required_contexts(_load_ruleset(EXTRAS_RULESET))
    required_contexts = baseline_contexts | extras_contexts
    assert baseline_contexts.isdisjoint(extras_contexts)
    assert len(required_contexts) == 14

    emitted_contexts: set[str] = set()

    for path in REQUIRED_WORKFLOWS:
        workflow = _load_workflow(path)
        assert workflow["on"].get("merge_group") == {"types": ["checks_requested"]}, (
            f"{path.name} cannot supply required contexts to a merge-group SHA"
        )

        emitted_contexts.update(
            job.get("name", job_id) for job_id, job in workflow["jobs"].items()
        )
    missing = required_contexts - emitted_contexts
    assert not missing, f"workflows no longer emit required contexts: {sorted(missing)}"


def test_queue_activation_readbacks_preserve_exact_policy_and_bypass() -> None:
    baseline = _load_ruleset(BASELINE_RULESET)
    disabled = _load_ruleset(QUEUE_DISABLED_READBACK)
    active = _load_ruleset(QUEUE_ACTIVE_READBACK)

    assert disabled["enforcement"] == "disabled"
    assert active["enforcement"] == "active"

    immutable_fields = (
        "id",
        "name",
        "source_type",
        "target",
        "conditions",
        "rules",
        "bypass_actors",
    )
    assert {field: disabled[field] for field in immutable_fields} == {
        field: active[field] for field in immutable_fields
    }
    assert active["name"] == "homeric-main-merge-queue"
    assert active["source_type"] == "Repository"
    assert active["target"] == "branch"
    assert active["conditions"] == {
        "ref_name": {"exclude": [], "include": ["refs/heads/main"]}
    }
    assert active["bypass_actors"] == baseline["bypass_actors"]
    assert active["rules"] == [
        {
            "type": "merge_queue",
            "parameters": {
                "check_response_timeout_minutes": 60,
                "grouping_strategy": "ALLGREEN",
                "max_entries_to_build": 10,
                "max_entries_to_merge": 5,
                "merge_method": "SQUASH",
                "min_entries_to_merge": 1,
                "min_entries_to_merge_wait_minutes": 5,
            },
        }
    ]
