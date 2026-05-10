#!/usr/bin/env bash
# Bumps pixi.toml version (patch|minor|major), promotes [Unreleased] CHANGELOG section,
# and creates a git commit.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PIXI_TOML="${REPO_ROOT}/pixi.toml"
CHANGELOG="${REPO_ROOT}/CHANGELOG.md"
GENERATE_SCRIPT="${REPO_ROOT}/scripts/generate-changelog.sh"

usage() {
    echo "Usage: $0 <patch|minor|major>" >&2
    exit 1
}

[[ $# -ne 1 ]] && usage
BUMP_TYPE="$1"
[[ "$BUMP_TYPE" =~ ^(patch|minor|major)$ ]] || usage

# Read current version from pixi.toml using Python tomllib
CURRENT_VERSION=$(python3 - "$PIXI_TOML" <<'EOF'
import sys
import tomllib
with open(sys.argv[1], "rb") as f:
    data = tomllib.load(f)
section = data.get("workspace") or data.get("project")
if section is None:
    raise KeyError("pixi.toml has neither [workspace] nor [project] section")
print(section["version"])
EOF
)

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

case "$BUMP_TYPE" in
    patch) PATCH=$((PATCH + 1)) ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
TAG="v${NEW_VERSION}"
TODAY=$(date +%Y-%m-%d)

echo "Bumping ${CURRENT_VERSION} → ${NEW_VERSION} (${BUMP_TYPE})"

# Update version in pixi.toml (safe: only replaces the exact version line)
sed -i "s/^version = \"${CURRENT_VERSION}\"$/version = \"${NEW_VERSION}\"/" "$PIXI_TOML"

# Generate changelog entries since last tag
CHANGELOG_BODY=$(bash "$GENERATE_SCRIPT")

# Build the new versioned section
NEW_SECTION="## [${NEW_VERSION}] - ${TODAY}"$'\n\n'"${CHANGELOG_BODY}"

# Extract repo base URL from CHANGELOG
BASE_URL=$(grep -oP 'https://github\.com/[^/]+/[^/]+' "$CHANGELOG" | head -1 || true)

# Splice CHANGELOG:
# 1. Replace empty [Unreleased] section (## [Unreleased]\n\n## [prev]) with
#    ## [Unreleased]\n\n## [new]\n\n## [prev]
# 2. Update the link references at the bottom
python3 - "$CHANGELOG" "$NEW_VERSION" "$CURRENT_VERSION" "$NEW_SECTION" "$BASE_URL" <<'PYEOF'
import sys

changelog_path = sys.argv[1]
new_ver = sys.argv[2]
cur_ver = sys.argv[3]
new_section = sys.argv[4]
base_url = sys.argv[5]

with open(changelog_path) as f:
    content = f.read()

# Insert new versioned section after [Unreleased] header (and its blank line)
unreleased_marker = "## [Unreleased]\n"
idx = content.find(unreleased_marker)
if idx == -1:
    print("ERROR: [Unreleased] section not found in CHANGELOG.md", file=sys.stderr)
    sys.exit(1)

insert_at = idx + len(unreleased_marker)
# Skip any blank lines immediately after [Unreleased]
while insert_at < len(content) and content[insert_at] == '\n':
    insert_at += 1

content = content[:insert_at] + "\n" + new_section + "\n\n" + content[insert_at:]

# Update link references
old_unreleased_link = f"[Unreleased]: {base_url}/compare/v{cur_ver}...HEAD"
new_unreleased_link = f"[Unreleased]: {base_url}/compare/v{new_ver}...HEAD"
new_ver_link = f"[{new_ver}]: {base_url}/compare/v{cur_ver}...v{new_ver}"

content = content.replace(old_unreleased_link, new_unreleased_link)

# Insert new version link after [Unreleased] link
if new_unreleased_link in content and new_ver_link not in content:
    content = content.replace(
        new_unreleased_link,
        new_unreleased_link + "\n" + new_ver_link,
    )

with open(changelog_path, "w") as f:
    f.write(content)

print(f"CHANGELOG.md updated with [{new_ver}] section.")
PYEOF

# Stage and commit
git -C "$REPO_ROOT" add "$PIXI_TOML" "$CHANGELOG"
git -C "$REPO_ROOT" commit -m "chore(release): bump version to ${TAG}"

echo "Done. Committed version bump to ${TAG}."
echo "To create a release tag: git tag ${TAG} && git push origin ${TAG}"
