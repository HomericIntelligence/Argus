# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the Atlas dashboard.
#
# Stage 1 (builder): build the static binary on golang:1.24-alpine.
#   - CGO_ENABLED=0 → fully static binary suitable for distroless.
#   - -trimpath, -s -w → smaller binary, no local paths leaked.
#   - Embeds version via -ldflags.
#
# Stage 2 (runtime): distroless static-debian12 nonroot.
#   - No shell, no package manager → minimal CVE surface.
#   - USER 65532 (nonroot) by default.
#   - Listens on :3002.
#
# Build:
#   docker build -t ghcr.io/homericintelligence/atlas:dev .
#
# Run:
#   docker run --rm -p 3002:3002 \
#       -e ATLAS_AUTH_BEARER_TOKEN=… \
#       ghcr.io/homericintelligence/atlas:dev
#
ARG GO_VERSION=1.25-alpine

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM golang:${GO_VERSION} AS builder

# Build-time argument propagated from CI / git describe.
ARG VERSION=dev

WORKDIR /src

# Cache the module layer separately from the source layer so source-only
# changes don't re-download the dep graph.
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go mod download

# Now bring in the source. .dockerignore should exclude tests/, .git, etc.
COPY . .

# Build the static binary. The embedded version comes from --build-arg
# VERSION=… set by release.yml (or git-describe in CI for snapshot builds).
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux \
    go build \
        -trimpath \
        -ldflags="-s -w -X github.com/HomericIntelligence/atlas/internal/version.Version=${VERSION}" \
        -o /out/atlas \
        ./cmd/argus-dashboard

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
# distroless/static-debian12:nonroot ships glibc-free, with USER 65532 and
# /etc/passwd already populated for the nonroot user. No shell, no apt, no
# package manager — minimal CVE surface.
FROM gcr.io/distroless/static-debian12:nonroot

ARG VERSION=dev

LABEL org.opencontainers.image.title="atlas"
LABEL org.opencontainers.image.description="Unified status dashboard for the HomericIntelligence ecosystem."
LABEL org.opencontainers.image.source="https://github.com/HomericIntelligence/ProjectArgus"
LABEL org.opencontainers.image.licenses="BSD-3-Clause"
LABEL org.opencontainers.image.version="${VERSION}"

# distroless/static defaults USER to nonroot (UID 65532); make it explicit.
USER 65532:65532

# The binary needs the embedded web/static directory at runtime (templates
# are compiled into the binary via templ generate; static CSS/JS is served
# from disk). Copy both.
COPY --from=builder /out/atlas /atlas
COPY --from=builder --chown=65532:65532 /src/web/static /web/static

EXPOSE 3002

# k8s-style /livez probe (always 200 if process is up). The HEALTHCHECK is
# defined in compose; see docker-compose.yml. Distroless has no shell, so a
# binary-based healthcheck would require shipping /atlas with a -healthcheck
# subcommand — out of scope for v0.2.x. Operators using compose should
# probe :3002/livez via wget on the host side.

ENTRYPOINT ["/atlas"]
