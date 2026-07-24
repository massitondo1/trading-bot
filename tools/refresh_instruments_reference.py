"""
Refreshes data/instruments_reference.json -- a filtered snapshot of Trading
212's tradeable stock universe (plain stocks only, leveraged/short ETPs
excluded) committed to the repo so the credential-less cloud research agent
can resolve company names to exact Trading 212 tickers.

Usage:
    python tools/refresh_instruments_reference.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trading212_client import Trading212Client

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "instruments_reference.json"

EXCLUDE_MARKERS = [
    "leverage", "graniteshares", "incomeshares", "3x long", "3x short",
    "2x ", "-1x", "-3x", "short ",
]


def refresh():
    client = Trading212Client()
    instruments = client.get_instruments()

    filtered = []
    for t in instruments:
        if t.get("type") != "STOCK":
            continue
        name_lower = t.get("name", "").lower()
        if any(m in name_lower for m in EXCLUDE_MARKERS):
            continue
        filtered.append({
            "ticker": t["ticker"],
            "name": t["name"],
            "currency": t["currencyCode"],
            "isin": t.get("isin"),
        })

    OUTPUT_PATH.write_text(json.dumps(filtered, indent=1))
    print(f"{len(instruments)} -> {len(filtered)} instruments written to {OUTPUT_PATH}")


if __name__ == "__main__":
    refresh()
