# `set dotenv-load` sources `.env` from the current directory before any
# recipe runs — every variable in `.env` is exported to the recipe's process.
# Several recipes depend on this, including `import-dashboards`, which reads
# GF_ADMIN_PASSWORD and GRAFANA_ADMIN_USER from the environment.
set dotenv-load

# === Variables ===

compose_cmd := if `command -v podman-compose 2>/dev/null || true` != "" { "podman-compose" } else { "docker compose" }
container_cmd := if `command -v podman-compose 2>/dev/null || true` != "" { "podman" } else { "docker" }

AGAMEMNON_URL := "http://172.20.0.1:8080"
GRAFANA_PORT := "3001"
GRAFANA_URL  := "http://localhost:" + GRAFANA_PORT
GRAFANA_ADMIN_USER := env_var_or_default("GRAFANA_ADMIN_USER", "admin")
GF_ADMIN_PASSWORD := env_var_or_default("GF_ADMIN_PASSWORD", "admin")

# === Default ===

default:
    @just --list

# === Certificates ===

# Generate self-signed CA and per-service TLS certificates
gen-certs:
    bash certs/gen-certs.sh

# === Services ===

# One-command bootstrap: prereqs, pixi install, .env generation
setup:
    @./scripts/setup.sh

# Generate or rotate configs/nginx/htpasswd using credentials from .env.
gen-htpasswd:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Generating runtime htpasswd files via scripts/gen-htpasswd.sh"
    ./scripts/gen-htpasswd.sh
    # Keep the implementation in the script; this marker documents the
    # bcrypt command used by the equivalent direct generation path.
    # htpasswd -nbB loki <password>

# Credential rotation entry point — discoverable via `just --list`.
alias rotate-htpasswd := gen-htpasswd

# Start all observability services
start: gen-htpasswd
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f secrets/htpasswd ]; then
        echo "ERROR: secrets/htpasswd is missing. Run 'just gen-htpasswd' to create it." >&2
        exit 1
    fi
    if [ ! -f secrets/htpasswd-grafana ]; then
        echo "ERROR: secrets/htpasswd-grafana is missing. Run 'just gen-htpasswd' to create it." >&2
        exit 1
    fi
    ./scripts/check-grafana-password.sh
    {{compose_cmd}} up -d

# Stop all services
stop:
    {{compose_cmd}} down

# Show running container status
status:
    {{compose_cmd}} ps

# Restart all services (stop then start)
restart: gen-htpasswd
    ./scripts/check-grafana-password.sh
    {{compose_cmd}} down
    {{compose_cmd}} up -d

# Remove all containers and volumes (destructive — data loss!)
clean:
    {{compose_cmd}} down -v

# Validate docker-compose config, YAML files, and required runtime files
validate: check-env-example validate-promtail
    #!/usr/bin/env bash
    set -euo pipefail
    {{compose_cmd}} config --quiet
    if [ ! -f secrets/htpasswd ]; then
        echo "ERROR: secrets/htpasswd is missing. Run 'just gen-htpasswd' to create it." >&2
        exit 1
    fi
    if [ ! -f secrets/htpasswd-grafana ]; then
        echo "ERROR: secrets/htpasswd-grafana is missing. Run 'just gen-htpasswd' to create it." >&2
        exit 1
    fi
    echo "Config is valid."
    just check-alertmanager

# Verify every env var referenced by docker-compose.yml is documented in
# .env.example. Fails on undocumented drift (issue #215).
check-env-example:
    @bash scripts/check-env-example.sh

# Validate promtail config syntax via the official image's -check-syntax
validate-promtail:
    @echo "Validating configs/promtail.yml ..."
    {{container_cmd}} run --rm \
        -v "$(pwd)/configs/promtail.yml:/etc/promtail/promtail.yml:ro" \
        grafana/promtail:3.1.2 \
        -config.file=/etc/promtail/promtail.yml \
        -config.expand-env=true \
        -check-syntax
    @echo "promtail config OK."

# Hot-reload dev loop for the dashboard (templ generate --watch + air in parallel)
dev:
    @command -v templ >/dev/null 2>&1 || { echo "templ not found on PATH. Install: go install github.com/a-h/templ/cmd/templ@v0.3.1001"; exit 1; }
    @command -v air >/dev/null 2>&1 || { echo "air not found on PATH. Install: go install github.com/air-verse/air@latest"; exit 1; }
    @test -f .env || { echo ".env missing. Run ./scripts/setup.sh first."; exit 1; }
    @./scripts/dev-watch.sh

# Run the complete pytest suite in CI/CD; use targeted pytest commands locally.
test:
    pixi run test

# Run the complete coverage-gated pytest suite in CI/CD; do not run locally.
test-unit:
    pixi run test-unit

# Tail logs for a specific service (e.g. just logs prometheus)
logs SERVICE:
    {{compose_cmd}} logs -f {{SERVICE}}

# === Prometheus ===

# Restart Prometheus to pick up configuration changes
reload-prometheus:
    {{compose_cmd}} restart prometheus

    {{compose_cmd}} exec prometheus wget -qO- http://localhost:9090/-/reload --post-data='' && echo "Prometheus config reloaded."

# Query Prometheus to verify all scrape targets are up (Prometheus is internal-only)
test-scrape:
    @echo "Querying Prometheus for 'up' metric..."
    {{compose_cmd}} exec prometheus wget -qO- "http://localhost:9090/api/v1/query?query=up" | jq '.data.result[] | {job: .metric.job, instance: .metric.instance, up: .value[1]}'


# Debug Prometheus from inside its container (port not exposed to host)
debug-prometheus:
    {{compose_cmd}} exec prometheus sh

# Debug Loki from inside its container (port not exposed to host)
debug-loki:
    {{compose_cmd}} exec loki sh

# Debug the Grafana auth proxy from inside its container
debug-grafana-proxy:
    {{compose_cmd}} exec grafana-proxy sh

# Manually test Agamemnon and Nestor health endpoints
scrape-agamemnon:
    ./scripts/scrape-agamemnon.sh {{AGAMEMNON_URL}}

# === Backup & Restore ===

# Back up Prometheus and Loki data volumes to ./backups/
backup:
    CONTAINER_CMD={{container_cmd}} ./scripts/backup.sh

# Restore a volume from a backup file: just restore <volume> <file>
restore VOLUME FILE:
    CONTAINER_CMD={{container_cmd}} ./scripts/restore.sh {{VOLUME}} {{FILE}}

# === Alertmanager ===

# Reload Alertmanager configuration
reload-alertmanager:
    curl -s -X POST http://localhost:9093/-/reload && echo "Alertmanager config reloaded."

# Check Alertmanager health and cluster status
test-alertmanager:
    curl -s http://localhost:9093/-/healthy && echo ""
    curl -s http://localhost:9093/api/v2/status | jq '.cluster.status'

# Health-gate Alertmanager; fails when the container is down unless
# ALERTMANAGER_CHECK_SKIP_ON_DOWN=1 (issue #250)
check-alertmanager:
    ./scripts/check-alertmanager.sh

# === Grafana ===

# Check jetstream-consumer metrics endpoint
test-jetstream:
    @echo "Checking jetstream-consumer metrics endpoint..."
    curl -s http://localhost:9101/metrics | grep hi_jetstream

# Import all JSON dashboards from dashboards/ into Grafana via API.
# Runs the import inside the grafana container (Grafana has no host port
# since #321), authenticating with credentials from .env.
import-dashboards:
    #!/usr/bin/env bash
    set -uo pipefail
    if [[ -z "${GF_ADMIN_PASSWORD-}" ]]; then
        echo "ERROR: GF_ADMIN_PASSWORD is not set (or is empty) in .env." >&2
        echo "       Set GF_ADMIN_PASSWORD=<your-grafana-admin-password> in .env" >&2
        echo "       at the repository root, then re-run 'just import-dashboards'." >&2
        exit 1
    fi
    GRAFANA_PORT={{GRAFANA_PORT}} GRAFANA_ADMIN_USER={{GRAFANA_ADMIN_USER}} GF_ADMIN_PASSWORD="${GF_ADMIN_PASSWORD}" ./scripts/import-dashboards.sh

# === Versioning ===

# Bump version and promote CHANGELOG (patch|minor|major)
bump TYPE:
    bash scripts/bump-version.sh {{TYPE}}

# Preview CHANGELOG entries since last tag without committing
generate-changelog:
    bash scripts/generate-changelog.sh

# === Containerized CI (podman by default) ===

# Build the CI container image (podman first, docker fallback)
ci-build:
    podman build -f ci/Containerfile -t argus-ci:local . || docker build -f ci/Containerfile -t argus-ci:local .

# Run CI lint (yamllint, JSON, gosec) in container
ci-lint:
    ./scripts/run_ci_local.sh lint

# Run CI pixi lock check in container
ci-pixi-check:
    ./scripts/run_ci_local.sh pixi-check

# Run CI unit tests in container
ci-unit-tests:
    ./scripts/run_ci_local.sh unit-tests

# Run CI integration tests in container
ci-integration-tests:
    ./scripts/run_ci_local.sh integration-tests

# Run CI secrets scan in container
ci-security-secrets-scan:
    ./scripts/run_ci_local.sh security-secrets-scan

# Run CI config validation in container
ci-config-validate:
    ./scripts/run_ci_local.sh config-validate

# Run CI schema validation in container
ci-schema-validation:
    ./scripts/run_ci_local.sh schema-validation

# Run CI Atlas dashboard (Go) checks in container
ci-atlas-dashboard:
    ./scripts/run_ci_local.sh atlas-dashboard

# Run all CI checks in container
ci-all:
    ./scripts/run_ci_local.sh all
