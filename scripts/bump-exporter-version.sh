#!/usr/bin/env bash
# Bumps the exporter image version (patch|minor|major) atomically across
# exporter/VERSION and the pinned tag in docker-compose.yml, verifies
# consistency, and creates a git commit. Refs #393.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VERSION_FILE="${REPO_ROOT}/exporter/VERSION"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
CHECKER="${REPO_ROOT}/scripts/check-exporter-image-version.py"

usage() {
    echo "Usage: $0 <patch|minor|major> [--dry-run]" >&2
    exit 1
}

[[ $# -lt 1 || $# -gt 2 ]] && usage
BUMP_TYPE="$1"
[[ "$BUMP_TYPE" =~ ^(patch|minor|major)$ ]] || usage
DRY_RUN=0
if [[ $# -eq 2 ]]; then
    [[ "$2" == "--dry-run" ]] || usage
    DRY_RUN=1
fi

CURRENT_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
if ! [[ "$CURRENT_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: exporter/VERSION is not strict semver: '${CURRENT_VERSION}'" >&2
    exit 1
fi

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

case "$BUMP_TYPE" in
    patch) PATCH=$((PATCH + 1)) ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"

echo "Bumping exporter image ${CURRENT_VERSION} → ${NEW_VERSION} (${BUMP_TYPE})"

OLD_TAG="ghcr.io/homericintelligence/argus-exporter:v${CURRENT_VERSION}"
NEW_TAG="ghcr.io/homericintelligence/argus-exporter:v${NEW_VERSION}"

# Exact-once guard: fail loudly if the compose pin is missing or duplicated.
# grep -c exits 1 when the count is zero but still prints "0".
if ! OCCURRENCES="$(grep -cF -- "${OLD_TAG}" "$COMPOSE_FILE")"; then
    OCCURRENCES=0
fi
if [[ "$OCCURRENCES" -ne 1 ]]; then
    echo "ERROR: expected exactly one '${OLD_TAG}' in docker-compose.yml, found ${OCCURRENCES}" >&2
    exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "--dry-run: would rewrite:"
    echo "  exporter/VERSION: ${CURRENT_VERSION} → ${NEW_VERSION}"
    echo "  docker-compose.yml: ${OLD_TAG} → ${NEW_TAG}"
    exit 0
fi

sed -i "s|^${CURRENT_VERSION}$|${NEW_VERSION}|" "$VERSION_FILE"
sed -i "s|${OLD_TAG}|${NEW_TAG}|" "$COMPOSE_FILE"

# Verify both files agree before committing.
python3 "$CHECKER"

git -C "$REPO_ROOT" add "$VERSION_FILE" "$COMPOSE_FILE"
git -C "$REPO_ROOT" commit -m "chore(exporter): bump exporter image version to v${NEW_VERSION}"

echo "Done. Committed exporter image bump to v${NEW_VERSION}."
echo "Push to main touching exporter/** re-publishes the :v${NEW_VERSION} GHCR tag."
