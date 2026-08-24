"""Ensure pixi.toml [tasks] and justfile recipes stay in sync.

Two guards live here:

1. ``test_pixi_tasks_have_just_recipes`` — every pixi task key has a
   matching justfile recipe *name*.
2. ``test_pixi_just_commands_consistent`` — every shared task's
   *command* stays consistent between the two files, catching silent
   semantic drift (different flags, different working directory) that
   the name-only check misses (issue #414).
"""

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def _pixi_tasks() -> dict[str, str]:
    """Return top-level pixi task names mapped to their effective command."""
    with (_ROOT / "pixi.toml").open("rb") as f:
        data = tomllib.load(f)
    tasks: dict[str, str] = {}
    for name, value in data.get("tasks", {}).items():
        if isinstance(value, str):
            tasks[name] = value
        elif isinstance(value, list):
            parts = [part for part in value if isinstance(part, str)]
            tasks[name] = " && ".join(parts)
        elif isinstance(value, dict) and isinstance(value.get("cmd"), str):
            tasks[name] = value["cmd"]
        else:
            msg = f"Unsupported pixi task definition for {name!r}: {value!r}"
            raise AssertionError(msg)
    return tasks


def _justfile_recipes() -> set[str]:
    """Return the set of recipe names declared in the justfile."""
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


def _justfile_recipe_body(name: str) -> list[str]:
    """Return the stripped, non-comment body lines of one justfile recipe.

    Stops at the next non-indented, non-blank line (the next recipe
    header). Blank lines and comment-only lines inside the body are
    skipped; a leading ``@`` quiet-marker is stripped from each line.
    """
    header = re.compile(rf"^{re.escape(name)}(?:\s[^:]*)?:")
    body: list[str] = []
    in_body = False
    for line in (_ROOT / "justfile").read_text().splitlines():
        if not in_body:
            if header.match(line):
                in_body = True
            continue
        if not line.strip():
            continue
        if not line[0].isspace():
            break
        text = line.strip().lstrip("@").strip()
        if not text or text.startswith("#"):
            continue
        body.append(text)
    return body


def test_pixi_tasks_have_just_recipes() -> None:
    """Every pixi task must correspond to a just recipe to prevent runner drift."""
    pixi_task_keys = list(_pixi_tasks())
    just_recipes = _justfile_recipes()
    missing = [t for t in pixi_task_keys if t not in just_recipes]
    assert not missing, (
        f"pixi tasks missing from justfile: {missing}. "
        "Add matching recipes or update pixi.toml."
    )


def test_pixi_just_commands_consistent() -> None:
    """Every shared task's command must stay consistent across pixi and just.

    Two wrapping directions are recognized:

    * pixi-side wrapper: ``pixi.toml`` says ``just <name>`` — pixi
      delegates to the justfile recipe, which is the source of truth.
      Drift means pixi stopped delegating (e.g. ``docker compose up``
      appearing directly in pixi.toml).
    * just-side wrapper: the justfile recipe body is a single
      ``pixi run <name>`` line — just delegates to the pixi task, which
      is the source of truth. Drift means the recipe body gained flags
      or diverged from the pixi command.

    Anything else fails loudly naming the offending task.
    """
    pixi_tasks = _pixi_tasks()
    just_recipes = _justfile_recipes()
    failures: list[str] = []
    for name, pixi_cmd in sorted(pixi_tasks.items()):
        if name not in just_recipes:
            # Name-level mismatch is covered by test_pixi_tasks_have_just_recipes.
            continue
        if pixi_cmd.strip() == f"just {name}":
            # pixi delegates to just; the recipe body is just's business.
            continue
        body = _justfile_recipe_body(name)
        expected = f"pixi run {name}"
        if len(body) != 1 or body[0] != expected:
            failures.append(
                f"{name}: pixi.toml runs {pixi_cmd!r} but the justfile "
                f"recipe body is {body!r}; expected a single {expected!r} line"
            )
    assert not failures, (
        "pixi/just command drift detected:\n  " + "\n  ".join(failures)
    )
