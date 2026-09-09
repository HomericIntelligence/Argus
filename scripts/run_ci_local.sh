#!/bin/bash
# Run the Argus CI suite locally inside a container.
#
# Mirrors what GitHub Actions runs, using the same CI container image.
# Supports both Podman (rootless, no SU — preferred) and Docker.
#
# Usage:
#   ./scripts/run_ci_local.sh              # Run all CI checks
#   ./scripts/run_ci_local.sh lint         # yamllint + JSON + gosec
#   ./scripts/run_ci_local.sh unit-tests   # yamllint + pytest
#   ./scripts/run_ci_local.sh atlas-dashboard  # Go vet + test
#
# Container engine: auto-detected (podman first, docker fallback).
# Override: CONTAINER_ENGINE=docker ./scripts/run_ci_local.sh
#
# Image: uses 'argus-ci:local' if available, falls back to GHCR image.
# Build locally: just ci-build  (or: podman build -f ci/Containerfile -t argus-ci:local .)

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SUBSET="${1:-all}"

LOCAL_IMAGE="argus-ci:local"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[CI]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[CI]${NC} $*"; }
log_error() { echo -e "${RED}[CI]${NC} $*" >&2; }
log_step()  { echo -e "\n${BLUE}==>${NC} $*"; }

# ============================================================================
# Container engine detection
# ============================================================================

detect_engine() {
    if [ -n "${CONTAINER_ENGINE:-}" ]; then
        if ! command -v "${CONTAINER_ENGINE}" &> /dev/null; then
            log_error "CONTAINER_ENGINE=${CONTAINER_ENGINE} not found in PATH"
            exit 1
        fi
        log_info "Container engine: ${CONTAINER_ENGINE} (from env)"
        return
    fi

    if command -v podman &> /dev/null; then
        CONTAINER_ENGINE="podman"
        log_info "Container engine: podman (rootless)"
    elif command -v docker &> /dev/null; then
        CONTAINER_ENGINE="docker"
        log_info "Container engine: docker"
    else
        log_error "No container engine found. Install podman (recommended) or docker."
        exit 1
    fi
    export CONTAINER_ENGINE
}

# ============================================================================
# Image resolution
# ============================================================================

resolve_image() {
    if "${CONTAINER_ENGINE}" images -q "${LOCAL_IMAGE}" 2>/dev/null | grep -q .; then
        CI_IMAGE="${LOCAL_IMAGE}"
        log_info "Using local CI image: ${CI_IMAGE}"
    else
        log_error "Local image '${LOCAL_IMAGE}' not found."
        log_error "Build it first: just ci-build"
        exit 1
    fi
    export CI_IMAGE
}

# ============================================================================
# Run a command inside the CI container
# ============================================================================
# Volume mounts:
#   /workspace       — the full repo (rw, :Z for SELinux/Podman)
#   ~/.cache/pixi    — persistent pixi cache so 'pixi install' is fast on warm runs
# --userns=keep-id:uid=1000,gid=1000 maps the host UID to the image's 'ci' user
#
# Usage: run_in_container [NO_PIXI_READY] "command..."
#   NO_PIXI_READY skips the pixi install + env-PATH setup for commands that
#   only need system binaries (go, gitleaks, gosec).
run_in_container() {
    local no_pixi=0
    if [[ "${1:-}" == NO_PIXI_READY ]]; then
        no_pixi=1
        shift
    fi
    local cmd="$*"
    local engine_flags=()

    if [ "${CONTAINER_ENGINE}" = "podman" ]; then
        engine_flags+=( "--userns=keep-id:uid=1000,gid=1000" )
    fi

    local script="set -euo pipefail"
    if [ "${no_pixi}" -eq 0 ]; then
        # Drop a stale env whose shebangs point at a host-side prefix (e.g.
        # from running pixi natively on the same checkout) so binaries always
        # resolve to /workspace inside the container.
        script+=$'\nif [ -d .pixi/envs/default/bin ] && ! grep -q \'^#!/workspace/.pixi\' .pixi/envs/default/bin/python3* 2>/dev/null; then rm -rf .pixi; fi'
        script+=$'\nif [ -f pixi.lock ]; then pixi install --locked; else pixi install; fi'
        script+=$'\nexport PATH="/workspace/.pixi/envs/default/bin:$PATH"'
    fi
    script+=$'\n'"${cmd}"

    mkdir -p "${HOME}/.cache/pixi"

    "${CONTAINER_ENGINE}" run --rm \
        "${engine_flags[@]}" \
        --volume "${PROJECT_ROOT}:/workspace:Z" \
        --volume "${HOME}/.cache/pixi:/home/ci/.cache/pixi:Z" \
        --workdir /workspace \
        "${CI_IMAGE}" \
        bash -c "${script}"
}

# ============================================================================
# CI subsets — mirror the jobs in .github/workflows/_required.yml
# ============================================================================

run_lint() {
    log_step "lint — yamllint"
    run_in_container "yamllint -c .yamllint.yaml ."

    log_step "lint — validate JSON files"
    run_in_container "JSON_FILES=\$(find . -path './.git' -prune -o -name '*.json' -print | grep '\\.json\$' || true); \
        if [ -n \"\$JSON_FILES\" ]; then FAILED=0; while IFS= read -r f; do \
        if jq . \"\$f\" > /dev/null 2>&1; then echo \"OK: \$f\"; else echo \"INVALID JSON: \$f\"; FAILED=1; fi; \
        done <<< \"\$JSON_FILES\"; if [ \"\$FAILED\" -eq 1 ]; then echo 'FAIL: one or more JSON files are invalid'; exit 1; fi; \
        echo 'OK: all JSON files are valid'; else echo 'No JSON files found — nothing to validate'; fi"

    log_step "lint — gosec (Go dashboard)"
    run_in_container NO_PIXI_READY "if [ -f dashboard/go.mod ]; then gosec ./dashboard/...; else echo '::notice::No dashboard/go.mod, skipping gosec'; fi"

    log_step "lint — bash -n + shellcheck (tracked *.sh)"
    # The checker binary is baked into the image (see ci/Containerfile); the
    # gate logic lives in tests/test-shell-lint.sh so CI, local container runs
    # and `just check-shell` all enforce exactly the same checks.
    run_in_container NO_PIXI_READY "bash tests/test-shell-lint.sh"
}

run_pixi_check() {
    log_step "pixi-check"
    run_in_container "if [ ! -f pixi.toml ]; then echo '::notice::No pixi.toml in repo, skipping pixi-check'; exit 0; fi; \
        if [ -f pixi.lock ]; then pixi install --locked; else echo '::warning::pixi.toml present but pixi.lock missing — running unlocked'; pixi install; fi"
}

run_unit_tests() {
    log_step "unit-tests — yamllint + pytest"
    run_in_container "yamllint -c .yamllint.yaml . && \
        if command -v promtool &>/dev/null && [ -d rules ]; then \
        mapfile -t rule_files < <(find rules -type f \\( -name '*.yml' -o -name '*.yaml' \\)); \
        if [ \"\${#rule_files[@]}\" -gt 0 ]; then promtool check rules \"\${rule_files[@]}\"; fi; fi; \
        pixi run pytest tests/ -v"
}

run_integration_tests() {
    log_step "integration-tests — docker image name format validation"
    run_in_container NO_PIXI_READY "python3 scripts/validate_compose_images.py"
}

run_security_secrets_scan() {
    log_step "security-secrets-scan — gitleaks"
    run_in_container NO_PIXI_READY "if [ -f .gitleaks.toml ]; then \
        gitleaks detect --source . --config .gitleaks.toml --report-format sarif --report-path gitleaks.sarif --exit-code 0; \
        else gitleaks detect --source . --report-format sarif --report-path gitleaks.sarif --exit-code 0; fi"
}

run_config_validate() {
    log_step "config-validate — YAML/HCL"
    run_in_container "HCL_FILES=\$(find . -path './.git' -prune -o -name '*.hcl' -print | grep '\\.hcl\$' || true); \
        if [ -n \"\$HCL_FILES\" ]; then \
        if command -v nomad &>/dev/null; then echo \"\$HCL_FILES\" | xargs -I{} nomad job validate {}; \
        elif command -v hclfmt &>/dev/null; then echo \"\$HCL_FILES\" | xargs -I{} hclfmt --check {}; \
        else echo '::notice::HCL files found but no nomad/hclfmt available; falling back to YAML validation'; yamllint -c .yamllint.yaml .; fi; \
        else echo '::notice::No HCL files found; validating YAML configs'; yamllint -c .yamllint.yaml .; fi"
}

run_schema_validation() {
    log_step "schema-validation — GitHub Actions schema"
    run_in_container "pixi run pip install --quiet check-jsonschema && \
        check-jsonschema --schemafile https://json.schemastore.org/github-workflow .github/workflows/*.yml"
}

run_atlas_dashboard() {
    log_step "atlas-dashboard — Go vet + test"
    run_in_container NO_PIXI_READY "cd dashboard && go vet ./... && go test ./internal/nats/..."
}

# ============================================================================
# Dispatch
# ============================================================================

detect_engine
resolve_image

case "${SUBSET}" in
    lint)                 run_lint ;;
    pixi-check)           run_pixi_check ;;
    unit-tests)           run_unit_tests ;;
    integration-tests)    run_integration_tests ;;
    security-secrets-scan) run_security_secrets_scan ;;
    config-validate)      run_config_validate ;;
    schema-validation)    run_schema_validation ;;
    atlas-dashboard)      run_atlas_dashboard ;;
    all)
        run_lint
        run_pixi_check
        run_unit_tests
        run_integration_tests
        run_security_secrets_scan
        run_config_validate
        run_schema_validation
        run_atlas_dashboard
        ;;
    *)
        log_error "Unknown subset: ${SUBSET}"
        echo "Valid subsets: lint, pixi-check, unit-tests, integration-tests, security-secrets-scan, config-validate, schema-validation, atlas-dashboard, all"
        exit 1
        ;;
esac

log_info "All CI checks passed."
