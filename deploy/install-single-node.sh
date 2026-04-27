#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  sudo bash ./deploy/install-single-node.sh \
    --telegram-token "<telegram-bot-token>" \
    --allowed-user-id "<telegram-user-id>" \
    [--allowed-user-id "<another-user-id>"] \
    [--runtime-user "<linux-user>"] \
    [--app-root "/opt/telecodex"] \
    [--config-root "/etc/telecodex"] \
    [--workspace-root "/opt/telecodex/current"] \
    [--worker-token "<shared-worker-token>"] \
    [--gemini-model "gemini-2.5-flash"] \
    [--python-bin "python3"] \
    [--no-start]

This installer bootstraps telecodex itself on a single Linux server.
It does NOT install Codex CLI or Gemini CLI for you.

Prerequisites:
  - Linux host with systemd
  - Python 3.10+
  - `codex` installed and already authenticated for the runtime user
  - `gemini` installed and already authenticated for the runtime user
    or GEMINI_API_KEY exported before running this script
EOF
}

log() {
  printf '[telecodex-install] %s\n' "$*"
}

fail() {
  printf '[telecodex-install] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

python_token() {
  "$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_hex(24))
PY
}

python_user_home() {
  "$PYTHON_BIN" - "$1" <<'PY'
import pwd
import sys
print(pwd.getpwnam(sys.argv[1]).pw_dir)
PY
}

yaml_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

run_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

run_root_shell() {
  if [[ "${EUID}" -eq 0 ]]; then
    bash -lc "$1"
  else
    sudo bash -lc "$1"
  fi
}

run_as_runtime_shell() {
  if [[ "$(id -un)" == "$RUNTIME_USER" ]]; then
    bash -lc "$1"
  else
    sudo -u "$RUNTIME_USER" -H bash -lc "$1"
  fi
}

APP_ROOT="/opt/telecodex"
CONFIG_ROOT="/etc/telecodex"
PYTHON_BIN="python3"
GEMINI_MODEL="gemini-2.5-flash"
RUNTIME_USER="${SUDO_USER:-$USER}"
WORKSPACE_ROOT=""
WORKER_TOKEN="${TELECODEX_WORKER_TOKEN:-}"
TELEGRAM_TOKEN="${TELECODEX_TELEGRAM_TOKEN:-}"
NO_START=0
ALLOWED_USER_IDS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-root)
      APP_ROOT="$2"
      shift 2
      ;;
    --config-root)
      CONFIG_ROOT="$2"
      shift 2
      ;;
    --runtime-user)
      RUNTIME_USER="$2"
      shift 2
      ;;
    --workspace-root)
      WORKSPACE_ROOT="$2"
      shift 2
      ;;
    --worker-token)
      WORKER_TOKEN="$2"
      shift 2
      ;;
    --telegram-token)
      TELEGRAM_TOKEN="$2"
      shift 2
      ;;
    --allowed-user-id)
      ALLOWED_USER_IDS+=("$2")
      shift 2
      ;;
    --gemini-model)
      GEMINI_MODEL="$2"
      shift 2
      ;;
    --python-bin)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --no-start)
      NO_START=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CURRENT_PATH="$APP_ROOT/current"
VENV_PATH="$APP_ROOT/venv"
RUNS_DIR="$APP_ROOT/.runs-single-node"
WORKER_CONFIG_PATH="$CONFIG_ROOT/worker.single-node.yaml"
GATEWAY_CONFIG_PATH="$CONFIG_ROOT/gateway.single-node.yaml"
WORKER_ENV_PATH="$CONFIG_ROOT/worker.env"
GATEWAY_ENV_PATH="$CONFIG_ROOT/gateway.env"
WORKER_SERVICE_PATH="/etc/systemd/system/telecodex-worker.service"
GATEWAY_SERVICE_PATH="/etc/systemd/system/telecodex-gateway.service"

[[ -d "$REPO_ROOT/src/telecodex" ]] || fail "run this installer from the telecodex repository checkout"
[[ "$(uname -s)" == "Linux" ]] || fail "single-node bootstrap currently supports Linux hosts with systemd only"
[[ -n "$TELEGRAM_TOKEN" ]] || fail "--telegram-token is required"
[[ ${#ALLOWED_USER_IDS[@]} -gt 0 ]] || fail "at least one --allowed-user-id is required"
if [[ -z "$WORKSPACE_ROOT" ]]; then
  WORKSPACE_ROOT="$CURRENT_PATH"
fi

if [[ "${EUID}" -ne 0 ]]; then
  require_command sudo
fi
require_command systemctl
require_command tar
if [[ "$PYTHON_BIN" == */* ]]; then
  [[ -x "$PYTHON_BIN" ]] || fail "python binary is not executable: $PYTHON_BIN"
else
  require_command "$PYTHON_BIN"
fi

id "$RUNTIME_USER" >/dev/null 2>&1 || fail "runtime user does not exist: $RUNTIME_USER"
RUNTIME_GROUP="$(id -gn "$RUNTIME_USER")"
RUNTIME_HOME="$(python_user_home "$RUNTIME_USER")"

log "Checking Codex and Gemini prerequisites for runtime user: $RUNTIME_USER"
CODEX_BIN="$(run_as_runtime_shell 'command -v codex' || true)"
[[ -n "$CODEX_BIN" ]] || fail "codex CLI is not installed for user $RUNTIME_USER"
GEMINI_BIN="$(run_as_runtime_shell 'command -v gemini' || true)"
[[ -n "$GEMINI_BIN" ]] || fail "gemini CLI is not installed for user $RUNTIME_USER"

run_as_runtime_shell "'$CODEX_BIN' login status >/dev/null" || fail "codex login status failed for user $RUNTIME_USER"

GEMINI_ENV_MODE="settings"
if [[ -n "${GEMINI_API_KEY:-}" ]]; then
  GEMINI_ENV_MODE="api_key"
else
  run_as_runtime_shell "test -f '$RUNTIME_HOME/.gemini/settings.json'" || fail \
    "gemini authentication is missing for user $RUNTIME_USER; run gemini login first or export GEMINI_API_KEY before installing"
fi

if [[ -z "$WORKER_TOKEN" ]]; then
  WORKER_TOKEN="$(python_token)"
fi

log "Preparing application directories under $APP_ROOT"
run_root install -d -m 755 "$APP_ROOT" "$CONFIG_ROOT"
run_root rm -rf "$CURRENT_PATH"
run_root install -d -m 755 "$CURRENT_PATH" "$RUNS_DIR"

TAR_EXCLUDES=(
  --exclude=.git
  --exclude=.venv
  --exclude=.pytest_cache
  --exclude=.mypy_cache
  --exclude=.ruff_cache
  --exclude=.runs-python
  --exclude=.runs-single-node
  --exclude=dist
  --exclude=src/telecodex.egg-info
  --exclude=__pycache__
)

tar "${TAR_EXCLUDES[@]}" -cf - -C "$REPO_ROOT" . | run_root tar -xf - -C "$CURRENT_PATH"
run_root chown -R "$RUNTIME_USER:$RUNTIME_GROUP" "$CURRENT_PATH" "$RUNS_DIR"

log "Creating virtual environment at $VENV_PATH"
run_root "$PYTHON_BIN" -m venv "$VENV_PATH"
run_root chown -R "$RUNTIME_USER:$RUNTIME_GROUP" "$VENV_PATH"
run_as_runtime_shell "'$VENV_PATH/bin/python' -m pip install --disable-pip-version-check --upgrade pip"
run_as_runtime_shell "'$VENV_PATH/bin/python' -m pip install --disable-pip-version-check -e '$CURRENT_PATH'"

WORKSPACE_ROOT_ESCAPED="$(yaml_escape "$WORKSPACE_ROOT")"
RUNS_DIR_ESCAPED="$(yaml_escape "$RUNS_DIR")"
CODEX_BIN_ESCAPED="$(yaml_escape "$CODEX_BIN")"
GEMINI_BIN_ESCAPED="$(yaml_escape "$GEMINI_BIN")"
GEMINI_MODEL_ESCAPED="$(yaml_escape "$GEMINI_MODEL")"

ALLOWED_USER_LINES=""
for user_id in "${ALLOWED_USER_IDS[@]}"; do
  [[ "$user_id" =~ ^[0-9]+$ ]] || fail "allowed user id must be numeric: $user_id"
  ALLOWED_USER_LINES="${ALLOWED_USER_LINES}"$'\n'"  - $user_id"
done

log "Writing single-node config files"
run_root_shell "cat > '$WORKER_CONFIG_PATH' <<EOF
host: \"127.0.0.1\"
port: 8081
workspace_root: \"$WORKSPACE_ROOT_ESCAPED\"
runs_dir: \"$RUNS_DIR_ESCAPED\"
max_turns: 6
max_turns_cap: 6
dynamic_turn_budget: false
max_codex_failures: 2
dry_run: false
print_io: false
worker_token: \"\"
execution_policy:
  allow_commands:
    - git
    - python
    - pytest
  deny_commands:
    - rm
    - del
gemini:
  protocol: \"gemini_cli\"
  command: \"$GEMINI_BIN_ESCAPED\"
  args: []
  model: \"$GEMINI_MODEL_ESCAPED\"
  timeout_sec: 120
  retries: 1
codex:
  protocol: \"codex_app_server\"
  command: \"$CODEX_BIN_ESCAPED\"
  args: []
  model: \"\"
  timeout_sec: 900
  retries: 0
  default_commands:
    - \"pytest\"
EOF"

run_root_shell "cat > '$GATEWAY_CONFIG_PATH' <<EOF
channel_provider: \"telegram\"
telegram_token: \"\"
allowed_user_ids:${ALLOWED_USER_LINES}
worker_base_url: \"http://127.0.0.1:8081\"
worker_token: \"\"
poll_timeout_sec: 30
request_timeout_sec: 30
session_push_interval_sec: 5
EOF"

run_root_shell "cat > '$WORKER_ENV_PATH' <<EOF
TELECODEX_WORKER_TOKEN=$WORKER_TOKEN
PYTHONUNBUFFERED=1
EOF"

if [[ "$GEMINI_ENV_MODE" == "api_key" ]]; then
  GEMINI_API_KEY_ESCAPED="$(printf '%s' "$GEMINI_API_KEY" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  run_root_shell "cat >> '$WORKER_ENV_PATH' <<EOF
GEMINI_API_KEY=$GEMINI_API_KEY_ESCAPED
EOF"
fi

TELEGRAM_TOKEN_ESCAPED="$(printf '%s' "$TELEGRAM_TOKEN" | sed 's/\\/\\\\/g; s/"/\\"/g')"
run_root_shell "cat > '$GATEWAY_ENV_PATH' <<EOF
TELECODEX_TELEGRAM_TOKEN=$TELEGRAM_TOKEN_ESCAPED
TELECODEX_WORKER_TOKEN=$WORKER_TOKEN
PYTHONUNBUFFERED=1
EOF"

run_root chown "root:$RUNTIME_GROUP" "$WORKER_CONFIG_PATH" "$GATEWAY_CONFIG_PATH"
run_root chown root:root "$WORKER_ENV_PATH" "$GATEWAY_ENV_PATH"
run_root chmod 640 "$WORKER_CONFIG_PATH" "$GATEWAY_CONFIG_PATH"
run_root chmod 600 "$WORKER_ENV_PATH" "$GATEWAY_ENV_PATH"

log "Installing systemd service units"
run_root_shell "cat > '$WORKER_SERVICE_PATH' <<EOF
[Unit]
Description=telecodex single-node worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUNTIME_USER
Group=$RUNTIME_GROUP
WorkingDirectory=$CURRENT_PATH
Environment=HOME=$RUNTIME_HOME
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/bin
EnvironmentFile=$WORKER_ENV_PATH
ExecStart=$VENV_PATH/bin/python -m telecodex.worker.main --config $WORKER_CONFIG_PATH
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF"

run_root_shell "cat > '$GATEWAY_SERVICE_PATH' <<EOF
[Unit]
Description=telecodex single-node gateway
After=network-online.target telecodex-worker.service
Wants=network-online.target telecodex-worker.service

[Service]
Type=simple
User=$RUNTIME_USER
Group=$RUNTIME_GROUP
WorkingDirectory=$CURRENT_PATH
Environment=HOME=$RUNTIME_HOME
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/bin
EnvironmentFile=$GATEWAY_ENV_PATH
ExecStart=$VENV_PATH/bin/python -m telecodex.gateway.main --config $GATEWAY_CONFIG_PATH
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF"

run_root systemctl daemon-reload
run_root systemctl enable telecodex-worker.service telecodex-gateway.service

if [[ "$NO_START" -eq 0 ]]; then
  log "Starting telecodex services"
  run_root systemctl restart telecodex-worker.service
  run_root systemctl restart telecodex-gateway.service
  run_root systemctl is-active --quiet telecodex-worker.service
  run_root systemctl is-active --quiet telecodex-gateway.service

  "$VENV_PATH/bin/python" - <<PY
import sys
import time
import urllib.error
import urllib.request

token = "$WORKER_TOKEN"
request = urllib.request.Request(
    "http://127.0.0.1:8081/health",
    headers={"X-Worker-Token": token},
)
for _ in range(20):
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status == 200:
                sys.exit(0)
    except (urllib.error.URLError, TimeoutError):
        time.sleep(1)
sys.exit("worker health check failed after install")
PY
fi

log "Single-node bootstrap complete"
log "Gateway config: $GATEWAY_CONFIG_PATH"
log "Worker config:  $WORKER_CONFIG_PATH"
log "Worker token stored in: $WORKER_ENV_PATH"
log "Services: telecodex-worker.service, telecodex-gateway.service"
