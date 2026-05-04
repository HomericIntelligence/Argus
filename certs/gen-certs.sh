#!/usr/bin/env bash
# Generate a self-signed CA and per-service TLS certificates for the argus stack.
# Run once before `just start`. Certificates are written to this directory.
# Re-running is idempotent: existing certs are skipped unless --force is passed.
set -euo pipefail

CERTS_DIR="$(cd "$(dirname "$0")" && pwd)"
FORCE="${1:-}"

SERVICES=(prometheus loki grafana promtail argus-exporter)
DAYS=3650

# Subject Alternative Names per service (hostname inside Docker + localhost)
declare -A SANS
SANS[prometheus]="DNS:prometheus,DNS:argus-prometheus,DNS:localhost,IP:127.0.0.1"
SANS[loki]="DNS:loki,DNS:argus-loki,DNS:localhost,IP:127.0.0.1"
SANS[grafana]="DNS:grafana,DNS:argus-grafana,DNS:localhost,IP:127.0.0.1"
SANS[promtail]="DNS:promtail,DNS:argus-promtail,DNS:localhost,IP:127.0.0.1"
SANS[argus-exporter]="DNS:argus-exporter,DNS:localhost,IP:127.0.0.1"

cd "$CERTS_DIR"

# ── CA ─────────────────────────────────────────────────────────────────────────
if [[ -f ca.crt && -z "$FORCE" ]]; then
    echo "[skip] CA already exists (pass --force to regenerate)"
else
    echo "[gen] Generating CA key and certificate..."
    openssl genrsa -out ca.key 4096
    openssl req -new -x509 -days "$DAYS" -key ca.key -out ca.crt \
        -subj "/CN=argus-local-ca/O=ProjectArgus/OU=HomericIntelligence"
    echo "[ok]  CA generated: ca.crt"
fi

# ── Per-service certs ──────────────────────────────────────────────────────────
for svc in "${SERVICES[@]}"; do
    if [[ -f "${svc}.crt" && -z "$FORCE" ]]; then
        echo "[skip] ${svc}.crt already exists"
        continue
    fi

    echo "[gen] Generating cert for ${svc}..."
    openssl genrsa -out "${svc}.key" 2048

    # Write SAN extension to a temp file
    san_ext=$(mktemp)
    cat > "$san_ext" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions     = v3_req
prompt             = no

[req_distinguished_name]
CN = ${svc}
O  = ProjectArgus

[v3_req]
subjectAltName = ${SANS[$svc]}
EOF

    openssl req -new -key "${svc}.key" -out "${svc}.csr" -config "$san_ext"
    openssl x509 -req -days "$DAYS" \
        -in "${svc}.csr" \
        -CA ca.crt -CAkey ca.key -CAcreateserial \
        -out "${svc}.crt" \
        -extfile "$san_ext" -extensions v3_req
    rm -f "$san_ext" "${svc}.csr"
    echo "[ok]  ${svc}.crt generated"
done

echo ""
echo "Certificate generation complete."
echo "Files written to: $CERTS_DIR"
echo ""
echo "Next steps:"
echo "  1. Run 'just start' to bring up the stack with TLS enabled."
echo "  2. Trust ca.crt in your browser/OS to avoid certificate warnings."
echo "  3. See docs/tls-setup.md for full runbook."
