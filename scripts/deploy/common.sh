#!/usr/bin/env bash
# Sourced by deployment scripts; never source the credential file as shell code.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
HWP_ENV_FILE=${HWP_ENV_FILE:-/opt/hwp-assistant/production.env}
HWP_ENV_FILE=$(realpath -- "$HWP_ENV_FILE")
STATE_DIR="$(dirname -- "$HWP_ENV_FILE")/state"
mkdir -p -- "$STATE_DIR"
chmod 700 -- "$STATE_DIR"
umask 077
export HWP_IMAGE_TAG=${HWP_IMAGE_TAG:-$(git -C "$ROOT" rev-parse HEAD)}
if [[ ! "$HWP_IMAGE_TAG" =~ ^[a-f0-9]{40}$ ]]; then
  echo 'HWP_IMAGE_TAG must be a full 40-character source commit SHA.' >&2
  exit 1
fi
python3 "$ROOT/scripts/deploy/config.py" "$HWP_ENV_FILE"
dc() {
  docker compose --project-name hwp-assistant-prod --project-directory "$ROOT" \
    --env-file "$HWP_ENV_FILE" -f "$ROOT/compose.deploy.yaml" "$@"
}
dc config --quiet
