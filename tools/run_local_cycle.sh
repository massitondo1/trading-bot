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

  python tools/apply_recommendations.py "$SESSION"

  git add data/ 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -q -m "Local execution cycle ($SESSION): update holdings/trade log"
    git push --quiet origin main
  fi

  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) finished session=$SESSION ==="
} >> "$LOG_FILE" 2>&1
