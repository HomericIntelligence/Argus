#!/usr/bin/env bash
# Generates secrets/htpasswd from LOKI_AUTH_USER and LOKI_AUTH_PASSWORD.
# Reads from .env if present; both vars must be set.
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

if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: openssl not found on PATH — required to hash the Loki password." >&2
    exit 1
fi

SECRETS_DIR="${REPO_ROOT}/secrets"
HTPASSWD_FILE="${SECRETS_DIR}/htpasswd"
mkdir -p "${SECRETS_DIR}"

# A failing openssl aborts here (set -e) before anything is written, so a
# failed generation cannot disturb an existing credential.
HASH="$(openssl passwd -apr1 "${LOKI_AUTH_PASSWORD}")"

# Atomic write (issue #341): generate into a temp file in the same directory,
# verify it fully, and only then mv it into place. A garbage/empty hash can
# never truncate or replace a previously valid secrets/htpasswd.
HTPASSWD_TMP="${HTPASSWD_FILE}.tmp"
cleanup() { rm -f "${HTPASSWD_TMP}"; }
trap cleanup EXIT

printf '%s:%s\n' "${LOKI_AUTH_USER}" "${HASH}" > "${HTPASSWD_TMP}"
chmod 600 "${HTPASSWD_TMP}"

# Post-write verification (issue #341): temp file must be non-empty, and match
# the expected "<user>:$apr1$..." format. Glob match (not grep regex) so
# metacharacters in LOKI_AUTH_USER cannot skew the check.
if [ ! -s "${HTPASSWD_TMP}" ]; then
    echo "ERROR: ${HTPASSWD_TMP} is missing or empty after generation." >&2
    exit 1
fi
first_line="$(head -n 1 "${HTPASSWD_TMP}")"
if [[ "${first_line}" != "${LOKI_AUTH_USER}:\$apr1\$"* ]]; then
    echo "ERROR: generated htpasswd has unexpected format — expected '${LOKI_AUTH_USER}:\$apr1\$<hash>'." >&2
    exit 1
fi

mv "${HTPASSWD_TMP}" "${HTPASSWD_FILE}"

echo "Generated ${HTPASSWD_FILE} for user '${LOKI_AUTH_USER}'"
