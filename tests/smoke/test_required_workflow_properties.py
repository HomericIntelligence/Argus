from pathlib import Path

WORKFLOW = Path(__file__).parent.parent.parent / ".github" / "workflows" / "_required.yml"


def test_required_workflow_exists() -> None:
    assert WORKFLOW.exists(), f"Workflow file not found: {WORKFLOW}"


def test_unit_tests_job_invokes_pytest() -> None:
    content = WORKFLOW.read_text()
    assert "pytest" in content, (
        "unit-tests job in _required.yml does not invoke pytest — "
        "test regressions will reach main undetected"
    )
