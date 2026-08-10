"""Ensure every pixi.toml [tasks] key has a matching recipe in justfile."""

from pathlib import Path

import tomllib

_ROOT = Path(__file__).parent.parent


def _pixi_task_keys() -> list[str]:
    with (_ROOT / "pixi.toml").open("rb") as f:
        data = tomllib.load(f)
    return list(data.get("tasks", {}).keys())


def _justfile_recipes() -> set[str]:
    text = (_ROOT / "justfile").read_text()
    recipes: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("@"):
            name = stripped.split()[0].rstrip(":")
            if ":" not in stripped.split()[0] and stripped.split()[0].endswith(":"):
                recipes.add(name)
            elif line and not line[0].isspace() and ":" in stripped:
                candidate = stripped.split(":")[0].strip()
                if candidate and " " not in candidate:
                    recipes.add(candidate)
    return recipes


def test_pixi_tasks_have_just_recipes() -> None:
    """Every pixi task must correspond to a just recipe to prevent runner drift."""
    pixi_tasks = _pixi_task_keys()
    just_recipes = _justfile_recipes()
    missing = [t for t in pixi_tasks if t not in just_recipes]
    assert not missing, (
        f"pixi tasks missing from justfile: {missing}. "
        "Add matching recipes or update pixi.toml."
    )
