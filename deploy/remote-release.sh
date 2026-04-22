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

if [[ "$ROLE" != "gateway" && "$ROLE" != "worker" ]]; then
  echo "unsupported role: $ROLE" >&2
  exit 1
fi

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "archive not found: $ARCHIVE_PATH" >&2
  exit 1
fi

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

sudo systemctl restart "$SERVICE_NAME"
sudo systemctl is-active --quiet "$SERVICE_NAME"

if [[ "$ROLE" == "worker" ]]; then
  TOKEN="$(sudo awk -F= '/^TELECODEX_WORKER_TOKEN=/{print $2}' /etc/telecodex/worker.env | tr -d '\r')"
  curl -fsS -H "X-Worker-Token: $TOKEN" http://127.0.0.1:8081/health >/dev/null
fi

sudo bash -lc "cd '$RELEASES_DIR' && ls -1dt */ 2>/dev/null | tail -n +$((KEEP_RELEASES + 1)) | xargs -r rm -rf --"

echo "deployed role=$ROLE release=$RELEASE_ID service=$SERVICE_NAME"
