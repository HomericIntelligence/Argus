#!/usr/bin/env bash
# Bring up the full argus docker-compose stack, run the live-stack smoke suite
# (tests/smoke/test_live_stack.py) against it, then tear the stack down.
#
# Idempotent: creates any missing prereqs that `just start` normally assumes
# (see AGENTS.md operator notes). Teardown runs on success AND failure via
# EXIT trap. No `|| true` suppressions (repo convention).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

# Same backend selection as the justfile: podman-compose when present,
# else the docker compose plugin, else the legacy docker-compose binary.
if command -v podman-compose >/dev/null 2>&1; then
    COMPOSE=(podman-compose)
elif docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
else
    COMPOSE=(docker-compose)
fi

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
cleanup() {
    "${COMPOSE[@]}" down -v --remove-orphans
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
