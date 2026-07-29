#!/bin/bash
# Wrapper invoked by launchd, once a week (Saturday 00:45 EEST / Friday
# 21:45 UTC), shortly after the Friday cloud weekly_wrapup routine has had
# time to write and push research/weekly_wrapup/<date>.md. Pulls that file
# and delivers it to Slack -- see tools/deliver_weekly_wrapup.py.
set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
PROJECT_DIR="/Users/massimilianotondo/trading-bot"
LOG_FILE="$PROJECT_DIR/.tmp/weekly_wrapup_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$PROJECT_DIR/.tmp"
cd "$PROJECT_DIR"

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) starting weekly wrap-up delivery ==="

  git pull --quiet origin main

  source .venv/bin/activate

  set +e
  python tools/deliver_weekly_wrapup.py
  EXEC_STATUS=$?
  set -e

  git add data/last_delivered_wrapup.json 2>/dev/null || true
  GIT_STATUS=0
  if ! git diff --cached --quiet; then
    set +e
    git commit -q -m "Weekly wrap-up delivery: mark $(date +%Y-%m-%d) as sent" \
      && git pull --quiet --rebase origin main \
      && git push --quiet origin main
    GIT_STATUS=$?
    set -e
  fi

  if [ "$EXEC_STATUS" -ne 0 ] || [ "$GIT_STATUS" -ne 0 ]; then
    python tools/slack_notify.py ":rotating_light: Weekly wrap-up delivery FAILED (local) — exec_status=$EXEC_STATUS, git_status=$GIT_STATUS, see $LOG_FILE on $(hostname)" || true
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) FAILED weekly wrap-up delivery (exec_status=$EXEC_STATUS git_status=$GIT_STATUS) ==="
    exit 1
  fi

  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) finished weekly wrap-up delivery ==="
} >> "$LOG_FILE" 2>&1
