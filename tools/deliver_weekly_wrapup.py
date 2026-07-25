"""
Local delivery step for the weekly wrap-up: finds the latest file the cloud
weekly_wrapup agent wrote to research/weekly_wrapup/, posts it to Slack, and
records what it sent so re-runs (e.g. a launchd retry) don't double-post.

This does no reasoning of its own -- the wrap-up content is entirely written
by the cloud agent per workflows/weekly_wrapup.md; this script only delivers
it, same split as apply_recommendations.py does for trades.

Usage as a script:
    python tools/deliver_weekly_wrapup.py
"""
import json
import sys
from pathlib import Path

import slack_notify

ROOT = Path(__file__).resolve().parent.parent
WRAPUP_DIR = ROOT / "research" / "weekly_wrapup"
LAST_DELIVERED_FILE = ROOT / "data" / "last_delivered_wrapup.json"

SLACK_CHUNK_SIZE = 3500  # keep individual Slack messages readable, not hit the 40k hard cap


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def latest_wrapup_file() -> Path | None:
    if not WRAPUP_DIR.exists():
        return None
    files = sorted(WRAPUP_DIR.glob("*.md"))
    return files[-1] if files else None


def chunk_text(text: str, size: int) -> list:
    """Split on paragraph breaks where possible so chunks don't cut mid-sentence."""
    chunks = []
    remaining = text
    while len(remaining) > size:
        split_at = remaining.rfind("\n\n", 0, size)
        if split_at == -1:
            split_at = size
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def deliver():
    wrapup_file = latest_wrapup_file()
    if not wrapup_file:
        return {"skipped": "no weekly wrap-up file found in research/weekly_wrapup/"}

    last_delivered = load_json(LAST_DELIVERED_FILE, {})
    if last_delivered.get("file") == wrapup_file.name:
        return {"skipped": f"already delivered {wrapup_file.name}"}

    content = wrapup_file.read_text()
    chunks = chunk_text(content, SLACK_CHUNK_SIZE)
    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        prefix = f"*Weekly Wrap-Up ({wrapup_file.stem}) -- part {i}/{total}*\n\n" if total > 1 else ""
        slack_notify.send_message(prefix + chunk)

    LAST_DELIVERED_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_DELIVERED_FILE.write_text(json.dumps({"file": wrapup_file.name}, indent=2))
    return {"delivered": wrapup_file.name, "chunks": total}


if __name__ == "__main__":
    result = deliver()
    print(json.dumps(result, indent=2))
    if "skipped" in result:
        sys.exit(0)
