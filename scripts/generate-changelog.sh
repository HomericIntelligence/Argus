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

# Use indexed arrays only: macOS runners still ship Bash 3.2, which does not
# support associative arrays.
declare -a section_order=("feat" "fix" "docs" "chore" "refactor" "test" "ci" "other")
declare -a section_titles=("### Added" "### Fixed" "### Documentation" "### Chore" "### Refactored" "### Tests" "### CI" "### Other")
declare -a sections=("" "" "" "" "" "" "" "")

section_index() {
    case "$1" in
        feat) echo 0 ;;
        fix) echo 1 ;;
        docs) echo 2 ;;
        chore) echo 3 ;;
        refactor) echo 4 ;;
        test) echo 5 ;;
        ci) echo 6 ;;
        *) echo 7 ;;
    esac
}

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

    section_index_value=$(section_index "$type")
    if [[ -n "${sections[$section_index_value]}" ]]; then
        sections[$section_index_value]="${sections[$section_index_value]}"$'\n'"${entry}"
    else
        sections[$section_index_value]="${entry}"
    fi
done < <(git log "${RANGE}" --format="%h%x09%s%x09%an" 2>/dev/null || true)

# Print non-empty sections in order
first=true
for section_index_value in "${!section_order[@]}"; do
    if [[ -n "${sections[$section_index_value]}" ]]; then
        if [[ "$first" == "false" ]]; then
            echo ""
        fi
        echo "${section_titles[$section_index_value]}"
        echo ""
        echo "${sections[$section_index_value]}"
        first=false
    fi
done
