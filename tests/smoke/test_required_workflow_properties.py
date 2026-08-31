import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "_required.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RULESET_FIXTURES = ROOT / "tests" / "fixtures" / "rulesets"
BASELINE_RULESET = RULESET_FIXTURES / "homeric-main-baseline.json"
EXTRAS_RULESET = RULESET_FIXTURES / "homeric-main-extras.json"
QUEUE_DISABLED_READBACK = RULESET_FIXTURES / "homeric-main-merge-queue-disabled.json"
QUEUE_ACTIVE_READBACK = RULESET_FIXTURES / "homeric-main-merge-queue-active.json"

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
TEST_AGGREGATE_DEPENDENCIES = ("unit-tests", "test", "atlas-dashboard")
TEST_AGGREGATE_ENV_BY_DEPENDENCY = {
    "unit-tests": "UNIT_TESTS_RESULT",
    "test": "EXPORTER_TEST_RESULT",
    "atlas-dashboard": "ATLAS_DASHBOARD_RESULT",
}
NON_SUCCESS_RESULTS = ("failure", "cancelled", "skipped")
EXPECTED_CONCURRENCY_GROUP = (
    "${{ github.workflow }}-${{ github.event_name }}-"
    "${{ github.event.pull_request.number || github.sha }}"
)


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


REQUIRED_JOB_NAMES = frozenset(
    _required_contexts(_load_ruleset(BASELINE_RULESET))
    | _required_contexts(_load_ruleset(EXTRAS_RULESET))
)


def test_required_workflow_exists() -> None:
    assert WORKFLOW.exists(), f"Workflow file not found: {WORKFLOW}"


def test_unit_tests_job_invokes_pytest() -> None:
    content = WORKFLOW.read_text()
    assert "pytest" in content, (
        "unit-tests job in _required.yml does not invoke pytest — "
        "test regressions will reach main undetected"
    )


def _test_aggregate_step(job: dict) -> dict:
    needs = job.get("needs", [])
    assert len(needs) == len(TEST_AGGREGATE_DEPENDENCIES)
    assert set(needs) == set(TEST_AGGREGATE_DEPENDENCIES), (
        "test aggregate must depend on every real in-workflow test producer"
    )
    assert job.get("if") == "always()", (
        "test aggregate must run after failed, cancelled, or skipped dependencies"
    )

    expected_env = {
        env_name: f"${{{{ needs.{dependency}.result }}}}"
        for dependency, env_name in TEST_AGGREGATE_ENV_BY_DEPENDENCY.items()
    }
    aggregate_steps = [
        step for step in job.get("steps", []) if step.get("env") == expected_env
    ]
    assert len(aggregate_steps) == 1, (
        "test aggregate must have one step that inspects every dependency result"
    )
    return aggregate_steps[0]


def _run_test_aggregate(
    job: dict, dependency_results: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    step = _test_aggregate_step(job)
    env = {"LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    env.update(
        {
            env_name: dependency_results[dependency]
            for dependency, env_name in TEST_AGGREGATE_ENV_BY_DEPENDENCY.items()
        }
    )
    return subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _reachable_job_outcomes(
    path: Path, event: str, dependency_results: dict[str, str]
) -> dict[str, str]:
    workflow = _load_workflow(path)
    assert event in workflow["on"], f"{path.name} does not run for {event}"

    outcomes: dict[str, str] = {}
    for job_id, job in workflow["jobs"].items():
        name = job.get("name", job_id)
        if name in REQUIRED_JOB_NAMES:
            if job_id == "test-aggregate":
                assert job.get("if") == "always()", (
                    "required test aggregate must run with if: always()"
                )
            else:
                assert not job.get("if"), (
                    f"required job {name} has conditional execution"
                )
                assert not job.get("needs"), (
                    f"required job {name} declares dependencies"
                )
            assert not any(step.get("if") for step in job.get("steps", [])), (
                f"required job {name} has a conditional validator step"
            )

        conditions = [job.get("if")]
        conditions.extend(step.get("if") for step in job.get("steps", []))
        event_tokens = (
            "github.event",
            "github.ref",
            "github.head_ref",
            "github.base_ref",
            "merge_group",
            "pull_request",
        )
        for condition in filter(None, conditions):
            assert not any(token in condition for token in event_tokens), (
                f"{path.name}:{job_id} has an event-sensitive condition "
                f"({condition!r}), so its real validation is not guaranteed "
                "to run for both pull_request and merge_group"
            )
        if job_id == "test-aggregate":
            completed = _run_test_aggregate(job, dependency_results)
            outcomes[name] = "success" if completed.returncode == 0 else "failure"
        elif path == CI_WORKFLOW and job_id in dependency_results:
            outcomes[name] = dependency_results[job_id]
        else:
            outcomes[name] = "success"
    return outcomes


def test_required_workflows_keep_pr_push_and_merge_group_triggers() -> None:
    for path in REQUIRED_WORKFLOWS:
        triggers = _load_workflow(path)["on"]

        for event, expected_config in EXISTING_TRIGGERS[path].items():
            assert triggers.get(event) == expected_config, (
                f"{path.name} changed its existing {event} behavior"
            )
        assert triggers.get("merge_group") == {"types": ["checks_requested"]}, (
            f"{path.name} must run for merge_group/checks_requested"
        )


@pytest.mark.parametrize("dependency", TEST_AGGREGATE_DEPENDENCIES)
@pytest.mark.parametrize("result", ("success", *NON_SUCCESS_RESULTS))
def test_pr_and_merge_group_run_the_same_producer_outcomes(
    dependency: str, result: str
) -> None:
    dependency_results = dict.fromkeys(TEST_AGGREGATE_DEPENDENCIES, "success")
    dependency_results[dependency] = result
    outcomes_by_event = {}
    for event in ("pull_request", "merge_group"):
        outcomes: dict[str, str] = {}
        for path in REQUIRED_WORKFLOWS:
            outcomes.update(_reachable_job_outcomes(path, event, dependency_results))
        outcomes_by_event[event] = outcomes

    assert outcomes_by_event["pull_request"] == outcomes_by_event["merge_group"]


def test_test_aggregate_accepts_only_all_successful_dependencies() -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    dependency_results = dict.fromkeys(TEST_AGGREGATE_DEPENDENCIES, "success")

    completed = _run_test_aggregate(
        workflow["jobs"]["test-aggregate"], dependency_results
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("dependency", TEST_AGGREGATE_DEPENDENCIES)
@pytest.mark.parametrize("result", NON_SUCCESS_RESULTS)
def test_test_aggregate_rejects_non_success_dependency(
    dependency: str, result: str
) -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    dependency_results = dict.fromkeys(TEST_AGGREGATE_DEPENDENCIES, "success")
    dependency_results[dependency] = result

    completed = _run_test_aggregate(
        workflow["jobs"]["test-aggregate"], dependency_results
    )

    assert completed.returncode != 0, (
        f"test aggregate accepted {dependency}={result} as successful"
    )


@pytest.mark.parametrize(
    ("conditional_target", "expected_message"),
    (
        ("job", "required job build has conditional execution"),
        ("validator-step", "required job build has a conditional validator step"),
    ),
)
def test_required_job_conditions_are_rejected(
    tmp_path: Path, conditional_target: str, expected_message: str
) -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    build = workflow["jobs"]["build"]
    if conditional_target == "job":
        build["if"] = "false"
    else:
        build["steps"][-1]["if"] = "false"

    fixture = tmp_path / "ci.yml"
    fixture.write_text(yaml.safe_dump(workflow, sort_keys=False))
    dependency_results = dict.fromkeys(TEST_AGGREGATE_DEPENDENCIES, "success")

    with pytest.raises(AssertionError, match=expected_message):
        _reachable_job_outcomes(fixture, "pull_request", dependency_results)


def test_nonaggregate_required_job_dependencies_are_rejected(tmp_path: Path) -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    workflow["jobs"]["build"]["needs"] = ["unit-tests"]

    fixture = tmp_path / "ci.yml"
    fixture.write_text(yaml.safe_dump(workflow, sort_keys=False))
    dependency_results = dict.fromkeys(TEST_AGGREGATE_DEPENDENCIES, "success")

    with pytest.raises(
        AssertionError, match="required job build declares dependencies"
    ):
        _reachable_job_outcomes(fixture, "pull_request", dependency_results)


def test_merge_queue_has_no_smoke_only_carrier() -> None:
    assert not SMOKE_WORKFLOW.exists(), (
        "merge-queue-smoke.yml must be removed; real required producers own "
        "the merge_group event"
    )
    workflows = ROOT / ".github" / "workflows"
    for pattern in ("*.yml", "*.yaml"):
        for path in workflows.glob(pattern):
            workflow = _load_workflow(path)
            job_names = {
                job.get("name", job_id)
                for job_id, job in workflow.get("jobs", {}).items()
            }
            assert "merge-queue-smoke" not in job_names, (
                f"{path.name} still emits the obsolete merge-queue-smoke context"
            )


def test_smoke_carrier_detection_includes_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "hidden-smoke.yaml").write_text(
        "name: Hidden Smoke\n"
        "'on':\n"
        "  merge_group:\n"
        "    types: [checks_requested]\n"
        "jobs:\n"
        "  smoke:\n"
        "    name: merge-queue-smoke\n"
        "    runs-on: ubuntu-latest\n"
    )
    monkeypatch.setitem(globals(), "ROOT", tmp_path)

    with pytest.raises(AssertionError, match="obsolete merge-queue-smoke context"):
        test_merge_queue_has_no_smoke_only_carrier()


def _modeled_concurrency_key(
    path: Path, event: str, sha: str, pull_request_number: int | None = None
) -> str:
    workflow = _load_workflow(path)
    assert workflow["concurrency"]["group"] == EXPECTED_CONCURRENCY_GROUP
    identity = str(pull_request_number) if event == "pull_request" else sha
    return f"{workflow['name']}-{event}-{identity}"


def test_required_workflow_concurrency_uses_pr_number_or_revision() -> None:
    for path in REQUIRED_WORKFLOWS:
        concurrency = _load_workflow(path)["concurrency"]
        assert concurrency["group"] == EXPECTED_CONCURRENCY_GROUP
        assert concurrency["cancel-in-progress"] == "true"


def test_concurrency_keys_isolate_unrelated_fork_prs_with_same_branch_name() -> None:
    for path in REQUIRED_WORKFLOWS:
        first = _modeled_concurrency_key(path, "pull_request", "head-a", 101)
        second = _modeled_concurrency_key(path, "pull_request", "head-b", 102)
        repeated = _modeled_concurrency_key(path, "pull_request", "head-c", 101)

        assert first != second
        assert first == repeated


def test_concurrency_keys_isolate_pull_request_and_merge_group() -> None:
    for path in REQUIRED_WORKFLOWS:
        pull_request = _modeled_concurrency_key(path, "pull_request", "same", 101)
        merge_group = _modeled_concurrency_key(path, "merge_group", "same")

        assert pull_request != merge_group


def test_all_required_contexts_have_pr_and_merge_group_supplying_jobs() -> None:
    baseline_contexts = _required_contexts(_load_ruleset(BASELINE_RULESET))
    extras_contexts = _required_contexts(_load_ruleset(EXTRAS_RULESET))
    required_contexts = baseline_contexts | extras_contexts
    assert baseline_contexts.isdisjoint(extras_contexts)
    assert len(required_contexts) == 14

    for event in ("pull_request", "merge_group"):
        dependency_results = dict.fromkeys(TEST_AGGREGATE_DEPENDENCIES, "success")
        emitted_contexts: set[str] = set()
        for path in REQUIRED_WORKFLOWS:
            emitted_contexts.update(
                _reachable_job_outcomes(path, event, dependency_results)
            )
        missing = required_contexts - emitted_contexts
        assert not missing, (
            f"{event} workflows do not emit required contexts: {sorted(missing)}"
        )


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
