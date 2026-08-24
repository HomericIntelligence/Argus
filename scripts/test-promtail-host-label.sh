#!/usr/bin/env bash
# test-promtail-host-label.sh — Smoke-test that Promtail expands
# ${PROMTAIL_HOST_LABEL:-${HOSTNAME}} in-process at load time.
#
# Promtail does not expose its rendered config over HTTP, so verification
# is done end-to-end: after (re)creating promtail, the script polls Loki's
# label-values API (GET /loki/api/v1/label/host/values) through the
# basic-auth-protected loki-proxy service (reached from inside the promtail
# container over the `argus` network) until the expected `host` label value
# appears among emitted streams.
#
# Loki credentials are read from LOKI_AUTH_USER / LOKI_AUTH_PASSWORD,
# falling back to a silent parse of the repo-root .env.
#
# Usage: COMPOSE_CMD="docker compose" ./scripts/test-promtail-host-label.sh

set -euo pipefail

COMPOSE="${COMPOSE_CMD:-docker compose}"
OVERRIDE_LABEL="argus-smoke-test"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC}  $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"

# bash sets HOSTNAME but does not export it; compose interpolation of
# `HOSTNAME: ${HOSTNAME}` only sees exported vars, so export explicitly.
export HOSTNAME="${HOSTNAME:-$(hostname)}"

load_loki_auth() {
    if [[ -z "${LOKI_AUTH_USER:-}" && -r "${REPO_ROOT}/.env" ]]; then
        # shellcheck disable=SC1091
        LOKI_AUTH_USER="$(sed -n 's/^LOKI_AUTH_USER=//p' "${REPO_ROOT}/.env" | head -n1 | tr -d '\r')"
    fi
    if [[ -z "${LOKI_AUTH_PASSWORD:-}" && -r "${REPO_ROOT}/.env" ]]; then
        # shellcheck disable=SC1091
        LOKI_AUTH_PASSWORD="$(sed -n 's/^LOKI_AUTH_PASSWORD=//p' "${REPO_ROOT}/.env" | head -n1 | tr -d '\r')"
    fi
    : "${LOKI_AUTH_USER:?LOKI_AUTH_USER must be set in environment or .env}"
    : "${LOKI_AUTH_PASSWORD:?LOKI_AUTH_PASSWORD must be set in environment or .env}"
}

host_label_values() {
    local auth values=""
    auth="$(printf '%s:%s' "${LOKI_AUTH_USER}" "${LOKI_AUTH_PASSWORD}" | base64 | tr -d '\n')"
    # Transient exec/HTTP failures are tolerated here on purpose:
    # assert_host_label polls repeatedly and fails definitively if the
    # expected label value never shows up.
    if ! values="$(${COMPOSE} exec -T promtail wget -qO- \
            --header "Authorization: Basic ${auth}" \
            "http://loki-proxy/loki/api/v1/label/host/values" 2>/dev/null)"; then
        values=""
    fi
    printf '%s' "${values}"
}

wait_ready() {
    for _ in $(seq 1 30); do
        if ${COMPOSE} exec -T promtail wget -qO- http://localhost:9080/ready >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    fail "promtail did not become ready within 60s"
}

assert_host_label() {
    local expected="$1" values
    for _ in $(seq 1 30); do
        values="$(host_label_values)"
        if echo "${values}" | grep -Fq "\"${expected}\""; then
            ok "Loki reports host label '${expected}'"
            return 0
        fi
        sleep 2
    done
    info "host label values observed after 60s: ${values:-<none>}"
    if echo "${values}" | grep -Fq '"PROMTAIL_HOST_LABEL"'; then
        fail "unexpanded \${PROMTAIL_HOST_LABEL} placeholder observed as host label value"
    fi
    fail "expected host label '${expected}' never appeared in Loki within 60s"
}

restore_promtail() {
    info "Restoring promtail container without override..."
    if ! ${COMPOSE} up -d --force-recreate promtail >/dev/null 2>&1; then
        info "Could not restore promtail (stack may be down); continuing."
    fi
}

load_loki_auth

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
