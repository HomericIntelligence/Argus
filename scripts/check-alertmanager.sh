#!/usr/bin/env bash
# check-alertmanager.sh — Health-gate for Alertmanager (issue #250).
#
# Skips (exit 0) when Alertmanager is not part of this deployment; fails
# (exit 1) when its container is not running or /-/healthy does not return
# a successful response. Set ALERTMANAGER_CHECK_SKIP_ON_DOWN=1 to skip
# (exit 0) instead of failing while the stack is down.
# Usage: ./scripts/check-alertmanager.sh
#   COMPOSE_FILE                     compose file inspected for the service (default: docker-compose.yml)
#   ALERTMANAGER_URL                 base URL probed for health             (default: http://localhost:9093)
#   ALERTMANAGER_CONTAINER           container name looked up in ps output  (default: argus-alertmanager)
#   ALERTMANAGER_CHECK_SKIP_ON_DOWN  1 = skip instead of failing when down  (default: unset = fail)
#   CONTAINER_CMD                    container runtime binary               (default: podman if podman-compose
#                                                                           is on PATH, else docker)

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
ALERTMANAGER_URL="${ALERTMANAGER_URL:-http://localhost:9093}"
ALERTMANAGER_CONTAINER="${ALERTMANAGER_CONTAINER:-argus-alertmanager}"

if [[ -z "${CONTAINER_CMD:-}" ]]; then
    if command -v podman-compose >/dev/null 2>&1; then
        CONTAINER_CMD="podman"
    else
        CONTAINER_CMD="docker"
    fi
fi

if ! grep -qE '^[[:space:]]+alertmanager:' "$COMPOSE_FILE"; then
    echo "SKIP: no alertmanager service in ${COMPOSE_FILE} — skipping health check."
    exit 0
fi

if ! "$CONTAINER_CMD" ps --filter "name=${ALERTMANAGER_CONTAINER}" \
        --format '{{.Names}}' 2>/dev/null | grep -qx "${ALERTMANAGER_CONTAINER}"; then
    if [[ "${ALERTMANAGER_CHECK_SKIP_ON_DOWN:-0}" == "1" ]]; then
        echo "SKIP: Alertmanager container '${ALERTMANAGER_CONTAINER}' is not running (ALERTMANAGER_CHECK_SKIP_ON_DOWN=1)."
        exit 0
    fi
    echo "FAIL: Alertmanager container '${ALERTMANAGER_CONTAINER}' is not running — run 'just start', or set ALERTMANAGER_CHECK_SKIP_ON_DOWN=1 to opt out." >&2
    exit 1
fi

if curl -sf --max-time 5 "${ALERTMANAGER_URL}/-/healthy" > /dev/null; then
    echo "OK: Alertmanager is healthy (${ALERTMANAGER_URL}/-/healthy)."
else
    echo "FAIL: Alertmanager container is running but ${ALERTMANAGER_URL}/-/healthy did not return a healthy response." >&2
    exit 1
fi
