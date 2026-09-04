#!/usr/bin/env bash
set -euo pipefail

project=""
config=""
state_root=""
timeout_seconds="300"
max_tasks="0"
stale_minutes="60"
skip_doctor="false"
skip_final_audit="false"
final_checks=()

usage() {
  cat >&2 <<'USAGE'
Usage: run-daily-ops.sh [options]

  --project-root <path>     Project root. Defaults to the distribution root.
  --config <path>           Config file. Defaults to <project-root>/harness.toml.
  --state-root <path>       Explicit HoH state root.
  --timeout <seconds>       Per-task timeout. Default 300.
  --max-tasks <count>       Maximum tasks to run, 0 for the whole queue. Default 0.
  --stale-minutes <minutes> Stale running threshold. Default 60.
  --final-check <command>   Final-audit verification command. Repeatable.
  --skip-doctor             Do not run doctor before the queue loop.
  --skip-final-audit        Do not run the final audit even with --final-check.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-root) project="$2"; shift 2 ;;
    --config) config="$2"; shift 2 ;;
    --state-root) state_root="$2"; shift 2 ;;
    --timeout) timeout_seconds="$2"; shift 2 ;;
    --max-tasks) max_tasks="$2"; shift 2 ;;
    --stale-minutes) stale_minutes="$2"; shift 2 ;;
    --final-check) final_checks+=("$2"); shift 2 ;;
    --skip-doctor) skip_doctor="true"; shift ;;
    --skip-final-audit) skip_final_audit="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$project" ]]; then
  project="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
else
  project="$(cd "$project" && pwd -P)"
fi
launcher="$project/scripts/hoh.sh"
config="${config:-$project/harness.toml}"

if [[ ! -x "$launcher" ]]; then
  echo "Portable launcher not found or not executable: $launcher" >&2
  exit 1
fi
if [[ ! -f "$config" ]]; then
  echo "Config file not found: $config" >&2
  exit 1
fi
if ! awk -v value="$timeout_seconds" 'BEGIN { exit !(value + 0 > 0) }'; then
  echo "--timeout must be greater than 0." >&2
  exit 2
fi
if [[ ! "$max_tasks" =~ ^[0-9]+$ ]]; then
  echo "--max-tasks must be 0 or greater." >&2
  exit 2
fi
if ! awk -v value="$stale_minutes" 'BEGIN { exit !(value + 0 >= 0) }'; then
  echo "--stale-minutes must be 0 or greater." >&2
  exit 2
fi

common_args=(--config "$config" --project-root "$project")
loop_args=(queue-run-loop "${common_args[@]}")
if [[ -n "$state_root" ]]; then
  loop_args+=(--state-root "$state_root")
fi
loop_args+=(--timeout "$timeout_seconds" --max-tasks "$max_tasks" --stale-minutes "$stale_minutes")

if [[ "$skip_final_audit" != "true" && ${#final_checks[@]} -gt 0 ]]; then
  loop_args+=(--final-audit)
  for check in "${final_checks[@]}"; do
    [[ -n "$check" ]] && loop_args+=(--final-check "$check")
  done
fi

if [[ "$skip_doctor" != "true" ]]; then
  echo "HoH daily ops: doctor"
  if ! "$launcher" doctor "${common_args[@]}"; then
    echo "Doctor failed. Queue loop was not started." >&2
    exit 1
  fi
fi

echo "HoH daily ops: queue-run-loop"
exec "$launcher" "${loop_args[@]}"
