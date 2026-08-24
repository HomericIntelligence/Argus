import json
import re
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

# The merge queue runs ONLY merge-queue-smoke.yml (single merge-gate job,
# <5 min, one runner slot). The full workflows below must keep their PR/push
# triggers unchanged and must NOT run for merge_group.
SMOKE_WORKFLOW = ROOT / ".github" / "workflows" / "merge-queue-smoke.yml"
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


def test_integration_tests_job_validates_images() -> None:
    """The integration-tests job must still run docker image validation.

    Guards the full validation chain (workflow -> justfile -> runner script
    -> validator) so the docker image format check cannot be silently
    hollowed out at any link.
    """
    content = WORKFLOW.read_text()
    job_block_re = re.compile(
        r"^  integration-tests:.*?(?=^  [a-z][a-z0-9-]*:|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    match = job_block_re.search(content)
    assert match, (
        "integration-tests job not found in _required.yml — "
        "the required check was removed or renamed; restore it or update "
        "this smoke test to match the new job name"
    )
    assert "just ci-integration-tests" in match.group(0), (
        "integration-tests job in _required.yml no longer invokes "
        "`just ci-integration-tests` — the docker image validation entry "
        "point was removed; restore the containerized check step or update "
        "this smoke test if validation moved"
    )

    justfile = (ROOT / "justfile").read_text()
    assert "ci-integration-tests:" in justfile, (
        "justfile no longer defines the ci-integration-tests recipe — "
        "the workflow's image validation step would fail or no-op; "
        "restore the recipe or update this smoke test"
    )

    runner = (ROOT / "scripts" / "run_ci_local.sh").read_text()
    assert "scripts/validate_compose_images.py" in runner, (
        "run_ci_local.sh no longer runs scripts/validate_compose_images.py "
        "for integration-tests — docker image name formats go unvalidated; "
        "restore the invocation or update this smoke test"
    )

    validator = (ROOT / "scripts" / "validate_compose_images.py").read_text()
    required_markers = [
        "valid_pattern",
        "INVALID image reference format",
        "--include=docker-compose*.yml",
        "--include=docker-compose*.yaml",
    ]
    missing = [marker for marker in required_markers if marker not in validator]
    assert not missing, (
        f"validate_compose_images.py is missing docker image validation "
        f"markers: {missing}. The validation logic was hollowed out — "
        f"restore the valid_pattern regex and the grep over "
        f"docker-compose image: lines, or update this smoke test if the "
        f"validation moved."
    )


def test_required_workflows_keep_pr_triggers_and_skip_merge_group() -> None:
    for path in REQUIRED_WORKFLOWS:
        triggers = _load_workflow(path)["on"]

        for event, expected_config in EXISTING_TRIGGERS[path].items():
            assert triggers.get(event) == expected_config, (
                f"{path.name} changed its existing {event} behavior"
            )
        assert "merge_group" not in triggers, (
            f"{path.name} must not run for merge_group — merge-queue-smoke.yml "
            "owns that event so the queue consumes a single runner slot"
        )


def test_merge_group_runs_only_the_smoke_workflow() -> None:
    """The merge queue must run exactly one fast smoke job."""
    smoke = _load_workflow(SMOKE_WORKFLOW)
    assert smoke["on"] == {"merge_group": {"types": ["checks_requested"]}}
    assert list(smoke["jobs"]) == ["merge-queue-smoke"]
    assert smoke["jobs"]["merge-queue-smoke"]["name"] == "merge-queue-smoke"
    assert smoke["jobs"]["merge-queue-smoke"]["timeout-minutes"] == "5"


def test_all_required_contexts_have_pr_supplying_jobs() -> None:
    baseline_contexts = _required_contexts(_load_ruleset(BASELINE_RULESET))
    extras_contexts = _required_contexts(_load_ruleset(EXTRAS_RULESET))
    required_contexts = baseline_contexts | extras_contexts
    assert baseline_contexts.isdisjoint(extras_contexts)
    assert len(required_contexts) == 14

    emitted_contexts: set[str] = set()

    for path in REQUIRED_WORKFLOWS:
        workflow = _load_workflow(path)

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
