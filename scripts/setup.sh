#!/usr/bin/env bash
#
# scripts/setup.sh — one-command bootstrap for ProjectArgus
#
# Usage:
#   ./scripts/setup.sh
#
# What it does:
#   1. Verifies prerequisites (pixi, docker or podman with compose, git).
#   2. Installs the pixi environment (uses pixi.lock for reproducibility).
#   3. Generates a fresh .env from .env.example (random bearer token + Grafana
#      admin password) if .env doesn't already exist.
#   4. Verifies the pixi env is usable via `pixi shell-hook`.
#   5. Prints next-steps.
#
# Idempotent: re-running leaves an existing .env alone and skips pixi install
# if the environment is already up-to-date.

set -euo pipefail

# ---------------------------------------------------------------------------
# Locate repo root (this script lives in scripts/, repo root is one level up).
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# Colour output (disabled when stdout is not a TTY, so CI logs stay clean).
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
    C_RESET=$'\033[0m'
    C_GREEN=$'\033[0;32m'
    C_YELLOW=$'\033[0;33m'
    C_RED=$'\033[0;31m'
    C_BOLD=$'\033[1m'
else
    C_RESET=''
    C_GREEN=''
    C_YELLOW=''
    C_RED=''
    C_BOLD=''
fi

step()  { printf '%s\n' "${C_BOLD}-> $*${C_RESET}"; }
ok()    { printf '%s\n' "${C_GREEN}[ok] $*${C_RESET}"; }
warn()  { printf '%s\n' "${C_YELLOW}[warn] $*${C_RESET}" >&2; }
err()   { printf '%s\n' "${C_RED}[err] $*${C_RESET}" >&2; }

# ---------------------------------------------------------------------------
# 1. Prerequisite checks.
# ---------------------------------------------------------------------------
require_cmd() {
    local cmd="$1"
    local hint="${2:-}"
    if ! command -v "${cmd}" >/dev/null 2>&1; then
        err "missing required command: ${cmd}"
        if [ -n "${hint}" ]; then
            printf '       %s\n' "${hint}" >&2
        fi
        return 1
    fi
}

check_compose() {
    # Prefer podman if present; fall back to docker. Either must support a
    # compose subcommand (or have podman-compose / docker-compose alongside).
    if command -v podman >/dev/null 2>&1; then
        if podman compose version >/dev/null 2>&1 \
           || command -v podman-compose >/dev/null 2>&1; then
            ok "container runtime: podman (with compose)"
            return 0
        fi
        warn "podman found but no working compose subcommand"
    fi
    if command -v docker >/dev/null 2>&1; then
        if docker compose version >/dev/null 2>&1 \
           || command -v docker-compose >/dev/null 2>&1; then
            ok "container runtime: docker (with compose)"
            return 0
        fi
        warn "docker found but no working compose subcommand"
    fi
    err "no working container runtime + compose found"
    printf '       install one of:\n' >&2
    printf '         podman + podman-compose: https://podman.io/\n' >&2
    printf '         docker (with built-in compose v2): https://docs.docker.com/engine/install/\n' >&2
    return 1
}

step "Checking prerequisites"
fail=0
require_cmd git \
    "git is required (it should already be present since you cloned this repo)" \
    || fail=1
require_cmd pixi \
    "install pixi: curl -fsSL https://pixi.sh/install.sh | bash" \
    || fail=1
require_cmd openssl \
    "install openssl via your OS package manager (apt-get install openssl, brew install openssl, ...)" \
    || fail=1
check_compose || fail=1
if [ "${fail}" -ne 0 ]; then
    err "prerequisite check failed; install the missing tools above and re-run"
    exit 1
fi
ok "all prerequisites present"

# ---------------------------------------------------------------------------
# 2. Bootstrap pixi environment.
# ---------------------------------------------------------------------------
need_pixi_install=0
if [ ! -d ".pixi" ]; then
    need_pixi_install=1
elif [ -f "pixi.lock" ] && [ "pixi.lock" -nt ".pixi" ]; then
    need_pixi_install=1
fi

if [ "${need_pixi_install}" -eq 1 ]; then
    step "Installing pixi environment"
    if [ -f "pixi.lock" ]; then
        if ! pixi install --locked; then
            err "pixi install --locked failed"
            exit 1
        fi
    else
        warn "no pixi.lock found; installing without --locked"
        if ! pixi install; then
            err "pixi install failed"
            exit 1
        fi
    fi
    ok "pixi environment installed"
else
    ok "pixi environment is up-to-date (skipping install)"
fi

# ---------------------------------------------------------------------------
# 3. Bootstrap .env.
# ---------------------------------------------------------------------------
ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"

if [ -f "${ENV_FILE}" ]; then
    warn "Existing .env detected at ${ENV_FILE}, leaving in place"
    warn "(delete it manually and re-run this script if you want to regenerate)"
else
    if [ ! -f "${ENV_EXAMPLE}" ]; then
        err ".env.example missing — cannot bootstrap .env"
        exit 1
    fi
    step "Generating .env from .env.example"
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"

    bearer_token="$(openssl rand -hex 32)"
    grafana_password="$(openssl rand -base64 24)"

    # In-place edit. Use a portable tmpfile shuffle so we don't depend on
    # GNU vs BSD sed -i differences.
    tmp_env="$(mktemp)"
    # shellcheck disable=SC2002  # explicit cat keeps the pipeline readable
    awk -v tok="${bearer_token}" -v pw="${grafana_password}" '
        /^ATLAS_AUTH_BEARER_TOKEN=/ { print "ATLAS_AUTH_BEARER_TOKEN=" tok; next }
        /^GF_ADMIN_PASSWORD=/        { print "GF_ADMIN_PASSWORD=" pw;       next }
        { print }
    ' "${ENV_FILE}" > "${tmp_env}"
    mv "${tmp_env}" "${ENV_FILE}"
    chmod 0600 "${ENV_FILE}"

    ok "wrote ${ENV_FILE}"
    printf '     ATLAS_AUTH_BEARER_TOKEN starts with: %s...\n' "${bearer_token:0:8}"
    warn "GF_ADMIN_PASSWORD is a random dev-only value; production deployments"
    warn "MUST override it via .env or your secrets manager."
fi

# ---------------------------------------------------------------------------
# 4. Verify pixi env resolves.
# ---------------------------------------------------------------------------
step "Verifying pixi environment is usable"
if ! pixi shell-hook >/dev/null 2>&1; then
    err "pixi shell-hook failed — the pixi environment is not usable"
    err "re-run with verbose output to debug:  pixi shell-hook"
    exit 1
fi
ok "pixi shell-hook succeeded"

# ---------------------------------------------------------------------------
# 5. Next steps.
# ---------------------------------------------------------------------------
printf '\n'
printf '%s\n' "${C_GREEN}${C_BOLD}Setup complete.${C_RESET}"
printf '\n'
printf 'Next steps:\n'
printf '  pixi shell                              # activate the dev environment\n'
printf '  just start                              # bring the full Argus stack up via docker compose\n'
printf '  curl -fsS http://localhost:3002/livez   # verify Atlas is responding\n'
