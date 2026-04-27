#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:?usage: remote-release.sh <gateway|worker> <archive-path> [release-id]}"
ARCHIVE_PATH="${2:?usage: remote-release.sh <gateway|worker> <archive-path> [release-id]}"
RELEASE_ID="${3:-manual-$(date -u +%Y%m%d%H%M%S)}"

APP_ROOT="${APP_ROOT:-/opt/telecodex}"
APP_USER="${APP_USER:-telecodex}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
VENV_PATH="${VENV_PATH:-$APP_ROOT/venv}"
RELEASES_DIR="${RELEASES_DIR:-$APP_ROOT/releases}"
CURRENT_PATH="${CURRENT_PATH:-$APP_ROOT/current}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
SERVICE_NAME="${SERVICE_NAME:-telecodex-$ROLE}"
CONFIG_DIR="${CONFIG_DIR:-/etc/telecodex}"

config_paths_for_role() {
  case "$ROLE" in
    gateway)
      CONFIG_PATH="$CONFIG_DIR/gateway.yaml"
      ENV_PATH="$CONFIG_DIR/gateway.env"
      ;;
    worker)
      CONFIG_PATH="$CONFIG_DIR/worker.yaml"
      ENV_PATH="$CONFIG_DIR/worker.env"
      ;;
  esac
}

normalize_runtime_config_permissions() {
  if [[ -f "$CONFIG_PATH" ]]; then
    sudo chown "root:$APP_GROUP" "$CONFIG_PATH"
    sudo chmod 640 "$CONFIG_PATH"
  fi

  if [[ -f "$ENV_PATH" ]]; then
    sudo chown root:root "$ENV_PATH"
    sudo chmod 600 "$ENV_PATH"
  fi
}

migrate_worker_execution_policy() {
  if [[ "$ROLE" != "worker" || ! -f "$CONFIG_PATH" ]]; then
    return 0
  fi

  sudo CONFIG_PATH="$CONFIG_PATH" "$VENV_PATH/bin/python" <<'PY'
from __future__ import annotations

import os
from pathlib import Path

import yaml

config_path = Path(os.environ["CONFIG_PATH"])
payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
if not isinstance(payload, dict):
    raise SystemExit(0)

policy = payload.get("execution_policy")
if not isinstance(policy, dict):
    policy = {}

allow_commands = policy.get("allow_commands")
legacy_restrictive_allowlist = {"git", "python", "pytest"}
if isinstance(allow_commands, list) and set(map(str, allow_commands)) <= legacy_restrictive_allowlist:
    # Older single-node installs generated an allowlist that blocked normal development commands
    # such as mkdir, ls, npm, and shell helpers. Migrate only that known restrictive default; leave
    # deliberate custom allowlists untouched.
    policy["allow_commands"] = []
    policy.setdefault("deny_commands", ["rm", "del"])
    payload["execution_policy"] = policy
    config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
PY
}

wait_for_worker_health() {
  local token="$1"
  local attempts="${2:-15}"

  for _ in $(seq 1 "$attempts"); do
    if curl -fsS -H "X-Worker-Token: $token" http://127.0.0.1:8081/health >/dev/null; then
      return 0
    fi
    sleep 2
  done

  return 1
}

if [[ "$ROLE" != "gateway" && "$ROLE" != "worker" ]]; then
  echo "unsupported role: $ROLE" >&2
  exit 1
fi

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "archive not found: $ARCHIVE_PATH" >&2
  exit 1
fi

config_paths_for_role

TARGET_RELEASE="$RELEASES_DIR/$RELEASE_ID"
NEXT_LINK="$APP_ROOT/current.next"

sudo install -d -m 755 "$APP_ROOT"
sudo install -d -m 755 "$RELEASES_DIR"
sudo rm -rf "$TARGET_RELEASE"
sudo install -d -m 755 "$TARGET_RELEASE"

sudo tar -xzf "$ARCHIVE_PATH" -C "$TARGET_RELEASE"
sudo chown -R "$APP_USER:$APP_GROUP" "$TARGET_RELEASE"

if [[ -d "$CURRENT_PATH" && ! -L "$CURRENT_PATH" ]]; then
  sudo mv "$CURRENT_PATH" "$APP_ROOT/legacy-current-$RELEASE_ID"
fi

sudo ln -sfn "$TARGET_RELEASE" "$NEXT_LINK"
sudo mv -Tf "$NEXT_LINK" "$CURRENT_PATH"
sudo chown -h "$APP_USER:$APP_GROUP" "$CURRENT_PATH"

sudo -u "$APP_USER" -H "$VENV_PATH/bin/python" -m pip install --disable-pip-version-check -e "$CURRENT_PATH"
migrate_worker_execution_policy
normalize_runtime_config_permissions

sudo systemctl restart "$SERVICE_NAME"
sudo systemctl is-active --quiet "$SERVICE_NAME"

if [[ "$ROLE" == "worker" ]]; then
  TOKEN="$(sudo awk -F= '/^TELECODEX_WORKER_TOKEN=/{print $2}' /etc/telecodex/worker.env | tr -d '\r')"
  wait_for_worker_health "$TOKEN"
fi

sudo bash -lc "cd '$RELEASES_DIR' && ls -1dt */ 2>/dev/null | tail -n +$((KEEP_RELEASES + 1)) | xargs -r rm -rf --"

echo "deployed role=$ROLE release=$RELEASE_ID service=$SERVICE_NAME"
