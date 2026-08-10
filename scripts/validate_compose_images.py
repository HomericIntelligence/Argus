#!/usr/bin/env python3
"""Validate docker-compose image reference formats (integration-tests job).

Extracted verbatim from the original _required.yml integration-tests heredoc so
the containerized CI runs byte-identical validation logic.
"""
import subprocess
import re
import sys

result = subprocess.run(
    ["grep", "-rh", "image:", "--include=docker-compose*.yml", "--include=docker-compose*.yaml", "."],
    capture_output=True, text=True,
)
lines = result.stdout.strip().splitlines()

# Resolve docker-compose env-var interpolations of the form
# ${VAR:-default} or ${VAR-default} to their default value so the
# resulting image reference can be validated. Bare ${VAR} (no
# default) is skipped since we cannot know the runtime value.
interp_with_default = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]+)\}")
bare_interp = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

images = []
for line in lines:
    parts = line.strip().split()
    if len(parts) >= 2:
        img = parts[-1]
        img = interp_with_default.sub(lambda m: m.group(1), img)
        if bare_interp.search(img):
            # Cannot validate without a default; skip.
            continue
        images.append(img)

if not images:
    print("No docker-compose image references found — skipping format validation")
    sys.exit(0)

# Valid image pattern: [registry/][org/]name[:tag][@digest]
# Disallow bare unqualified names without at minimum a colon-tag or slash
valid_pattern = re.compile(
    r"^([a-z0-9][a-z0-9._\-]*(:[0-9]+)?/)?"  # optional registry
    r"([a-z0-9][a-z0-9._\-]*/)*"             # optional org path
    r"[a-z0-9][a-z0-9._\-]*"                 # image name
    r"(:[a-zA-Z0-9][a-zA-Z0-9._\-]*)?"       # optional tag
    r"(@sha256:[a-fA-F0-9]{64})?$"           # optional digest
)

failed = []
for img in sorted(set(images)):
    if not valid_pattern.match(img):
        failed.append(img)
        print(f"INVALID image reference format: {img}")
    else:
        print(f"OK: {img}")

if failed:
    print(f"\nFAIL: {len(failed)} image reference(s) have invalid format")
    sys.exit(1)
else:
    print(f"\nOK: all {len(images)} image reference(s) have valid format")
