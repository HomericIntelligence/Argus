#!/usr/bin/env bash
# Warn when GRAFANA_ADMIN_PASSWORD is unset or matches a known default.
#
# Called from `just start` / `just restart` so the user is told — loudly —
# whenever the Grafana stack would otherwise come up with the insecure
# fallback password baked into docker-compose.yml.
#
# Since issue #321 this script also HARD-FAILS (exit 1) when the new
# grafana-proxy Basic Auth credentials are unset or insecure defaults:
# the proxy fronts Grafana on 127.0.0.1:${GRAFANA_PORT:-3001} and must not
# come up with an empty/default shared credential.
#
# Issue: HomericIntelligence/Argus#182, HomericIntelligence/Argus#321

set -euo pipefail

# Source .env so credentials are visible even when the caller didn't export
# them (justfile uses `set dotenv-load`, but this script may also be invoked
# directly).
if [ -f .env ] && { [ -z "${GRAFANA_ADMIN_PASSWORD:-}" ] || [ -z "${GRAFANA_PROXY_PASSWORD:-}" ]; }; then
    # shellcheck disable=SC1091
    set -a; . ./.env; set +a
fi

PASSWORD="${GRAFANA_ADMIN_PASSWORD:-}"

# Known-insecure defaults that must never reach a deployed stack.
#   - empty    : docker-compose.yml falls back to `admin`
#   - admin    : the compose fallback itself
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

# Grafana auth-proxy Basic Auth (issue #321): fail closed. Unlike the admin
# password above, an insecure proxy credential blocks startup entirely — the
# proxy is the ONLY thing standing between callers and Grafana's login page.
PROXY_PASSWORD="${GRAFANA_PROXY_PASSWORD:-}"
PROXY_USER="${GRAFANA_PROXY_USER:-}"
case "$PROXY_USER" in
    "") echo "ERROR: GRAFANA_PROXY_USER is unset." >&2; exit 1 ;;
esac
case "$PROXY_PASSWORD" in
    ""|admin|changeme)
        echo "ERROR: GRAFANA_PROXY_PASSWORD is unset or set to a known insecure default ('admin', 'changeme')." >&2
        echo "Set GRAFANA_PROXY_PASSWORD to a strong, unique value in .env before starting the stack." >&2
        exit 1
        ;;
esac
