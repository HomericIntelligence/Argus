#!/usr/bin/env bash
# import-dashboards.sh — Import all Grafana dashboard JSON files via the HTTP API.
#
# Since issue #321 Grafana has no host port: browsers reach it through the
# nginx basic-auth proxy (grafana-proxy) on 127.0.0.1:${GRAFANA_PORT:-3001}.
# A single request cannot carry two Basic Auth challenges (nginx consumes the
# Authorization header), so this script executes the API call INSIDE the
# grafana container instead, using busybox wget against Grafana's internal
# HTTPS endpoint.
#
# Reads GF_ADMIN_PASSWORD from env (required; set GF_ADMIN_PASSWORD in .env).
set -euo pipefail

GF_ADMIN_PASSWORD="${GF_ADMIN_PASSWORD:?ERROR: GF_ADMIN_PASSWORD is not set. Set GF_ADMIN_PASSWORD in .env}"

COMPOSE_CMD="docker compose"
if command -v podman-compose >/dev/null 2>&1; then
    COMPOSE_CMD="podman-compose"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARDS_DIR="${SCRIPT_DIR}/../dashboards"

if [[ ! -d "$DASHBOARDS_DIR" ]]; then
    echo "ERROR: dashboards directory not found at $DASHBOARDS_DIR" >&2
    exit 1
fi

# Per-run mktemp files instead of predictable /tmp paths (avoids a race
# condition and minor info-leak on shared machines). Cleaned up on exit.
resp_file=$(mktemp)
hdr_file=$(mktemp)
trap 'rm -f "$resp_file" "$hdr_file"' EXIT

shopt -s nullglob
files=("$DASHBOARDS_DIR"/*.json)

if [[ ${#files[@]} -eq 0 ]]; then
    echo "No dashboard JSON files found in $DASHBOARDS_DIR"
    exit 0
fi

for f in "${files[@]}"; do
    echo "Importing $(basename "$f") ..."
    payload=$(jq -n --slurpfile dash "$f" '{"dashboard": $dash[0], "overwrite": true, "folderId": 0}')
    set +e
    printf '%s' "$payload" | $COMPOSE_CMD exec -T grafana sh -c '
        wget --no-check-certificate -q -S -O - \
            --header "Content-Type: application/json" \
            --post-data "$(cat)" \
            --user admin --password "$1" \
            https://localhost:3000/api/dashboards/db
    ' sh "${GF_ADMIN_PASSWORD}" >"$resp_file" 2>"$hdr_file"
    wget_rc=$?
    set -e
    # BusyBox wget -S writes response headers to stderr; extract status code.
    http_code="$(sed -n 's/^[[:space:]]*HTTP\/[0-9.]* \([0-9][0-9][0-9]\).*/\1/p' "$hdr_file" | tail -n 1)"
    if [[ "$wget_rc" -ne 0 || -z "$http_code" ]]; then
        echo "  -> ERROR: could not reach Grafana API inside the grafana container" >&2
        cat "$hdr_file" >&2
        exit 1
    fi
    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
        echo "  -> ERROR: HTTP $http_code from Grafana API" >&2
        cat "$resp_file" >&2
        exit 1
    fi
    status=$(jq -r '.status // "unknown"' "$resp_file")
    echo "  -> status: $status"
done

echo "Dashboard import complete."
