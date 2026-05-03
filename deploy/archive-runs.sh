#!/usr/bin/env bash
set -euo pipefail

RUNS_DIR="${RUNS_DIR:-/var/lib/telecodex/runs}"
ARCHIVE_DIR="${ARCHIVE_DIR:-/var/lib/telecodex/run-archives}"
OLDER_THAN_DAYS="${OLDER_THAN_DAYS:-14}"
KEEP_LATEST="${KEEP_LATEST:-20}"
DELETE=false

usage() {
  cat <<'EOF'
Usage: archive-runs.sh [options]

Archive old telecodex session/run directories. Dry-run by default.

Options:
  --runs-dir PATH          Runs directory. Default: /var/lib/telecodex/runs
  --archive-dir PATH       Archive output directory. Default: /var/lib/telecodex/run-archives
  --older-than-days DAYS   Archive terminal sessions older than DAYS. Default: 14
  --keep-latest N          Always keep the latest N sessions. Default: 20
  --delete                 Write tar.gz archives and delete originals
  -h, --help               Show this help

Environment variables with the same names are also supported:
  RUNS_DIR, ARCHIVE_DIR, OLDER_THAN_DAYS, KEEP_LATEST
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs-dir)
      RUNS_DIR="${2:?missing value for --runs-dir}"
      shift 2
      ;;
    --archive-dir)
      ARCHIVE_DIR="${2:?missing value for --archive-dir}"
      shift 2
      ;;
    --older-than-days)
      OLDER_THAN_DAYS="${2:?missing value for --older-than-days}"
      shift 2
      ;;
    --keep-latest)
      KEEP_LATEST="${2:?missing value for --keep-latest}"
      shift 2
      ;;
    --delete)
      DELETE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "$OLDER_THAN_DAYS" =~ ^[0-9]+$ ]]; then
  echo "--older-than-days must be a non-negative integer" >&2
  exit 2
fi

if ! [[ "$KEEP_LATEST" =~ ^[0-9]+$ ]]; then
  echo "--keep-latest must be a non-negative integer" >&2
  exit 2
fi

SESSIONS_DIR="$RUNS_DIR/_sessions"
if [[ ! -d "$SESSIONS_DIR" ]]; then
  echo "sessions directory not found: $SESSIONS_DIR" >&2
  exit 1
fi

cutoff_epoch="$(date -u -d "$OLDER_THAN_DAYS days ago" +%s)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

session_table="$tmp_dir/sessions.tsv"
kept_ids="$tmp_dir/kept_ids.txt"
eligible_table="$tmp_dir/eligible.tsv"
: >"$session_table"
: >"$kept_ids"
: >"$eligible_table"

json_value() {
  local path="$1"
  local key="$2"
  python3 - "$path" "$key" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    payload = {}
value = payload.get(key, "") if isinstance(payload, dict) else ""
print(value if value is not None else "")
PY
}

datetime_epoch() {
  local value="$1"
  if [[ -z "$value" ]]; then
    return 1
  fi
  date -u -d "${value/Z/+00:00}" +%s 2>/dev/null
}

path_epoch() {
  stat -c '%Y' "$1"
}

is_active_state() {
  case "$1" in
    planning|executing|reviewing|waiting_user)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

while IFS= read -r -d '' session_dir; do
  session_id="$(basename "$session_dir")"
  summary_path="$session_dir/session.json"
  state=""
  updated_at=""
  if [[ -f "$summary_path" ]]; then
    state="$(json_value "$summary_path" state)"
    updated_at="$(json_value "$summary_path" updated_at)"
  fi
  updated_epoch="$(datetime_epoch "$updated_at" || true)"
  if [[ -z "$updated_epoch" ]]; then
    updated_epoch="$(path_epoch "$session_dir")"
  fi
  printf '%s\t%s\t%s\t%s\n' "$updated_epoch" "$session_id" "$state" "$session_dir" >>"$session_table"
done < <(find "$SESSIONS_DIR" -mindepth 1 -maxdepth 1 -type d -print0)

sort -rn "$session_table" >"$tmp_dir/sessions.sorted.tsv"

if [[ "$KEEP_LATEST" -gt 0 ]]; then
  head -n "$KEEP_LATEST" "$tmp_dir/sessions.sorted.tsv" | cut -f2 >"$kept_ids"
fi

while IFS=$'\t' read -r updated_epoch session_id state session_dir; do
  [[ -n "$session_id" ]] || continue
  if grep -Fxq "$session_id" "$kept_ids"; then
    continue
  fi
  if is_active_state "$state"; then
    continue
  fi
  if [[ "$updated_epoch" -ge "$cutoff_epoch" ]]; then
    continue
  fi
  printf '%s\t%s\t%s\t%s\n' "$updated_epoch" "$session_id" "$state" "$session_dir" >>"$eligible_table"
done <"$tmp_dir/sessions.sorted.tsv"

candidate_count="$(wc -l <"$session_table" | tr -d ' ')"
eligible_count="$(wc -l <"$eligible_table" | tr -d ' ')"

echo "runs_dir=$RUNS_DIR"
echo "archive_dir=$ARCHIVE_DIR"
echo "older_than_days=$OLDER_THAN_DAYS keep_latest=$KEEP_LATEST delete=$DELETE"
echo "candidates=$candidate_count eligible=$eligible_count"

if [[ "$eligible_count" -eq 0 ]]; then
  exit 0
fi

if [[ "$DELETE" == true ]]; then
  mkdir -p "$ARCHIVE_DIR"
fi

while IFS=$'\t' read -r updated_epoch session_id state session_dir; do
  archive_path="$ARCHIVE_DIR/$session_id.tar.gz"
  paths=("$session_dir")
  while IFS= read -r -d '' run_dir; do
    paths+=("$run_dir")
  done < <(find "$RUNS_DIR" -mindepth 1 -maxdepth 1 -type d -name "$session_id-run-*" -print0 | sort -z)

  echo "$session_id: archive ${#paths[@]} paths -> $archive_path"
  for path in "${paths[@]}"; do
    echo "  - ${path#$RUNS_DIR/}"
  done

  if [[ "$DELETE" != true ]]; then
    continue
  fi

  tmp_archive="$archive_path.tmp"
  rm -f "$tmp_archive"
  tar -C "$RUNS_DIR" -czf "$tmp_archive" "${paths[@]/#"$RUNS_DIR/"/}"
  mv -f "$tmp_archive" "$archive_path"
  rm -rf -- "${paths[@]}"
done <"$eligible_table"
