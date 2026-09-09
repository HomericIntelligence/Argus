#!/usr/bin/env bash
# check-ports.sh — runtime smoke test asserting every host-exposed port of the
# running compose stack is bound to 127.0.0.1 (or ::1). Fails loudly if any
# binding starts with 0.0.0.0 or another non-loopback address.
#
# Companion to the authoritative static check (tests/test_port_bindings.py,
# wired into `just validate`); see HomericIntelligence/Argus#327 (follow-up
# to #126). Pure bash + grep — deliberately no jq dependency so it also works
# where jq is unavailable.
#
# Exit codes:
#   0  all published host ports are loopback-only, OR the stack is not
#      running (the static check remains authoritative in that case)
#   1  at least one non-loopback host binding was observed
set -euo pipefail

COMPOSE_CMD="${COMPOSE_CMD:-docker compose}"

if ! output="$($COMPOSE_CMD ps --format json 2>/dev/null)"; then
    echo "INFO: '$COMPOSE_CMD ps' failed; run 'pixi run pytest tests/test_port_bindings.py' for the authoritative static check."
    exit 0
fi
if [[ -z "${output//[[:space:]]/}" ]]; then
    echo "INFO: compose stack not running; run 'pixi run pytest tests/test_port_bindings.py' for the authoritative static check."
    exit 0
fi

# Extract every Publishers[].URL *value*. grep -oE splits each match (so
# multi-publisher rows are not collapsed); sed then trims to the inner value.
# Tolerates both line-delimited JSON (Compose v2.x default) and a single
# JSON array, plus optional whitespace around the ':' separator.
set +e
url_values="$(printf '%s\n' "$output" \
    | grep -oE '"URL"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed -E 's/.*:[[:space:]]*"([^"]*)"$/\1/')"
extract_rc=$?
set -e

violations=()
total=0
if [[ $extract_rc -eq 0 ]]; then
    while IFS= read -r url; do
        [[ -z "$url" ]] && continue
        total=$((total + 1))
        # Strip the trailing :port (and any [ ] IPv6 brackets).
        host="${url%:*}"
        host="${host#[}"
        host="${host%]}"
        case "$host" in
            127.0.0.1 | ::1) ;;
            *)
                violations+=("$url")
                ;;
        esac
    done < <(printf '%s\n' "$url_values")
fi

if ((${#violations[@]} > 0)); then
    echo "ERROR: non-loopback host port binding(s) detected:" >&2
    for v in "${violations[@]}"; do
        echo "  - $v" >&2
    done
    exit 1
fi

if ((total == 0)); then
    # Some runtimes print an empty JSON array instead of nothing on a cold
    # host; either way there is nothing to assert against.
    echo "INFO: compose stack not running; run 'pixi run pytest tests/test_port_bindings.py' for the authoritative static check."
    exit 0
fi

echo "OK: all ${total} host port bindings are loopback-only."
