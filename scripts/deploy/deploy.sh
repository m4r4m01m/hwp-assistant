#!/usr/bin/env bash
# build: compile this exact checkout. pull: use the already published SHA images.
# This script never deletes volumes or resets a checkout; migrations are not reversed.
set -Eeuo pipefail
mode=${1:-build}
[[ "$mode" == build || "$mode" == pull ]] || { echo 'Usage: bash scripts/deploy/deploy.sh build|pull' >&2; exit 2; }
source "$(dirname -- "$0")/common.sh"
exec 9>"$STATE_DIR/deploy.lock"
flock -n 9 || { echo 'Another deployment/backup is running.' >&2; exit 1; }
export HWP_DEPLOY_LOCKED=1
[[ "$(uname -m)" == x86_64 ]] || { echo 'This release requires an x86_64 Ubuntu host.' >&2; exit 1; }
[[ "$HWP_IMAGE_TAG" == "$(git -C "$ROOT" rev-parse HEAD)" ]] || {
  echo 'Checkout and image SHA must match; use a clean worktree at the desired release.' >&2; exit 1;
}
git -C "$ROOT" diff --quiet && git -C "$ROOT" diff --cached --quiet || {
  echo 'Commit or preserve local edits before deployment; no files were reset.' >&2; exit 1;
}
[[ -z "$(git -C "$ROOT" ls-files --others --exclude-standard)" ]] || {
  echo 'Untracked source files found; preserve/commit them before building a release.' >&2; exit 1;
}
for volume in hwp-assistant-prod-metadata hwp-assistant-prod-documents; do
  docker volume inspect "$volume" >/dev/null || { echo 'Run scripts/deploy/init.py first.' >&2; exit 1; }
done
# Add only the new credential volume on upgrade. Never replace credentials or
# touch existing DB/files; Docker seeds its private ownership from the API image.
if ! docker volume inspect hwp-assistant-prod-codex-state >/dev/null 2>&1; then
  docker volume create --label app=hwp-assistant-prod hwp-assistant-prod-codex-state >/dev/null
fi
if [[ "$mode" == build ]]; then
  dc build api web
else
  dc pull api web
fi
for image in $(dc config --images | grep -E -- '-(api|web):'); do
  [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$HWP_IMAGE_TAG" ]] || {
    echo 'Image revision label does not match the checkout; refusing deployment.' >&2; exit 1;
  }
done
# Download the reviewed DB image without replacing a running database yet.
dc pull db
# Keep an existing DB container/image for pg_dump. On a fresh install this starts
# the pinned image before backing up the still-empty application volumes.
dc up -d --no-build --no-recreate --pull never --wait --wait-timeout 180 db
# Backup failure aborts before replacement; it restarts the previously running app.
HWP_BACKUP_RESTART=0 bash "$ROOT/scripts/deploy/backup.sh"
# A successful backup leaves app writers stopped. Only now replace the DB image.
if ! dc up -d --no-build --pull never --wait --wait-timeout 180 db; then
  echo 'Database replacement did not become healthy. Application writers remain stopped.' >&2
  echo 'Inspect DB logs and the verified backup, then explicitly recover before restarting the app.' >&2
  echo 'No automatic DB downgrade was attempted.' >&2
  exit 1
fi
if ! dc up -d --no-build --pull never --wait --wait-timeout 180 api web; then
  echo 'Deployment did not become healthy. DB/files were not deleted.' >&2
  echo 'Inspect compose logs and the latest backup. No automatic DB downgrade was attempted.' >&2
  exit 1
fi
python3 "$ROOT/scripts/deploy/smoke.py" --url http://127.0.0.1:23000 --stripped
# Record IDs only: docker inspect's full payload contains secrets and is never saved.
api_id=$(dc ps -q api); web_id=$(dc ps -q web)
python3 - "$STATE_DIR/last-success.json" "$HWP_IMAGE_TAG" \
  "$(docker inspect --format '{{.Image}}' "$api_id")" \
  "$(docker inspect --format '{{.Image}}' "$web_id")" <<'PY'
import json, os, sys, time
from pathlib import Path
path, sha, api, web = sys.argv[1:]
target = Path(path); temporary = target.with_suffix('.tmp')
temporary.write_text(json.dumps({'source_commit': sha, 'api_image_id': api, 'web_image_id': web,
                                 'verified_at': time.time(), 'check': 'local-http-smoke'}, indent=2)+'\n')
os.replace(temporary, target)
PY
printf '\nLocal deployment checks passed: %s\n' "$HWP_IMAGE_TAG"
printf 'Next: verify login/editor/save over the real Funnel HTTPS URL. Codex device login and the configured owner must be verified separately.\n'
