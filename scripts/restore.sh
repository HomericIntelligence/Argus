#!/usr/bin/env bash
set -euo pipefail

# Restore a Docker volume from a backup file
# Usage: restore.sh <volume_name> <backup_file.tar.gz>
# Example: restore.sh prometheus_data ./backups/prometheus_data_20260422_193000.tar.gz

if [[ $# -lt 2 ]]; then
    echo "Usage: restore.sh <volume_name> <backup_file.tar.gz>"
    echo ""
    echo "Example:"
    echo "  restore.sh prometheus_data ./backups/prometheus_data_20260422_193000.tar.gz"
    echo "  restore.sh loki_data ./backups/loki_data_20260422_193000.tar.gz"
    exit 1
fi

VOLUME="$1"
FILE="$2"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Portable volume→service mapping (see backup.sh: no bash-4 keyed arrays —
# macOS ships Bash 3.2 and pixi-matrix runs on osx-arm64).
volume_to_service() {
    case "$1" in
        prometheus_data) printf 'prometheus' ;;
        loki_data) printf 'loki' ;;
        grafana_data) printf 'grafana' ;;
        *) return 1 ;;
    esac
}

SERVICE=""
if svc="$(volume_to_service "$VOLUME")"; then
    SERVICE="$svc"
fi

if [[ ! -f "$FILE" ]]; then
    echo "Error: Backup file not found: $FILE"
    exit 1
fi

if [[ -n "$SERVICE" ]]; then
    echo "Stopping $SERVICE before restore..."
    docker compose --project-directory "$PROJECT_DIR" stop "$SERVICE"
    # shellcheck disable=SC2064
    trap "echo 'Restarting $SERVICE...'; docker compose --project-directory '$PROJECT_DIR' start '$SERVICE'" EXIT
else
    echo "Warning: unknown volume '$VOLUME' — ensure the stack is stopped before restoring."
fi

echo "Restoring $VOLUME from $FILE ..."
${CONTAINER_CMD:-docker} run --rm -v "${VOLUME}:/data" -v "$(realpath "$FILE"):/backup.tar.gz:ro" \
    alpine sh -c "cd /data && tar xzf /backup.tar.gz"
echo "✓ Restore complete: $VOLUME"
