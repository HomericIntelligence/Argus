#!/usr/bin/env bash
# Run `templ generate --watch` and `air` in parallel from dashboard/.
# Tees both children's output with a [templ]/[air] prefix so the
# contributor can tell which pipeline yelled.
#
# Ctrl-C (or any signal) tears down the whole process group: the trap
# below sends INT/TERM to every child via `kill 0`, and the `wait`
# returns. Used by the `just dev` recipe.
#
# Pre-flight (PATH for air/templ, .env existence) is done in the
# justfile recipe, not here, so this script can be linted in isolation
# without those side effects.

set -euo pipefail

# Resolve repo root (this script lives in <repo>/scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DASHBOARD_DIR="${REPO_ROOT}/dashboard"

if [[ ! -d "${DASHBOARD_DIR}" ]]; then
  echo "dev-watch: dashboard/ not found at ${DASHBOARD_DIR}" >&2
  exit 1
fi

cd "${DASHBOARD_DIR}"

# Make sure tmp/ exists for air's build artifact.
mkdir -p tmp

echo "dev-watch: starting templ + air from ${DASHBOARD_DIR}"
echo "dev-watch: Ctrl-C tears both watchers down."

# Tear-down trap. `kill 0` signals the whole process group, including
# any descendants the watchers spawned (go build, the rebuilt binary,
# etc.). Using INT first gives them a chance to flush; the EXIT branch
# is a belt-and-braces fallback.
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "dev-watch: stopping watchers..."
  kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Run each watcher in its own subshell so we capture stderr alongside
# stdout and can prefix every line. `stdbuf -oL` keeps the output
# line-buffered through the pipe so prefixes appear in real time.
( stdbuf -oL templ generate --watch 2>&1 | sed -u 's/^/[templ] /' ) &
TEMPL_PID=$!

( stdbuf -oL air -c .air.toml 2>&1 | sed -u 's/^/[air]   /' ) &
AIR_PID=$!

# Block on either child. Whichever exits first triggers the trap and
# brings the other down with it.
wait -n "${TEMPL_PID}" "${AIR_PID}"
