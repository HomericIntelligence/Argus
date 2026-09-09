#!/usr/bin/env bash
# import-dashboards.sh — Import all Grafana dashboard JSON files via the HTTP API.
# Reads GF_ADMIN_PASSWORD from env (required; set GF_ADMIN_PASSWORD in .env).
set -euo pipefail

GRAFANA_PORT="${GRAFANA_PORT:-3000}"
GRAFANA_URL="http://localhost:${GRAFANA_PORT}"
GF_ADMIN_PASSWORD="${GF_ADMIN_PASSWORD:?ERROR: GF_ADMIN_PASSWORD is not set. Set GF_ADMIN_PASSWORD in .env}"
GRAFANA_AUTH="admin:${GF_ADMIN_PASSWORD}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARDS_DIR="${SCRIPT_DIR}/../dashboards"

if [[ ! -d "$DASHBOARDS_DIR" ]]; then
    echo "ERROR: dashboards directory not found at $DASHBOARDS_DIR" >&2
    exit 1
fi

# Use a per-run mktemp file instead of a predictable /tmp path (avoids a
# race condition and minor info-leak on shared machines). Cleaned up on exit.
resp_file=$(mktemp)
trap 'rm -f "$resp_file"' EXIT

shopt -s nullglob
files=("$DASHBOARDS_DIR"/*.json)

if [[ ${#files[@]} -eq 0 ]]; then
    echo "No dashboard JSON files found in $DASHBOARDS_DIR"
    exit 0
fi

for f in "${files[@]}"; do
    echo "Importing $(basename "$f") ..."
    payload=$(jq -n --slurpfile dash "$f" '{"dashboard": $dash[0], "overwrite": true, "folderId": 0}')
    http_code=$(curl -s --connect-timeout 5 -m 10 \
        -o "$resp_file" -w "%{http_code}" \
        -u "$GRAFANA_AUTH" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "${GRAFANA_URL}/api/dashboards/db")
    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
        echo "  -> ERROR: Grafana API returned HTTP $http_code for $(basename "$f") — check GF_ADMIN_PASSWORD and Grafana logs" >&2
        cat "$resp_file" >&2
        exit 1
    fi
    status=$(jq -r '.status // "unknown"' "$resp_file")
    echo "  -> status: $status"
done

echo "Dashboard import complete."
