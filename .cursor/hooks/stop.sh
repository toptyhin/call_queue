#!/bin/bash
set -uo pipefail

log() {
  echo "$@" >&2
}

# Always emit valid JSON on stdout for Cursor stop hooks.
emit_json() {
  local followup="${1:-}"
  if [ -n "$followup" ]; then
    jq -n --arg msg "$followup" '{followup_message: $msg}'
  else
    echo '{}'
  fi
}

# True when doctor output reports real findings (not a clean scan).
has_doctor_findings() {
  local report="$1"
  if printf '%s\n' "$report" | grep -qiE 'No issues found!'; then
    return 1
  fi
  printf '%s\n' "$report" | grep -qE '(^|[[:space:]])(✖|⚠|All [1-9][0-9]* issues)'
}

input=$(cat)
status=$(echo "$input" | jq -r '.status // empty')

if [ "$status" != "completed" ]; then
  log "Stop hook: status=$status, skipping doctors."
  emit_json
  exit 0
fi

if [ "${SKIP_DOCTORS:-0}" = "1" ]; then
  log "Stop hook: SKIP_DOCTORS=1, skipping."
  emit_json
  exit 0
fi

log "Stop hook: completed, checking changed files..."

changed_files=""
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # quotepath=false: raw UTF-8 paths (иначе кириллица приходит quoted/octal и не матчится по расширению);
  # cut -c4- keeps paths with spaces intact (porcelain: XY + space + path)
  changed_files=$(git -c core.quotepath=false status --porcelain | cut -c4-)
  if [ -z "$changed_files" ]; then
    changed_files=$(git diff --name-only HEAD~1 HEAD 2>/dev/null || true)
  fi
else
  # Not a git repo yet: detect runnable targets by source presence.
  [ -d apps/web/src ] && changed_files="apps/web/src/index.tsx"
  if [ -d apps/api ] && find apps/api -name '*.py' -print -quit 2>/dev/null | grep -q .; then
    changed_files="${changed_files:+$changed_files
}apps/api/main.py"
  fi
fi

if [ -z "$changed_files" ]; then
  log "Stop hook: no changed files, skipping."
  emit_json
  exit 0
fi

reports=()

frontend_pattern='\.(js|jsx|ts|tsx)$'
if echo "$changed_files" | grep -qE "$frontend_pattern"; then
  if command -v npx >/dev/null 2>&1; then
    log "Stop hook: frontend changes detected, running react-doctor..."
    # -y: never prompt (non-interactive agent env); exit != 0 with errors = findings.
    doctor_out=$(timeout 240 npx --yes react-doctor@latest -y --no-telemetry --verbose 2>&1); rd_code=$?
    doctor_out=$(printf '%s\n' "$doctor_out" | grep -vE '^npm warn ' | head -n 80 || true)
    if [ "$rd_code" -eq 124 ]; then
      log "Stop hook: react-doctor timed out, skipping."
    elif has_doctor_findings "$doctor_out" || { [ "$rd_code" -ne 0 ] && printf '%s' "$doctor_out" | grep -qiE 'error|✖'; }; then
      reports+=("react-doctor (exit $rd_code):
$doctor_out")
    else
      log "Stop hook: react-doctor clean, no follow-up."
    fi
  else
    log "Stop hook: npx not available, skipping react-doctor."
  fi
fi

if echo "$changed_files" | grep -qE '\.py$'; then
  pd_cmd=""
  if command -v python-doctor >/dev/null 2>&1; then
    pd_cmd="python-doctor"
  elif command -v uvx >/dev/null 2>&1; then
    pd_cmd="uvx python-doctor"
  fi
  if [ -n "$pd_cmd" ]; then
    log "Stop hook: Python changes detected, running python-doctor ($pd_cmd)..."
    target="."
    [ -d apps/api ] && target="apps/api"
    # exit 1 = score below --min-score (default 50); exit 2 = regression vs cache.
    doctor_out=$(timeout 180 $pd_cmd "$target" --verbose 2>&1); pd_code=$?
    doctor_out=$(printf '%s\n' "$doctor_out" | head -n 80 || true)
    if [ "$pd_code" -eq 124 ]; then
      log "Stop hook: python-doctor timed out, skipping."
    elif [ "$pd_code" -ne 0 ] || has_doctor_findings "$doctor_out"; then
      reports+=("python-doctor (exit $pd_code):
$doctor_out")
    else
      log "Stop hook: python-doctor clean, no follow-up."
    fi
  else
    log "Stop hook: python-doctor not available (install: pipx install python-doctor), skipping."
  fi
fi

if [ ${#reports[@]} -eq 0 ]; then
  log "Stop hook: no actionable doctor findings."
  emit_json
  exit 0
fi

followup=$(printf '%s\n\n' "${reports[@]}")
followup=$(printf 'Doctor checks after agent stop:

%s
Please review findings and fix critical issues if needed.' "$followup")

log "Stop hook: doctors finished, returning followup_message."
emit_json "$followup"
exit 0
