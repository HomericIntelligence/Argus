"""Regression guard for the composed lint environment (issue #310).

The ``lint`` pixi environment must compose over the default environment's
dependencies (no ``no-default-feature`` flag) so that the weekly pip-audit
CVE scan covers the default toolchain (bandit, ruff, pytest, pyyaml,
yamllint) in addition to pip-audit itself.
"""

import tomllib
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def _pixi() -> dict:
    with (_ROOT / "pixi.toml").open("rb") as f:
        return tomllib.load(f)


def test_lint_env_composes_over_default() -> None:
    """The lint environment must not opt out of default-feature deps."""
    envs = _pixi().get("environments", {})
    assert "lint" in envs, "pixi.toml must define a lint environment"
    lint = envs["lint"]
    assert not lint.get("no-default-feature", False), (
        "lint environment sets no-default-feature; pip-audit would only "
        "scan pip-audit's own dependencies, not the default toolchain"
    )


def test_lint_env_declares_pip_audit() -> None:
    """pip-audit must stay declared in the lint feature's pypi-dependencies."""
    pypi_deps = _pixi().get("feature", {}).get("lint", {}).get("pypi-dependencies", {})
    assert "pip-audit" in pypi_deps, (
        "pixi.toml [feature.lint.pypi-dependencies] must declare pip-audit"
    )


def test_lint_python_constraint_matches_default() -> None:
    """Lint feature python pin must equal the default to avoid solve conflicts."""
    data = _pixi()
    default_python = data["dependencies"]["python"]
    lint_python = data["feature"]["lint"]["dependencies"]["python"]
    assert lint_python == default_python, (
        f"lint python constraint {lint_python!r} != default {default_python!r}; "
        "composition would risk a solve conflict"
    )
