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
#
# Portability: the CI matrix runs this script under macOS bash 3.2, which has
# neither `mapfile` nor associative arrays. Use read loops and linear scans of
# the sorted key lists instead of those bash 4+ builtins.
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
# section); GF_SECURITY_ADMIN_PASSWORD and GF_SECURITY_ADMIN_USER are Grafana's
# native container env vars (set by docker-compose.yml from the corresponding
# .env values, never direct .env knobs).
# Extend as needed with a justification for each entry.
DOC_ONLY_ALLOWLIST=(
    HOSTNAME
    CONTAINER_CMD
    GF_SECURITY_ADMIN_PASSWORD
    GF_SECURITY_ADMIN_USER
)

# Prefix skipped on BOTH sides. ATLAS_* variables belong to the Atlas
# dashboard service and are documented in dashboard/README.md; AGENTS.md
# deliberately only carries the prefix pointer, not the full table.
SKIP_PREFIX='ATLAS_'

# Doc-side extraction region: everything between these two headings.
DOC_SECTION_START='^## Environment Variables'
DOC_SECTION_END='^## Scrape Targets'

in_list() {
    local needle="$1"
    shift
    local item
    # `"$@"` rather than ${1+"$@"}: with the list emptied by shift, bash 3.2
    # expands the latter to `0`, which the runner reports as an ambiguous
    # redirect. Quoted "$@" expands to nothing when there are no arguments.
    for item in "$@"; do
        [[ "$item" == "$needle" ]] && return 0
    done
    return 1
}

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
env_keys=()
while IFS= read -r env_key; do
    env_keys+=("$env_key")
done < <(
    grep -oE '^[[:space:]]*#?[[:space:]]*[A-Z_][A-Z0-9_]*=' "$ENV_EXAMPLE" \
        | sed -E 's/^[[:space:]]*#?[[:space:]]*//; s/=$//' \
        | sort -u
)

# Extract every backticked uppercase name from the AGENTS.md environment
# region. The generic form (any [A-Z][A-Z0-9_]{2,} token) is intentional:
# a prefix-limited variant would miss documented names like AGAMEMNON_URL.
# Kept as sed + grep -oE: both are available and behave identically on the
# bash 3.2 macOS runner. Do not move the interval into an awk program --
# mawk builds do not implement {n,}.
doc_vars=()
while IFS= read -r doc_var; do
    doc_vars+=("$doc_var")
done < <(
    sed -n "/${DOC_SECTION_START}/,/${DOC_SECTION_END}/p" "$DOC_FILE" \
        | grep -oE '`[A-Z][A-Z0-9_]{2,}`' \
        | sed 's/`//g' \
        | sort -u
)

# Absolute line number of the first mention of a documented name, for the
# reverse-drift report. Computed only on the failure path so the happy path
# stays a single pass. $var is an uppercase name, so it carries no regex
# metacharacters; backticks are added via printf to keep the shell from
# reading them as command substitution.
doc_line_for() {
    local var="$1"
    local start_line rel pattern
    start_line="$(grep -n -m1 -E "${DOC_SECTION_START}" "$DOC_FILE" | cut -d: -f1)"
    printf -v pattern '`%s`' "$var"
    rel="$(sed -n "/${DOC_SECTION_START}/,/${DOC_SECTION_END}/p" "$DOC_FILE" \
        | grep -n -m1 -oE "$pattern" | cut -d: -f1)"
    if [[ -z "$start_line" || -z "$rel" ]]; then
        printf 'unknown line'
        return 0
    fi
    printf '%s' "$(( start_line + rel - 1 ))"
}

# Both lists are sorted and duplicate-free, so a linear scan is exact.
missing_docs=()
for var in ${env_keys[@]+"${env_keys[@]}"}; do
    if has_skip_prefix "$var"; then
        continue
    fi
    if ! in_list "$var" ${doc_vars[@]+"${doc_vars[@]}"}; then
        missing_docs+=("$var")
    fi
done

unknown_docs=()
for var in ${doc_vars[@]+"${doc_vars[@]}"}; do
    if has_skip_prefix "$var"; then
        continue
    fi
    if is_doc_allowlisted "$var"; then
        continue
    fi
    if ! in_list "$var" ${env_keys[@]+"${env_keys[@]}"}; then
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
            echo "  - $var (${DOC_FILE}:$(doc_line_for "$var"))" >&2
        done
        echo >&2
        echo "Add each variable (with a brief comment and default) to .env.example," >&2
        echo "or extend DOC_ONLY_ALLOWLIST in this script with a justification." >&2
        echo "Extraction treats every backticked UPPER_SNAKE token in this section" >&2
        echo "as a variable, so a prose acronym in backticks needs an allowlist" >&2
        echo "entry as well. Rephrase the prose to drop the backticks instead." >&2
    fi
    exit 1
fi

echo "OK: all ${#env_keys[@]} .env.example variable(s) are consistent with ${DOC_FILE}."
