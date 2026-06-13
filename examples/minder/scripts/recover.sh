#!/usr/bin/env bash
# Minder recovery CLI — backup / restore / doctor / health.
#
# Normal ops go through the running webapp's ops API. `restore-offline` works
# even when the app is degraded: it copies a host backup straight into the
# container's /data volume (then restart the container to pick it up).
#
#   ./scripts/recover.sh health
#   ./scripts/recover.sh backup
#   ./scripts/recover.sh backups
#   ./scripts/recover.sh approvals           # list repairs awaiting approval
#   ./scripts/recover.sh approve [ID]        # approve + apply a pending repair
#   ./scripts/recover.sh reject [ID]         # dismiss a pending repair
#   ./scripts/recover.sh restore [TS]        # via API (latest good if TS omitted)
#   (repairs are also approvable natively:  swarmkit review approve <id>)
#   ./scripts/recover.sh restore-offline TS  # copy host backup -> volume directly
#   ./scripts/recover.sh backup-ha           # tar the HA config volume (DR)
#   ./scripts/recover.sh restore-ha FILE     # stop HA, restore HA volume, start HA
set -euo pipefail
cd "$(dirname "$0")/.."

api() {
  local token
  token=$(docker compose exec -T minder cat /data/internal_token 2>/dev/null | tr -d '\r\n')
  curl -s -H "x-minder-internal: $token" -H "Content-Type: application/json" "$@"
  echo
}

cmd="${1:-health}"
base="http://localhost:80/api/ops"
case "$cmd" in
  health)    api "$base/health" ;;
  backups)   api "$base/backups" ;;
  backup)    api -X POST "$base/backup" ;;
  approvals) api "$base/approvals" ;;
  approve)   api -X POST -d "{\"id\":\"${2:-}\"}" "$base/approvals/approve" ;;
  reject)    api -X POST -d "{\"id\":\"${2:-}\"}" "$base/approvals/reject" ;;
  restore)   api -X POST -d "{\"ts\":\"${2:-}\"}" "$base/restore" ;;
  restore-offline)
    ts="${2:?usage: restore-offline <TS> (see ./backups/)}"
    for f in rules.json cameras.json ha_token.json; do
      if [ -f "./backups/$ts/$f" ]; then
        docker compose cp "./backups/$ts/$f" "minder:/data/$f" && echo "restored $f"
      fi
    done
    echo "done — restart to apply:  docker compose restart minder" ;;
  backup-ha)  api -X POST "$base/backup/ha" ;;
  restore-ha)
    file="${2:?usage: restore-ha <FILE> (see ./backups/ha-config-*.tar.gz)}"
    [ -f "./backups/$file" ] || { echo "no such backup: ./backups/$file"; exit 1; }
    vol=$(docker volume ls --format '{{.Name}}' | grep -E 'ha-config$' | head -1)
    [ -n "$vol" ] || { echo "ha-config volume not found"; exit 1; }
    echo "stopping Home Assistant..."; docker compose stop homeassistant
    echo "restoring $file into $vol..."
    docker run --rm -v "$vol:/v" -v "$PWD/backups:/b:ro" busybox \
      sh -c "rm -rf /v/* /v/..?* /v/.[!.]* 2>/dev/null; tar xzf /b/$file -C /v"
    echo "starting Home Assistant..."; docker compose start homeassistant ;;
  *)
    echo "usage: $0 {health|approvals|approve [ID]|reject [ID]|backup|backups|restore [TS]|restore-offline TS|backup-ha|restore-ha FILE}"
    exit 1 ;;
esac
