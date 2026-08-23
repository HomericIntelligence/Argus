#!/usr/bin/env bash
# test-promtail-host-label.sh — Smoke-test that Promtail expands
# ${PROMTAIL_HOST_LABEL:-${HOSTNAME}} in its rendered config at runtime.
#
# Reads the rendered (post-expansion) config from Promtail's /config
# endpoint via `compose exec` — the mounted file on disk never changes;
# expansion happens in-process at load time.
#
# Usage: COMPOSE_CMD="docker compose" ./scripts/test-promtail-host-label.sh

set -euo pipefail

COMPOSE="${COMPOSE_CMD:-docker compose}"
OVERRIDE_LABEL="argus-smoke-test"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC}  $*"; }

# bash sets HOSTNAME but does not export it; compose interpolation of
# `HOSTNAME: ${HOSTNAME}` only sees exported vars, so export explicitly.
export HOSTNAME="${HOSTNAME:-$(hostname)}"

wait_ready() {
    for _ in $(seq 1 30); do
        if ${COMPOSE} exec -T promtail wget -qO- http://localhost:9080/ready >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    fail "promtail did not become ready within 60s"
}

rendered_config() {
    ${COMPOSE} exec -T promtail wget -qO- http://localhost:9080/config
}

assert_host_label() {
    local expected="$1" rendered
    rendered="$(rendered_config)"
    if echo "${rendered}" | grep -Eq "^[[:space:]]*host:[[:space:]]*${expected}[[:space:]]*$"; then
        ok "rendered host label matches '${expected}'"
    else
        echo "${rendered}" | grep -E "^[[:space:]]*host:" || true
        fail "rendered host label does not match '${expected}'"
    fi
    if echo "${rendered}" | grep -q "PROMTAIL_HOST_LABEL"; then
        fail "unexpanded \${PROMTAIL_HOST_LABEL} placeholder found in rendered config"
    fi
}

restore_promtail() {
    info "Restoring promtail container without override..."
    ${COMPOSE} up -d --force-recreate promtail >/dev/null 2>&1 || true
}

info "Phase 1: HOSTNAME fallback (expect host=${HOSTNAME})"
${COMPOSE} up -d promtail
wait_ready
assert_host_label "${HOSTNAME}"

info "Phase 2: PROMTAIL_HOST_LABEL override (expect host=${OVERRIDE_LABEL})"
trap restore_promtail EXIT
PROMTAIL_HOST_LABEL="${OVERRIDE_LABEL}" ${COMPOSE} up -d --force-recreate promtail
wait_ready
assert_host_label "${OVERRIDE_LABEL}"

ok "promtail host-label smoke test passed"
