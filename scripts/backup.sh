#!/usr/bin/env bash
set -euo pipefail

# Back up Prometheus and Loki data volumes to timestamped tar.gz files
# The owning services are stopped for the duration of the snapshot and
# restarted automatically on exit (success or failure).
# Usage: backup.sh

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
mkdir -p "$BACKUP_DIR"

declare -A VOLUME_TO_SERVICE=(
    [prometheus_data]=prometheus
    [loki_data]=loki
)

echo "Backing up Prometheus and Loki data volumes..."
echo "Backup directory: $BACKUP_DIR"

STOPPED_SERVICES=()
cleanup() {
    for svc in "${STOPPED_SERVICES[@]}"; do
        echo "Restarting $svc..."
        ${CONTAINER_CMD:-docker} compose --project-directory "$PROJECT_DIR" start "$svc"
    done
}
trap cleanup EXIT

for vol in prometheus_data loki_data; do
    out="$BACKUP_DIR/${vol}_${TIMESTAMP}.tar.gz"
    svc="${VOLUME_TO_SERVICE[$vol]:-}"
    if [[ -n "$svc" ]]; then
        echo "Stopping $svc before snapshot..."
        ${CONTAINER_CMD:-docker} compose --project-directory "$PROJECT_DIR" stop "$svc"
        STOPPED_SERVICES+=("$svc")
    else
        echo "Warning: unknown volume '$vol' — skipping stop/start lifecycle." >&2
    fi
    echo "Backing up $vol -> $out"
    ${CONTAINER_CMD:-docker} run --rm -v "${vol}:/data:ro" -v "${BACKUP_DIR}:/backups" \
        alpine tar czf "/backups/${vol}_${TIMESTAMP}.tar.gz" -C /data .
    echo "  ✓ $vol backup complete"
done

trap - EXIT

for svc in "${STOPPED_SERVICES[@]}"; do
    echo "Restarting $svc..."
    ${CONTAINER_CMD:-docker} compose --project-directory "$PROJECT_DIR" start "$svc"
done
STOPPED_SERVICES=()

echo ""
echo "Backup complete: $BACKUP_DIR"
echo "Files:"
ls -lh "${BACKUP_DIR}/${TIMESTAMP:0:8}"* 2>/dev/null || echo "  (no backups found with today's date)"
