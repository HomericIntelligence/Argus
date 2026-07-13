#!/usr/bin/env bash
# Generates a Markdown CHANGELOG section body from git log since the last v* tag.
# Outputs grouped conventional-commit entries; no side effects.
set -euo pipefail

LAST_TAG=$(git tag --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true)

if [[ -z "$LAST_TAG" ]]; then
    RANGE="HEAD"
else
    RANGE="${LAST_TAG}..HEAD"
fi

declare -A sections
declare -a section_order=("feat" "fix" "docs" "chore" "refactor" "test" "ci" "other")
declare -A section_titles=(
    ["feat"]="### Added"
    ["fix"]="### Fixed"
    ["docs"]="### Documentation"
    ["chore"]="### Chore"
    ["refactor"]="### Refactored"
    ["test"]="### Tests"
    ["ci"]="### CI"
    ["other"]="### Other"
)

for key in "${section_order[@]}"; do
    sections[$key]=""
done

while IFS=$'\t' read -r hash subject _author; do
    [[ -z "$subject" ]] && continue

    # Extract conventional commit type (e.g. feat, fix, chore)
    # Assign the regex to a variable first: bash mis-parses the literal
    # parens in `(\([^)]*\))?` when inlined directly in `[[ =~ ]]`.
    conventional_commit_re='^([a-z]+)(\([^)]*\))?!?: (.*)$'
    if [[ "$subject" =~ $conventional_commit_re ]]; then
        type="${BASH_REMATCH[1]}"
        scope="${BASH_REMATCH[2]}"
        desc="${BASH_REMATCH[3]}"
        scope="${scope#(}"
        scope="${scope%)}"
        if [[ -n "$scope" ]]; then
            entry="- **${scope}**: ${desc} (${hash})"
        else
            entry="- ${desc} (${hash})"
        fi
    else
        type="other"
        entry="- ${subject} (${hash})"
    fi

    # Map unknown types to "other"
    if [[ -z "${sections[$type]+x}" ]]; then
        type="other"
    fi

    if [[ -n "${sections[$type]}" ]]; then
        sections[$type]="${sections[$type]}"$'\n'"${entry}"
    else
        sections[$type]="${entry}"
    fi
done < <(git log "${RANGE}" --format="%h%x09%s%x09%an" 2>/dev/null || true)

# Print non-empty sections in order
first=true
for key in "${section_order[@]}"; do
    if [[ -n "${sections[$key]}" ]]; then
        if [[ "$first" == "false" ]]; then
            echo ""
        fi
        echo "${section_titles[$key]}"
        echo ""
        echo "${sections[$key]}"
        first=false
    fi
done
