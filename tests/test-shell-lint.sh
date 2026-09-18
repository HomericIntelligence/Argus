#!/bin/bash
# Shell-script lint gate: bash -n syntax check + shellcheck over every tracked
# .sh file (issue #359).
#
# This script is the single source of truth for the shell lint gate. It is
# invoked by:
#   - .github/workflows/ci.yml  ("Lint shell scripts" step)
#   - scripts/run_ci_local.sh   (run_lint, inside the argus-ci container)
#   - justfile                  (`just check-shell`, via pixi run)
#
# Two cases:
#   positive — every tracked .sh parses (bash -n) and passes
#              `shellcheck --severity=warning` (same severity as CI).
#   negative — a known-broken fixture (unquoted variable + the SC2064-style
#              trap bug from #134) MUST be rejected; if shellcheck passes it,
#              the gate is vacuous and this test fails.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

failures=0

if ! command -v shellcheck > /dev/null 2>&1; then
    echo "::error::shellcheck not found on PATH (pixi run bash tests/test-shell-lint.sh to use the project env)"
    exit 1
fi

echo "== Positive case: bash -n + shellcheck --severity=warning over tracked *.sh =="
# Enumerate from the index, never globs — a glob false-passes when a directory
# is renamed. Fail loudly if enumeration itself fails (vacuous-gate guard).
if ! sh_files_raw="$(git ls-files -- '*.sh' 2>/dev/null)"; then
    # A linked worktree mounted without usable git metadata (e.g. the
    # containerized ci-lint job) cannot enumerate from the index; fall back
    # to a filesystem walk — explicitly announced, never silent.
    echo "::warning::git ls-files unavailable here; enumerating *.sh via find (filesystem superset)"
    sh_files_raw="$(find . \( -name .git -o -name .pixi \) -prune -o -type f -name '*.sh' -print | sed 's|^\./||')"
fi
sh_files=()
if [ -n "${sh_files_raw}" ]; then
    mapfile -t sh_files <<< "${sh_files_raw}"
fi
if [ "${#sh_files[@]}" -eq 0 ]; then
    echo "::notice::No tracked .sh files — nothing to lint"
else
    for f in "${sh_files[@]}"; do
        if ! bash -n "${f}"; then
            echo "SYNTAX FAIL: ${f}"
            failures=$((failures + 1))
        fi
    done
    if ! shellcheck --severity=warning "${sh_files[@]}"; then
        echo "FAIL: shellcheck reported violations above"
        failures=$((failures + 1))
    fi
fi

echo "== Negative case: known-broken fixture must be rejected =="
tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT
cat > "${tmpdir}/broken.sh" <<'FIXTURE'
#!/bin/bash
trap "cleanup $?" EXIT
echo $undefined_var
FIXTURE
if shellcheck --severity=warning "${tmpdir}/broken.sh" > /dev/null 2>&1; then
    echo "FAIL: shellcheck accepted a known-broken fixture (SC2064/SC2086) — gate is vacuous"
    failures=$((failures + 1))
else
    echo "OK: shellcheck rejected the broken fixture"
fi

if [ "${failures}" -ne 0 ]; then
    echo "shell lint gate FAILED with ${failures} failure(s)"
    exit 1
fi
echo "OK: all tracked .sh files parse and pass shellcheck"
