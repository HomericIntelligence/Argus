#!/usr/bin/env bash
# Bring up the full argus docker-compose stack, run the live-stack smoke suite
# (tests/smoke/test_live_stack.py) against it, then tear the stack down.
#
# Idempotent: creates any missing prereqs that `just start` normally assumes
# (see AGENTS.md operator notes). Teardown runs on success AND failure via
# EXIT trap. No `|| true` suppressions (repo convention): diagnose() and
# cleanup() disable errexit and print each command's exit status instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

# Same backend selection as the justfile: podman-compose when present,
# else the docker compose plugin, else the legacy docker-compose binary.
if command -v podman-compose >/dev/null 2>&1; then
    COMPOSE=(podman-compose)
    BUILDER=(podman build)
elif docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
    BUILDER=(docker build)
else
    COMPOSE=(docker-compose)
    BUILDER=(docker build)
fi

# The argus-exporter service resolves to the PUBLISHED image
# (ghcr.io/.../argus-exporter:v0.1.0) and exporter.py is COPYed into that
# image at build time, not bind-mounted. Without this override the smoke job
# scrapes the released exporter and never exercises the working tree: the
# released build's collect() takes 10.0 s against unreachable upstreams,
# which is exactly Prometheus' scrape_timeout, so up == 0 and the job fails
# on a fix that is already in the source. Build the tree's exporter/ and
# point EXPORTER_IMAGE at it so the gate covers the code under review.
SMOKE_EXPORTER_IMAGE="argus-exporter:smoke"
"${BUILDER[@]}" -t "$SMOKE_EXPORTER_IMAGE" "$REPO_ROOT/exporter"
export EXPORTER_IMAGE="$SMOKE_EXPORTER_IMAGE"

# ── Prereqs ────────────────────────────────────────────────────────────────────
[ -f .env ] || cp .env.example .env
bash certs/gen-certs.sh
bash scripts/gen-htpasswd.sh

# Promtail bind-mounts these; missing paths become stray root-owned dirs/files.
export NATS_LOG_DIR="${NATS_LOG_DIR:-/tmp/argus-smoke-nats-logs}"
mkdir -p "$NATS_LOG_DIR"
touch /tmp/hermes.log
export HOSTNAME="${HOSTNAME:-argus-smoke-runner}"

# Atlas refuses to start in bearer mode with an empty token; the smoke stack is
# loopback-only, so mint a throwaway token unless one is already configured.
export ATLAS_AUTH_BEARER_TOKEN="${ATLAS_AUTH_BEARER_TOKEN:-$(openssl rand -hex 32)}"

# ── Bring up + verify + tear down ─────────────────────────────────────────────
# On failure, dump the state that explains it BEFORE tearing the stack down.
# Without this the job only reports `up == 0` and no reader can tell a slow
# scrape from an unreachable target or a crash-looping container.
# Dump prometheus target health (each failing job carries its lastError).
_dump_targets() {
    local out
    out="$(mktemp)"
    curl -sk -o "$out" https://127.0.0.1:9090/api/v1/targets
    python - "$out" <<'PY'
import json
import sys

try:
    with open(sys.argv[1]) as handle:
        targets = json.load(handle)["data"]["activeTargets"]
except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the failure
    print(f"(could not read /api/v1/targets: {exc})")
else:
    for target in targets:
        job = target["labels"].get("job", "?")
        last = target.get("lastError") or "-"
        print(f"  {job}: health={target['health']} lastError={last}")
PY
    rm -f "$out"
}

# Run one diagnostic and print its exit status. The caller disables errexit
# so a failed probe cannot replace the real smoke failure, and the status
# is printed rather than swallowed -- that is what the repo's
# forbid-suppressions gate guards against (no `|| true`).
run_diag() {
    local label="$1"
    shift
    echo "--- ${label} ---" >&2
    "$@"
    echo "    [exit $?]" >&2
    return 0
}

# On failure, dump the state that explains it BEFORE tearing the stack down.
# Without this the job only reports `up == 0`, and no reader can tell a
# slow scrape from an unreachable target or a crash-looping container.
diagnose() {
    local status="$1"
    if (( status == 0 )); then
        return 0
    fi
    set +e
    echo "" >&2
    echo "::group::smoke failure diagnostics" >&2
    run_diag "compose ps" "${COMPOSE[@]}" ps
    run_diag "prometheus targets (health / lastError)" _dump_targets
    for svc in argus-exporter argus-prometheus argus-alertmanager; do
        run_diag "${svc} logs (tail 40)" "${COMPOSE[@]}" logs --tail 40 "$svc"
    done
    echo "::endgroup::" >&2
    set -e
    return 0
}

cleanup() {
    local status=$?
    diagnose "$status"
    set +e
    "${COMPOSE[@]}" down -v --remove-orphans
    echo "    [compose down exit $?]" >&2
    exit "$status"
}
trap cleanup EXIT

# docker compose v2 can block on healthchecks via --wait; older backends
# (podman-compose, legacy docker-compose) lack the flag, so fall back to
# polling the service health endpoints from the host.
if "${COMPOSE[@]}" up --help 2>&1 | grep -q -- "--wait"; then
    "${COMPOSE[@]}" up -d --wait --wait-timeout 300 --build
else
    "${COMPOSE[@]}" up -d --build
    wait_for_stack() {
        local deadline=$((SECONDS + 300))
        while (( SECONDS < deadline )); do
            if curl -skf https://127.0.0.1:9090/-/ready >/dev/null 2>&1 \
                && curl -sf http://127.0.0.1:9100/health >/dev/null 2>&1 \
                && curl -sf http://127.0.0.1:9093/-/healthy >/dev/null 2>&1 \
                && curl -sf http://127.0.0.1:3002/livez >/dev/null 2>&1; then
                return 0
            fi
            sleep 5
        done
        echo "ERROR: stack did not become ready within 300s" >&2
        return 1
    }
    wait_for_stack
fi

export ARGUS_SMOKE_STACK=1
python -m pytest tests/smoke --override-ini="addopts=" -v -m live_stack
