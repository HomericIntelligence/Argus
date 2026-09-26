#!/usr/bin/env bash
# scrape-agamemnon.sh — Manually test Agamemnon and Nestor health endpoints.
# Usage: ./scripts/scrape-agamemnon.sh [AGAMEMNON_URL] [NESTOR_URL]

set -euo pipefail

# The Prometheus probe verifies the Argus CA, so it needs a path that does not
# depend on the caller's working directory. Without this, running the script
# from anywhere but the repo root makes curl fail on a missing --cacert file
# and the operator sees "run 'just start' first" instead of the real cause.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

AGAMEMNON_URL="${1:-http://172.20.0.1:8080}"
NESTOR_URL="${2:-http://172.20.0.1:8081}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; }
info() { echo -e "${YELLOW}[INFO]${NC}  $*"; }

echo ""
info "Scraping Agamemnon at ${AGAMEMNON_URL}"
echo "-------------------------------------------"

HTTP=$(curl -s -o /tmp/argus_agamemnon.json -w "%{http_code}" --connect-timeout 5 "${AGAMEMNON_URL}/v1/health" || echo "000")
if [[ "$HTTP" == "200" ]]; then
    ok "HTTP ${HTTP} — /v1/health reachable"
    command -v jq &>/dev/null && jq '.' /tmp/argus_agamemnon.json || cat /tmp/argus_agamemnon.json
else
    fail "HTTP ${HTTP} — /v1/health not reachable"
fi

echo ""
HTTP=$(curl -s -o /tmp/argus_agents.json -w "%{http_code}" --connect-timeout 5 "${AGAMEMNON_URL}/v1/agents" || echo "000")
if [[ "$HTTP" == "200" ]]; then
    ok "HTTP ${HTTP} — /v1/agents reachable"
    TOTAL=$(command -v jq &>/dev/null && jq '.agents | length' /tmp/argus_agents.json 2>/dev/null || echo "?")
    echo "  Agent count: ${TOTAL}"
else
    fail "HTTP ${HTTP} — /v1/agents not reachable"
fi

echo ""
info "Scraping Nestor at ${NESTOR_URL}"
echo "-------------------------------------------"

HTTP=$(curl -s -o /tmp/argus_nestor.json -w "%{http_code}" --connect-timeout 5 "${NESTOR_URL}/v1/health" || echo "000")
if [[ "$HTTP" == "200" ]]; then
    ok "HTTP ${HTTP} — Nestor /v1/health reachable"
else
    fail "HTTP ${HTTP} — Nestor /v1/health not reachable"
fi

echo ""
info "Checking Prometheus (https://localhost:9090)"
# Prometheus serves HTTPS with the Argus CA. Verify it instead of bypassing
# certificate validation so the probe detects an expired or mis-signed cert.
PROM_UP=$(curl --fail --silent --show-error --cacert "$REPO_ROOT/certs/ca.crt" --connect-timeout 3 "https://localhost:9090/api/v1/query?query=up" 2>/dev/null || echo "")
if [[ -n "$PROM_UP" ]]; then
    ok "Prometheus reachable"
    command -v jq &>/dev/null && echo "$PROM_UP" | jq -r '.data.result[] | "  job=\(.metric.job) up=\(.value[1])"' 2>/dev/null
else
    fail "Prometheus not reachable — run 'just start' first"
fi

echo ""
info "Done."
