#!/usr/bin/env bash
# check-env-example.sh — fail if docker-compose.yml references env vars that
# are not documented in .env.example.
#
# Background: docker-compose.yml uses ${VAR} and ${VAR:-default} interpolation.
# Without enforcement, new variables silently drift away from .env.example
# (the operator-facing canonical list). This script extracts every variable
# referenced from the compose file and verifies each one has a corresponding
# entry in .env.example (commented or uncommented).
#
# Follow-up to HomericIntelligence/Argus#53; closes #215.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "ERROR: $COMPOSE_FILE not found" >&2
    exit 2
fi
if [[ ! -f "$ENV_EXAMPLE" ]]; then
    echo "ERROR: $ENV_EXAMPLE not found" >&2
    exit 2
fi

# System variables that compose interpolates from the calling shell and are
# not expected to be documented in .env.example (Docker resolves them from
# the host environment, not from .env). Extend as needed.
SYSTEM_ALLOWLIST=(
    HOSTNAME
    HOME
    USER
    PATH
    PWD
    UID
    GID
)

is_allowlisted() {
    local var="$1"
    local allowed
    for allowed in "${SYSTEM_ALLOWLIST[@]}"; do
        [[ "$var" == "$allowed" ]] && return 0
    done
    return 1
}

# Extract every ${VAR...} reference from docker-compose.yml. The regex
# tolerates both ${VAR} and ${VAR:-default} forms. Each captured name is
# emitted once, sorted.
mapfile -t compose_vars < <(
    grep -oE '\$\{[A-Z_][A-Z0-9_]*' "$COMPOSE_FILE" \
        | sed 's/^\${//' \
        | sort -u
)

# Extract every KEY= entry (commented or not) from .env.example. A line of
# the form "# FOO=..." counts as documented because operators rely on the
# inline comment to discover optional knobs.
mapfile -t env_keys < <(
    grep -oE '^[[:space:]]*#?[[:space:]]*[A-Z_][A-Z0-9_]*=' "$ENV_EXAMPLE" \
        | sed -E 's/^[[:space:]]*#?[[:space:]]*//; s/=$//' \
        | sort -u
)

# Build an associative lookup for fast membership checks.
declare -A env_seen=()
for k in "${env_keys[@]}"; do
    env_seen["$k"]=1
done

missing=()
for var in "${compose_vars[@]}"; do
    if is_allowlisted "$var"; then
        continue
    fi
    if [[ -z "${env_seen[$var]:-}" ]]; then
        missing+=("$var")
    fi
done

if (( ${#missing[@]} > 0 )); then
    echo "ERROR: docker-compose.yml references env vars missing from .env.example:" >&2
    for var in "${missing[@]}"; do
        echo "  - $var" >&2
    done
    echo >&2
    echo "Add each variable (with a brief comment and default) to .env.example" >&2
    echo "so operators can discover and override it." >&2
    exit 1
fi

echo "OK: all ${#compose_vars[@]} compose variable(s) are documented in .env.example."
