#!/usr/bin/env bash
# Warn when GRAFANA_ADMIN_PASSWORD is unset or matches a known default.
#
# Called from `just start` / `just restart` so the user is told — loudly —
# whenever the Grafana stack would otherwise come up with the insecure
# fallback password baked into docker-compose.yml.
#
# Issue: HomericIntelligence/Argus#182

set -euo pipefail

# Source .env so GRAFANA_ADMIN_PASSWORD is visible even when the caller
# didn't export it (justfile uses `set dotenv-load`, but this script may
# also be invoked directly).
if [ -f .env ] && [ -z "${GRAFANA_ADMIN_PASSWORD:-}" ]; then
    # shellcheck disable=SC1091
    set -a; . ./.env; set +a
fi

PASSWORD="${GRAFANA_ADMIN_PASSWORD:-}"

# Known-insecure defaults that must never reach a deployed stack.
#   - empty    : docker-compose.yml refuses to start (`:?` on GRAFANA_ADMIN_PASSWORD)
#   - admin    : the historical compose fallback, still a weak value
#   - changeme : the placeholder shipped in .env.example
case "$PASSWORD" in
    ""|admin|changeme)
        cat >&2 <<'WARN'
================================================================================
WARNING: GRAFANA_ADMIN_PASSWORD is unset or set to a known default value.

Grafana will start with an insecure admin password (default fallback: 'admin').
Set GRAFANA_ADMIN_PASSWORD to a strong, unique value in .env before exposing
this stack to any network. See .env.example for the canonical variable name.
================================================================================
WARN
        ;;
esac
