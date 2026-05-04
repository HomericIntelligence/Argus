# TLS Setup Runbook — ProjectArgus

This document describes how to enable and maintain TLS for all inter-service communication in the argus observability stack.

## Overview

The stack uses a two-tier TLS strategy:

| Tier | Path | Mechanism |
|------|------|-----------|
| 1 (high priority) | exporter → Agamemnon/NATS/Nestor | Tailscale transport encryption (cross-host WSL2 boundary) |
| 2 (best practice) | Docker-internal services | Self-signed CA + per-service certificates |

## Quick Start

### 1. Generate certificates

```bash
just gen-certs
```

This runs `certs/gen-certs.sh`, which creates:
- `certs/ca.crt` / `certs/ca.key` — local Certificate Authority
- `certs/<service>.crt` / `certs/<service>.key` — one cert per service

Certificates are valid for 10 years. The `certs/` directory is git-ignored for `*.crt` and `*.key` — private keys must never be committed.

### 2. Start the stack

```bash
just start
```

All services mount their certificates from `certs/` via the volumes defined in `docker-compose.yml`.

### 3. Verify

```bash
just test-scrape        # Prometheus queries over HTTPS
just reload-prometheus  # Prometheus reload over HTTPS
just import-dashboards  # Grafana API calls over HTTPS
```

Open Grafana at `https://localhost:3001`. Your browser will warn about the self-signed certificate; add `certs/ca.crt` to your OS/browser trust store to suppress the warning.

## Tier 1: Cross-Host Paths (Exporter → Agamemnon / NATS)

The exporter reaches Agamemnon (`172.20.0.1:8080`) and NATS (`172.24.0.1:8222`) across the WSL2 host gateway. These paths cross a network boundary and are the highest-risk.

**Recommended approach: Tailscale**

Route these URLs through Tailscale IPs instead of raw gateway IPs. Tailscale encrypts the hop end-to-end and sidesteps the self-signed certificate distribution problem for external services.

Update `docker-compose.yml`:
```yaml
AGAMEMNON_URL: "https://<tailscale-ip-of-agamemnon-host>:8080"
NESTOR_URL:    "https://<tailscale-ip-of-nestor-host>:8081"
NATS_URL:      "https://<tailscale-ip-of-nats-host>:8222"
```

If Agamemnon/NATS serve HTTPS with our self-signed CA, also set:
```yaml
AGAMEMNON_TLS_CA: "/certs/ca.crt"
NESTOR_TLS_CA:    "/certs/ca.crt"
NATS_TLS_CA:      "/certs/ca.crt"
```

The CA file `/certs/ca.crt` is already mounted in the `argus-exporter` container.

**Fallback: Plain HTTP (current default)**

The default `AGAMEMNON_TLS_CA=""` / `NESTOR_TLS_CA=""` / `NATS_TLS_CA=""` preserves backward compatibility — the exporter uses plain HTTP as long as the upstream services don't serve HTTPS. This avoids `SSL_ERROR_RX_RECORD_TOO_LONG` errors when `https://` is pointed at an HTTP-only endpoint.

## Tier 2: Docker-Internal Paths

| Service | Certificate | Mounted at |
|---------|-------------|------------|
| Prometheus | `certs/prometheus.{crt,key}` | `/etc/prometheus/tls/` |
| Loki | `certs/loki.{crt,key}` | `/etc/loki/tls/` |
| Grafana | `certs/grafana.{crt,key}` | `/etc/grafana/tls/` |
| Promtail (client) | `certs/ca.crt` | `/etc/promtail/tls/` |

### Grafana CA cert for datasources

Grafana provisioning (`configs/grafana/datasources.yml`) includes `tlsAuthWithCACert: true` and a `secureJsonData.tlsCACert` placeholder. To inject the actual CA cert at startup, either:

**Option A — Env var injection (recommended for Docker)**

Add to `docker-compose.yml` under `grafana.environment`:
```yaml
GF_DATASOURCE_PROMETHEUS_JSONDATA_TLSCACERT: |
  <contents of certs/ca.crt>
```

Or use a startup script that patches the provisioning file:
```bash
sed -i "s|# Mount the CA cert content here.*|$(cat certs/ca.crt | sed 's/^/        /')|" \
    configs/grafana/datasources.yml
```

**Option B — Grafana UI**

After startup, navigate to each datasource in the Grafana UI and paste the CA cert content into the "TLS CA Certificate" field. Export the datasource JSON and check it in.

## Certificate Rotation

1. Remove existing certificates: `rm certs/*.crt certs/*.key certs/*.srl`
2. Regenerate: `just gen-certs`
3. Restart the stack: `just stop && just start`

Or regenerate without removing first (force mode):
```bash
bash certs/gen-certs.sh --force
just stop && just start
```

## Troubleshooting

### `SSL_ERROR_RX_RECORD_TOO_LONG`

This means `https://` was used against a service that is still serving plain HTTP. Check:
1. Is the target service configured with TLS? (Prometheus `tls_server_config`, Loki `http_tls_config`, etc.)
2. Are the certificates mounted correctly? Check `docker compose logs <service>` for TLS init errors.
3. Did `just gen-certs` complete without errors?

### Certificate not trusted in browser

Add `certs/ca.crt` to your OS trust store:
- **Ubuntu/Debian**: `sudo cp certs/ca.crt /usr/local/share/ca-certificates/argus-ca.crt && sudo update-ca-certificates`
- **macOS**: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain certs/ca.crt`
- **Windows**: Import via Certificate Manager (`certmgr.msc`) → Trusted Root Certification Authorities

### Promtail push failures after TLS

Check that Loki is serving HTTPS and that `certs/ca.crt` is present in the container:
```bash
just logs promtail
just logs loki
docker exec argus-promtail ls /etc/promtail/tls/
```
