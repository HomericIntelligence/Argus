# Changelog

All notable changes to Argus will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- Removed silent `admin` fallbacks from `docker-compose.yml` and `justfile`.
  Both now fail fast when their respective Grafana password env vars are unset
  (`GRAFANA_ADMIN_PASSWORD` for compose, `GF_ADMIN_PASSWORD` for just-based
  recipes). CI workflows updated to inject `GRAFANA_ADMIN_PASSWORD` for
  compose validation. Regression tests added. (#318, follow-up to #124)

## Upgrade Guide: v0.1.0 → v0.2.0

This release is not backwards-compatible with v0.1.0 deployments without operator action.
The breaking changes are deliberate (closing audit findings) — please apply each step
before rolling the new image.

1. **Auth is on by default.** `ATLAS_AUTH_MODE` previously defaulted to `none`; in v0.2.0
   it defaults to `bearer` and Atlas will refuse to start unless `ATLAS_AUTH_BEARER_TOKEN`
   is also set. To preserve v0.1 behaviour explicitly (not recommended), set
   `ATLAS_AUTH_MODE=none` in your `.env` — Atlas will log a warning at startup.
2. **`/readyz` and `/metrics` are now auth-gated.** Update consumers:
   - **Prometheus scrape config**: add `authorization: { credentials_file: /run/secrets/atlas_token }`
     (or `bearer_token_file:`) to the Atlas scrape job.
   - **Kubernetes readiness probe**: switch from `httpGet: { path: /readyz }` to send the bearer
     token via `httpHeaders`, or move the probe to `/livez` (which remains unauthenticated and
     is a pure process-up signal).
   - **External monitors**: any blackbox prober hitting `/metrics` or `/readyz` needs the
     token; `/livez` and its alias `/healthz` continue to work unauthenticated.
3. **`/healthz` retained as alias of `/livez`.** Existing liveness probes pointing at
   `/healthz` keep working without changes — but new deployments should prefer `/livez`.
4. **Distroless image — no shell, no `wget`.** Container healthchecks that called
   `wget`/`curl` from inside the image will no longer work (the runtime image is
   `gcr.io/distroless/static-debian12:nonroot` — no shell, no package manager). Use HTTP
   probes from the orchestrator (Docker Compose `healthcheck:` with an external runner,
   k8s `httpGet:`, etc.) against `/livez`.
5. **Multi-arch image — pin to `:v0.2.0` not `:latest`.** Releases are now built for
   `linux/amd64` and `linux/arm64`. For reproducibility, pin Compose/Helm/manifests to
   the explicit version tag (`ghcr.io/homericintelligence/atlas:v0.2.0`) rather than
   `:latest`, which floats forward.
6. **NATS subscriber attaches six JetStream streams** (`homeric-agents`, `-tasks`,
   `-myrmidon`, `-research`, `-pipeline`, `-logs`). If your NATS deployment does not have
   JetStream enabled or is missing any of these streams, `/readyz` will report the
   subscriber as not-ready and the live SSE feed will only emit heartbeats.

## [0.2.0] - 2026-05-06

Atlas reaches release-readiness. Closes the audit findings from
`/hephaestus:repo-analyze-strict-full Analyze Epic 151 changes`.
Headline: the JetStream → events.Bus → SSE pipeline (the central
deliverable of Epic 151) is now actually wired in production; `/readyz`
returns 200 only when every component is loaded, enabled, and reporting
valid results without error; and Atlas ships as a 14.9 MB distroless
multi-arch image at `ghcr.io/homericintelligence/atlas:v0.2.0`.

### Added

- Atlas service (M1–M6): Go module with Chi server, SSE endpoint,
  NATS/JetStream subscribers, Tailscale sources, service probe matrix,
  hosts page, Agamemnon poller, agents/task detail pages with live SSE
  event tail, Grafana iframe matrix (8 panels), NATS monitoring page,
  Mnemosyne skill registry browser, pluggable auth middleware
  (none/basic/bearer), `/metrics` Prometheus exposition, alert rules,
  e2e tests, architecture doc
- **NATS spine wiring** — `internal/nats.Subscriber` is now instantiated
  in `cmd/argus-dashboard/main.go`. `nats.Event` is a type alias for
  `events.Event` so `*events.Bus` satisfies `nats.EventBus` directly.
  Subscriber exposes `Ready()` and `Attached()` for /readyz; fail-fast
  if zero JetStream durable subscriptions attach. Integration test
  (`tests/integration/nats_spine_test.go`) verifies 6/6 streams round-
  trip via embedded NATS+JetStream.
- **Real `/readyz` aggregator** — `internal/server/readyz.go` defines a
  ReadyRegistry with per-component checks (poller LastSuccess within
  2× interval; NATS subscriber Ready). Returns JSON
  `{ok, components: [{name, ok, error, last_success}]}`; 200 only when
  every check passes; 503 with the offending component named otherwise.
  Empty registry is treated as not-ready.
- **`/livez`** — pure liveness probe (always 200 if the process is up),
  intended for k8s liveness checks. `/healthz` is kept as an alias for
  back-compat.
- `internal/server/metrics.go` — replaced 287-line hand-rolled Prometheus
  exposition with `prometheus/client_golang`. Same metric names so
  `rules/atlas-alerts.yml` continues to match. Pollers, SSE handler, and
  NATS subscriber now actually emit metrics (previously the entire
  metrics surface was dead instrumentation).
- `dashboard/Dockerfile` — multi-stage `golang:1.25-alpine` builder →
  `gcr.io/distroless/static-debian12:nonroot`. CGO_ENABLED=0,
  -trimpath, -ldflags injects version, USER 65532, OCI labels. Final
  image ~14.9 MB.
- `release.yml` `publish-atlas-image` job — buildx multi-arch
  `linux/amd64,linux/arm64`, GHCR auth via `GITHUB_TOKEN`, publishes
  `:<tag>` and `:latest` on every git tag push, with SBOM and
  provenance attestations.
- `dependabot.yml` — gomod ecosystem on `/dashboard/` (prometheus and
  nats-io grouped); docker on `/dashboard/`; github-actions on `/`.
- CI: `govulncheck`, race-detected coverage profile uploaded as
  artefact, separate integration-tests step.
- `dashboard/docs/review-charter.md` — normative specification for the
  6-dimension review-wave gate that every `atlas-M*.md` PR template
  references.
- Test coverage for previously-zero-coverage packages: `internal/store`
  (cache, derive — race-tested concurrent stress), middleware CSP
  injection regression, mnemosyne render XSS surfaces.
- `config.Config.Validate` — refuses to start when bearer mode has no
  token, basic mode lacks credentials, AuthMode is unknown, or any
  iframe-target URL contains characters that would split CSP
  directives.
- `internal/handlers/render.go` — `renderTempl(w, r, c, route)`
  helper that distinguishes client-disconnect (debug log) from genuine
  template errors (warn log). Replaces 11 sites of silent
  `//nolint:errcheck` swallowing.

### Changed

- CI lint job consolidates typecheck steps; unified required-checks
  workflow added
- Python base image bumped to `3.14.0-slim`
- Atlas CI job extended: golangci-lint, templ generate no-op check,
  Docker build, govulncheck, coverage profile artefact
- `X-Frame-Options` changed from `DENY` to `SAMEORIGIN` (Atlas embeds
  its own Grafana panels)
- CSP `frame-src` now includes `ATLAS_LOKI_URL` and optionally
  `ATLAS_NATS_DASHBOARD_URL`; iframe-target URLs validated at startup
- `dashboard/.golangci.yml` migrated to v2 schema; enables `errorlint`
  and `bodyclose` on top of the prior set; G401/G501 exclusions removed
- Go toolchain bumped from 1.24 → **1.25.8** to clear 6 stdlib CVEs
  (net/url IPv6 parse, archive/tar, encoding/asn1, etc.). Local
  govulncheck reports `No vulnerabilities found`.
- `docker-compose.yml`: removes broken wget healthcheck (distroless has
  no shell); changes `ATLAS_AUTH_MODE` default to `bearer` (operators
  must explicitly opt-out of auth in their `.env`).
- `config.PollAgamemnonMs` → `PollAgamemnon` (staticcheck ST1011: no
  unit suffix on `time.Duration`). Env var keeps `_MS` suffix.

### Fixed (additional v0.2.0)

- **Audit S3 CRITICAL #1**: NATS subscriber unwired in main.go
- **Audit S3 CRITICAL #2**: Duplicate Event types (`nats.Event` vs
  `events.Event`) — resolved via type alias
- **Audit S3 CRITICAL #3**: Metrics surface dead instrumentation —
  every counter/gauge/histogram now actually fires
- **Audit S6 CRITICAL #1**: CI `docker build` had no Dockerfile
- **Audit S6 CRITICAL #2**: `release.yml` did no docker push
- **Audit S8 CRITICAL #1**: `/metrics` and `/readyz` mounted outside
  the auth Group — anyone on the network could read Prometheus
  internals
- **Audit S8 CRITICAL #2**: Default `ATLAS_AUTH_MODE=none` with no
  startup warning
- **Audit S8 CRITICAL #3**: SSRF/CSP-injection via unvalidated
  `ATLAS_GRAFANA_URL` etc. — now validated at `Config.Validate`
- **Audit S9 CRITICAL #1**: `/healthz` and `/readyz` always returned
  200 — now `/readyz` returns 503 when components aren't ready
- **Audit S12 CRITICAL**: No Dockerfile, no release automation, image
  tag mismatch — full pipeline now ships
- Atlas SSE heartbeat data race in tests
- Atlas M2 review-wave findings (healthz plaintext, hermes port, README
  line wrap)
- CI: valid job IDs in `_required.yml` (no slashes allowed in keys)
- Dependabot: removed invalid Docker `package-ecosystem` entry
- Atlas NATS poller: `consumer_count` JSON tag was `num_consumers`
  (stream consumers always read 0)
- Atlas aggregate review script: used `.verdict` field; Agamemnon
  stores verdict in `.result`
- Atlas Grafana handler: `from`/`to` query params now validated before
  embedding in iframe URLs
- Atlas Mnemosyne handlers: nil guards added for unconfigured
  `ATLAS_MNEMOSYNE_SKILLS_DIR`; readers now log skill-load failures
- `internal/poller/poller.go` `newBase` now returns `*base` (was value
  return — silently broken because `base` contains `sync.RWMutex`)
- `internal/nats/subscriber.go` `js.Subscribe` no longer combines a
  positional subject with `ConsumerFilterSubjects` (nats.go rejects
  this combination — bug surfaced when the integration test ran)
- `internal/nats/subscriber.go` publishes to bus before acking the NATS
  message — JetStream redelivers on bus drop instead of silent loss
- gosec G306 fix in `internal/tailscale/tailscale_test.go`: WriteFile
  at 0o600 then chmod 0o700 (was 0o755)
- gosec G304 fix in `internal/mnemosyne/reader.go`: containment check
  (resolved absolute path must live under the configured skills root,
  defends against escape symlinks)
- All `resp.Body.Close()` calls now explicit `_ = resp.Body.Close()`
  (errcheck) across `internal/{catalog,poller,tailscale,handlers}`

## [0.2.0] - 2026-05-04

### Changed

- **Exporter Dockerfile**: multi-stage build, non-root `exporter` user, `HEALTHCHECK`
  instruction — addresses `[MAJOR] §12` finding (#148)
- **Metric naming**: renamed `homeric_exporter_scrape_timestamp` →
  `homeric_exporter_scrape_timestamp_seconds` to follow Prometheus naming conventions
  — addresses `[MINOR] §14` finding (#156)
- **Alert rule**: updated `ExporterScrapeStale` to reference renamed timestamp metric
- **pixi.toml**: added `test` task (`python -m pytest tests/ -v`), broadened platforms
  to include `osx-arm64`, `osx-64`, `win-64` — addresses `[MINOR] §13` (#153) and
  `[MINOR] §12` (#151)
- **justfile**: removed hardcoded `admin:admin` from `import-dashboards` (now reads
  `GF_ADMIN_PASSWORD` from `.env`); added `.env` existence check to `start` and
  `import-dashboards`; replaced `docker exec`-based `test-scrape` with
  `docker compose exec` — addresses `[MAJOR] §13` (#152) and §13 (#187)
- **CLAUDE.md**: updated stale image version table, added missing `NATS_LOG_DIR`
  environment variable, added Mermaid architecture diagram, metric naming section,
  and AI agent collaboration notes — addresses `[MAJOR] §11` (#144) and
  `[MINOR] §11` (#147)
- **SECURITY.md**: replaced GitHub no-reply address with real contact email
  — addresses `[MINOR] §15` (#159)
- **LICENSE**: updated copyright year from 2025 → 2026
  — addresses `[MINOR] §15` (#158)

### Added

- **`# HELP` lines** in exporter `/metrics` output for every metric
  — addresses `[MINOR] §14` (#155)
- **`AGENTS.md`**: multi-agent coordination protocol and permitted-change matrix
  — addresses `[MINOR] §11` (#145)
- **`CODEOWNERS`**: maps all files to `@mvillmow` with CI/security escalations
  — addresses `[MINOR] §10` (#142)
- **`.github/PULL_REQUEST_TEMPLATE.md`**: structured PR template with validation
  checklist — addresses `[MINOR] §10` (#141)

## [0.1.0] - 2026-04-23

### Added

- Prometheus scrape stack with 15s global interval
- Loki log aggregation with 30-day retention
- Promtail log shipping from container stdout and host log files
- Grafana dashboards: agent-health, nats-events, task-throughput
- Custom Python exporter (`exporter/exporter.py`) scraping ProjectAgamemnon and
  NATS HTTP APIs, exposing metrics on port 9101
- Prometheus alerting rules in `rules/agent-alerts.yml` for agent health
- Grafana Alertmanager contact point provisioning
- Docker Compose orchestration with pinned image versions
- `justfile` task runner with targets for start, stop, reload, scrape, and dashboard import
- CI workflow (yamllint + Python lint) on GitHub Actions
- CLAUDE.md project conventions and development guidelines
- CONTRIBUTING.md, SECURITY.md, LICENSE at repository root
- `.gitignore` covering `.pixi/`, IDE files, OS files, and secrets
- `pixi.toml` with locked dependencies (`just`, `jq`)

[Unreleased]: https://github.com/HomericIntelligence/Argus/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/HomericIntelligence/Argus/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/HomericIntelligence/Argus/releases/tag/v0.1.0
