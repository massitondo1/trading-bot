#!/bin/bash
# Wrapper invoked by launchd. Syncs with the cloud research repo, runs the
# deterministic executor, then pushes updated local state back so the next
# cloud research session can see current holdings and trade history.
#
# Usage: run_local_cycle.sh <session-name>   (session-name: premarket|midday|postmarket)
set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
PROJECT_DIR="/Users/massimilianotondo/trading-bot"
SESSION="${1:-manual}"
LOG_FILE="$PROJECT_DIR/.tmp/local_cycle_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$PROJECT_DIR/.tmp"
cd "$PROJECT_DIR"

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) starting session=$SESSION ==="

  git pull --quiet origin main

  source .venv/bin/activate

  # Refresh the static instrument reference weekly (cloud agent has no live
  # API access and depends on this file to resolve tickers).
  REF_FILE="data/instruments_reference.json"
  if [ ! -f "$REF_FILE" ] || [ $(( $(date +%s) - $(stat -f %m "$REF_FILE") )) -gt 604800 ]; then
    python tools/refresh_instruments_reference.py
  fi

  # Don't let a mid-run failure (e.g. a transient network error after a
  # trade already executed) skip the commit below -- whatever landed in
  # data/ needs to reach the repo regardless, or the next run starts from
  # stale state and can lose track of trades/idempotency keys that already
  # happened for real.
  set +e
  python tools/apply_recommendations.py "$SESSION"
  EXEC_STATUS=$?
  set -e

  git add data/ 2>/dev/null || true
  GIT_STATUS=0
  if ! git diff --cached --quiet; then
    set +e
    git commit -q -m "Execution cycle ($SESSION): update holdings/trade log" \
      && git pull --quiet --rebase origin main \
      && git push --quiet origin main
    GIT_STATUS=$?
    set -e
  fi

  if [ "$EXEC_STATUS" -ne 0 ] || [ "$GIT_STATUS" -ne 0 ]; then
    python tools/slack_notify.py ":rotating_light: Trading bot execution cycle FAILED (local) — session=$SESSION, exec_status=$EXEC_STATUS, git_status=$GIT_STATUS, see $LOG_FILE on $(hostname)" || true
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) FAILED session=$SESSION (exec_status=$EXEC_STATUS git_status=$GIT_STATUS) ==="
    exit 1
  fi

  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) finished session=$SESSION ==="
} >> "$LOG_FILE" 2>&1
