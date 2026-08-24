#!/usr/bin/env bash
# check-env-docs.sh — fail if .env.example variable names drift from AGENTS.md.
#
# Background: .env.example is the operator-facing canonical list of stack
# variables, and the "Environment Variables" section of AGENTS.md is the
# agent/operator contract documenting them. Without enforcement, a variable
# added to one file silently disappears from the other (the gap fixed for
# NATS_LOG_DIR in #147 was found manually). This script extracts every KEY=
# entry from .env.example (commented or not) and every backticked uppercase
# name inside the AGENTS.md environment-variable region, then reports both
# drift directions:
#
#   - var present in .env.example but undocumented in AGENTS.md -> fail
#   - var documented in AGENTS.md but absent from .env.example  -> fail
#
# Exit codes: 0 = no drift, 1 = drift detected, 2 = missing input file.
#
# Follow-up to HomericIntelligence/Argus#147; closes #385.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_EXAMPLE="${1:-${REPO_ROOT}/.env.example}"
DOC_FILE="${2:-${REPO_ROOT}/AGENTS.md}"

if [[ ! -f "$ENV_EXAMPLE" ]]; then
    echo "ERROR: $ENV_EXAMPLE not found" >&2
    exit 2
fi
if [[ ! -f "$DOC_FILE" ]]; then
    echo "ERROR: $DOC_FILE not found" >&2
    exit 2
fi

# Variables mentioned in AGENTS.md that are intentionally absent from
# .env.example. HOSTNAME / CONTAINER_CMD are operator-shell or justfile-level
# knobs (see the "Optional overrides" bullets in the Environment Variables
# section); GF_SECURITY_ADMIN_PASSWORD is Grafana's native container env var
# (set by docker-compose.yml from GRAFANA_ADMIN_PASSWORD, never a .env knob).
# Extend as needed with a justification for each entry.
DOC_ONLY_ALLOWLIST=(
    HOSTNAME
    CONTAINER_CMD
    GF_SECURITY_ADMIN_PASSWORD
)

# Prefix skipped on BOTH sides. ATLAS_* variables belong to the Atlas
# dashboard service and are documented in dashboard/README.md; AGENTS.md
# deliberately only carries the prefix pointer, not the full table.
SKIP_PREFIX='ATLAS_'

# Doc-side extraction region: everything between these two headings.
DOC_SECTION_START='^## Environment Variables'
DOC_SECTION_END='^## Scrape Targets'

is_doc_allowlisted() {
    local var="$1"
    local allowed
    for allowed in "${DOC_ONLY_ALLOWLIST[@]}"; do
        [[ "$var" == "$allowed" ]] && return 0
    done
    return 1
}

has_skip_prefix() {
    [[ "$1" == "${SKIP_PREFIX}"* ]]
}

# Extract every KEY= entry (commented or not) from .env.example. A line of
# the form "# FOO=..." counts as defined because operators rely on the inline
# comment to discover optional knobs. Same extraction as check-env-example.sh.
mapfile -t env_keys < <(
    grep -oE '^[[:space:]]*#?[[:space:]]*[A-Z_][A-Z0-9_]*=' "$ENV_EXAMPLE" \
        | sed -E 's/^[[:space:]]*#?[[:space:]]*//; s/=$//' \
        | sort -u
)

# Extract every backticked uppercase name from the AGENTS.md environment
# region. The generic form (any [A-Z][A-Z0-9_]{2,} token) is intentional:
# a prefix-limited variant would miss documented names like AGAMEMNON_URL.
mapfile -t doc_vars < <(
    sed -n "/${DOC_SECTION_START}/,/${DOC_SECTION_END}/p" "$DOC_FILE" \
        | grep -oE '`[A-Z][A-Z0-9_]{2,}`' \
        | sed 's/`//g' \
        | sort -u
)

declare -A doc_seen=()
for v in "${doc_vars[@]}"; do
    doc_seen["$v"]=1
done

declare -A env_seen=()
for k in "${env_keys[@]}"; do
    env_seen["$k"]=1
done

missing_docs=()
for var in "${env_keys[@]}"; do
    if has_skip_prefix "$var"; then
        continue
    fi
    if [[ -z "${doc_seen[$var]:-}" ]]; then
        missing_docs+=("$var")
    fi
done

unknown_docs=()
for var in "${doc_vars[@]}"; do
    if has_skip_prefix "$var"; then
        continue
    fi
    if is_doc_allowlisted "$var"; then
        continue
    fi
    if [[ -z "${env_seen[$var]:-}" ]]; then
        unknown_docs+=("$var")
    fi
done

if (( ${#missing_docs[@]} > 0 || ${#unknown_docs[@]} > 0 )); then
    if (( ${#missing_docs[@]} > 0 )); then
        echo "::error::.env.example defines variables undocumented in ${DOC_FILE}:" >&2
        for var in "${missing_docs[@]}"; do
            echo "  - $var" >&2
        done
        echo >&2
        echo "Add each variable to the Environment Variables section of ${DOC_FILE}" >&2
        echo "(or remove it from .env.example if it is no longer used)." >&2
    fi
    if (( ${#unknown_docs[@]} > 0 )); then
        echo "::error::${DOC_FILE} documents variables absent from .env.example:" >&2
        for var in "${unknown_docs[@]}"; do
            echo "  - $var" >&2
        done
        echo >&2
        echo "Add each variable (with a brief comment and default) to .env.example," >&2
        echo "or extend DOC_ONLY_ALLOWLIST in this script with a justification." >&2
    fi
    exit 1
fi

echo "OK: all ${#env_keys[@]} .env.example variable(s) are consistent with ${DOC_FILE}."
