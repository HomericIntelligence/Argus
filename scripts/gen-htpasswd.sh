#!/usr/bin/env bash
# Generates secrets/htpasswd from LOKI_AUTH_USER and LOKI_AUTH_PASSWORD and
# secrets/htpasswd-grafana from GRAFANA_PROXY_USER and GRAFANA_PROXY_PASSWORD.
# Reads from .env if present; all vars must be set.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Source .env if it exists and vars are not already set
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${REPO_ROOT}/.env"
    set +a
fi

: "${LOKI_AUTH_USER:?LOKI_AUTH_USER must be set in .env or environment}"
: "${LOKI_AUTH_PASSWORD:?LOKI_AUTH_PASSWORD must be set in .env or environment}"
: "${GRAFANA_PROXY_USER:?GRAFANA_PROXY_USER must be set in .env or environment}"
: "${GRAFANA_PROXY_PASSWORD:?GRAFANA_PROXY_PASSWORD must be set in .env or environment}"

SECRETS_DIR="${REPO_ROOT}/secrets"
mkdir -p "${SECRETS_DIR}"

HASH="$(openssl passwd -apr1 "${LOKI_AUTH_PASSWORD}")"
printf '%s:%s\n' "${LOKI_AUTH_USER}" "${HASH}" > "${SECRETS_DIR}/htpasswd"
chmod 600 "${SECRETS_DIR}/htpasswd"

echo "Generated ${SECRETS_DIR}/htpasswd for user '${LOKI_AUTH_USER}'"

GRAFANA_HASH="$(openssl passwd -apr1 "${GRAFANA_PROXY_PASSWORD}")"
printf '%s:%s\n' "${GRAFANA_PROXY_USER}" "${GRAFANA_HASH}" > "${SECRETS_DIR}/htpasswd-grafana"
chmod 600 "${SECRETS_DIR}/htpasswd-grafana"

echo "Generated ${SECRETS_DIR}/htpasswd-grafana for user '${GRAFANA_PROXY_USER}'"
