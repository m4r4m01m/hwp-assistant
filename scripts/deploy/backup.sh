#!/usr/bin/env bash
# Stop app writers; dump DB and files at one consistent application boundary.
set -Eeuo pipefail
source "$(dirname -- "$0")/common.sh"
if [[ "${HWP_DEPLOY_LOCKED:-0}" != 1 ]]; then
  exec 9>"$STATE_DIR/deploy.lock"
  flock -n 9 || { echo 'Another deployment/backup is running.' >&2; exit 1; }
fi
[[ -n "$(dc ps --status running -q db)" ]] || { echo 'DB must be running for backup.' >&2; exit 1; }
mapfile -t running < <(dc ps --status running --services | grep -E '^(api|web)$' || true)
backup_root="$(dirname -- "$HWP_ENV_FILE")/backups"
mkdir -p -- "$backup_root"
chmod 700 -- "$backup_root"
work=$(mktemp -d "$backup_root/.partial-XXXXXXXX")
completed=0
resume() {
  local result=$?
  if (( ${#running[@]} )) && { [[ "${HWP_BACKUP_RESTART:-1}" == 1 ]] || (( ! completed )); }; then
    dc start "${running[@]}" >/dev/null || true
  fi
  if (( result )); then echo "Backup failed; incomplete files remain at $work" >&2; fi
}
trap resume EXIT
dc stop -t 40 web api
# PGDATA itself must never be tarred while PostgreSQL is running.
dc exec -T db sh -c 'pg_dump --format=custom --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$work/database.dump"
# Use the image of an existing app container when available, not a mutable tag.
api_id=$(dc ps -a -q api)
if [[ -n "$api_id" ]]; then
  image=$(docker inspect --format '{{.Image}}' "$api_id")
else
  image=$(dc config --images | grep -- '-api:' | head -n1)
fi
[[ -n "$image" ]] || { echo 'No API image available for file backup.' >&2; exit 1; }
docker run --rm --network none --read-only \
  --mount type=volume,src=hwp-assistant-prod-documents,dst=/data/files,readonly \
  --entrypoint tar "$image" -C /data/files -czf - . > "$work/documents.tar.gz"
dc exec -T db pg_restore --list < "$work/database.dump" >/dev/null
tar -tzf "$work/documents.tar.gz" >/dev/null
(cd "$work" && sha256sum database.dump documents.tar.gz > SHA256SUMS)
printf 'source_commit=%s\ncreated_utc=%s\n' "$HWP_IMAGE_TAG" "$(date -u +%FT%TZ)" > "$work/receipt.txt"
if [[ -f "$STATE_DIR/last-success.json" ]]; then cp -- "$STATE_DIR/last-success.json" "$work/previous-deployment.json"; fi
destination="$backup_root/$(date -u +%Y%m%dT%H%M%SZ)-${HWP_IMAGE_TAG:0:12}-$$"
mv -- "$work" "$destination"
completed=1
printf 'Backup complete: %s\n' "$destination"
