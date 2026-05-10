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

shopt -s nullglob
files=("$DASHBOARDS_DIR"/*.json)

if [[ ${#files[@]} -eq 0 ]]; then
    echo "No dashboard JSON files found in $DASHBOARDS_DIR"
    exit 0
fi

for f in "${files[@]}"; do
    echo "Importing $(basename "$f") ..."
    payload=$(jq -n --slurpfile dash "$f" '{"dashboard": $dash[0], "overwrite": true, "folderId": 0}')
    http_code=$(curl -s -o /tmp/grafana_import_resp.json -w "%{http_code}" \
        -u "$GRAFANA_AUTH" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "${GRAFANA_URL}/api/dashboards/db")
    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
        echo "  -> ERROR: HTTP $http_code from Grafana API" >&2
        cat /tmp/grafana_import_resp.json >&2
        exit 1
    fi
    status=$(jq -r '.status // "unknown"' /tmp/grafana_import_resp.json)
    echo "  -> status: $status"
done

echo "Dashboard import complete."
