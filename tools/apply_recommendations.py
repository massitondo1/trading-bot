"""
Deterministic local executor: reads the cloud research agent's recommendations,
applies HARD risk limits, executes what qualifies via Trading 212, logs it,
snapshots the portfolio, and notifies Slack. Holds all the real credentials --
this is the only part of the pipeline that ever touches money.

Safety model:
- 20% stop-loss is checked on every run, independent of what the cloud agent
  recommended. It cannot be skipped or overridden by a recommendation.
- Max 15% position size is enforced regardless of a recommendation's
  target_weight_pct.
- A non-stop-loss SELL that would drop total holdings below 5 is blocked
  (logged, not executed) to protect the diversification floor.
- Recommendations are processed once per generated_at timestamp -- reruns
  before new research exists are a no-op for BUY/SELL (stop-loss and
  snapshotting still run every time).

Usage:
    python tools/apply_recommendations.py
"""
import json
import math
import sys
from datetime import date
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trading212_client import Trading212Client, Trading212Error
import portfolio_tracker
import slack_notify

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESEARCH_DIR = ROOT / "research"
RECOMMENDATIONS_FILE = RESEARCH_DIR / "latest_recommendations.json"
LAST_PROCESSED_FILE = DATA_DIR / "last_processed_research.json"
CURRENT_HOLDINGS_FILE = DATA_DIR / "current_holdings.json"

MAX_POSITION_PCT = 15.0
MIN_HOLDINGS = 5
STOP_LOSS_PCT = -20.0
MIN_TRADE_GBP = 50  # skip trades smaller than this -- not worth the noise/fees


def t212_to_yahoo_ticker(ticker: str) -> str | None:
    """Best-effort mapping; unknown patterns return None so we skip rather than guess wrong."""
    if ticker.endswith("_US_EQ"):
        return ticker[:-6]
    if ticker.endswith("l_EQ"):
        return ticker[:-4] + ".L"
    return None


def get_live_price(t212_ticker: str) -> float | None:
    yahoo_ticker = t212_to_yahoo_ticker(t212_ticker)
    if not yahoo_ticker:
        return None
    hist = yf.Ticker(yahoo_ticker).history(period="5d")
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def write_current_holdings_snapshot(portfolio: list, total_value: float):
    snapshot = []
    for pos in portfolio:
        wi = pos["walletImpact"]
        snapshot.append({
            "ticker": pos["instrument"]["ticker"],
            "name": pos["instrument"]["name"],
            "quantity": pos["quantity"],
            "avg_price_paid": pos["averagePricePaid"],
            "current_price": pos["currentPrice"],
            "current_value_gbp": wi["currentValue"],
            "weight_pct": round(wi["currentValue"] / total_value * 100, 2) if total_value else 0,
            "unrealized_pl_pct": round(wi["unrealizedProfitLoss"] / wi["totalCost"] * 100, 2) if wi["totalCost"] else 0,
        })
    save_json(CURRENT_HOLDINGS_FILE, {"as_of": date.today().isoformat(), "holdings": snapshot})
    return snapshot


def check_stop_losses(client: Trading212Client, portfolio: list, session: str):
    """Unconditional -- runs every time, ignores recommendations entirely."""
    forced_sells = []
    for pos in portfolio:
        wi = pos["walletImpact"]
        if not wi["totalCost"]:
            continue
        loss_pct = wi["unrealizedProfitLoss"] / wi["totalCost"] * 100
        if loss_pct <= STOP_LOSS_PCT:
            ticker = pos["instrument"]["ticker"]
            qty = pos["quantity"]
            key = f"{date.today().isoformat()}-{session}-{ticker}-STOPLOSS"
            try:
                client.place_market_order(ticker, -qty, idempotency_key=key)
                portfolio_tracker.log_trade("SELL", ticker, qty, pos["currentPrice"],
                                             f"HARD STOP-LOSS: down {loss_pct:.1f}% from cost basis")
                forced_sells.append((ticker, loss_pct))
            except Trading212Error as e:
                slack_notify.send_message(f":rotating_light: Stop-loss SELL FAILED for {ticker}: {e}")
    return forced_sells


def process_recommendations(client: Trading212Client, portfolio: list, cash: dict, session: str):
    rec_data = load_json(RECOMMENDATIONS_FILE)
    if not rec_data:
        return {"skipped": "no recommendations file found"}

    last_processed = load_json(LAST_PROCESSED_FILE, {})
    if last_processed.get("generated_at") == rec_data.get("generated_at"):
        return {"skipped": f"already processed generated_at={rec_data.get('generated_at')}"}

    total_value = cash["total"]
    free_cash = cash["free"]
    holdings_by_ticker = {p["instrument"]["ticker"]: p for p in portfolio}
    holdings_count = len(portfolio)

    executed, skipped = [], []

    for rec in rec_data.get("recommendations", []):
        ticker = rec["ticker"]
        action = rec["action"].upper()
        reasoning = rec.get("reasoning", "")

        if action == "HOLD":
            continue

        if action == "SELL":
            pos = holdings_by_ticker.get(ticker)
            if not pos:
                skipped.append({"ticker": ticker, "reason": "recommended SELL but not currently held"})
                continue
            if holdings_count - 1 < MIN_HOLDINGS:
                skipped.append({"ticker": ticker, "reason": f"would breach {MIN_HOLDINGS}-holding diversification floor"})
                continue
            qty = pos["quantity"]
            key = f"{date.today().isoformat()}-{session}-{ticker}-SELL"
            try:
                result = client.place_market_order(ticker, -qty, idempotency_key=key)
                portfolio_tracker.log_trade("SELL", ticker, qty, pos["currentPrice"], reasoning)
                executed.append({"ticker": ticker, "action": "SELL", "quantity": qty, "reasoning": reasoning})
                holdings_count -= 1
            except Trading212Error as e:
                skipped.append({"ticker": ticker, "reason": f"order failed: {e}"})
            continue

        if action == "BUY":
            capped_weight = min(rec.get("target_weight_pct", 0), MAX_POSITION_PCT)
            target_gbp = total_value * capped_weight / 100
            current_gbp = holdings_by_ticker.get(ticker, {}).get("walletImpact", {}).get("currentValue", 0)
            incremental_gbp = min(target_gbp - current_gbp, free_cash)

            if incremental_gbp < MIN_TRADE_GBP:
                skipped.append({"ticker": ticker, "reason": f"incremental buy size £{incremental_gbp:.0f} below £{MIN_TRADE_GBP} minimum (already near target or insufficient cash)"})
                continue

            price = get_live_price(ticker)
            if price is None:
                skipped.append({"ticker": ticker, "reason": "could not resolve a live price (unmapped ticker format)"})
                continue

            currency = holdings_by_ticker.get(ticker, {}).get("instrument", {}).get("currency")
            # GBX-quoted (LSE pence) instruments: price is already pence, 1 GBP = 100 GBX
            native_budget = incremental_gbp * 100 if currency == "GBX" or ticker.endswith("l_EQ") else incremental_gbp
            # non-GBP/GBX instruments priced in USD etc need FX; approximate via GBPUSD for US tickers
            if ticker.endswith("_US_EQ"):
                fx = yf.Ticker("GBPUSD=X").history(period="5d")["Close"].iloc[-1]
                native_budget = incremental_gbp * float(fx)

            quantity = math.floor(native_budget / price)
            if quantity < 1:
                skipped.append({"ticker": ticker, "reason": "budget too small for even 1 share"})
                continue

            key = f"{date.today().isoformat()}-{session}-{ticker}-BUY"
            try:
                client.place_market_order(ticker, quantity, idempotency_key=key)
                portfolio_tracker.log_trade("BUY", ticker, quantity, price, reasoning)
                executed.append({"ticker": ticker, "action": "BUY", "quantity": quantity, "reasoning": reasoning})
                free_cash -= incremental_gbp
            except Trading212Error as e:
                skipped.append({"ticker": ticker, "reason": f"order failed: {e}"})

    save_json(LAST_PROCESSED_FILE, {"generated_at": rec_data.get("generated_at"), "processed_on": date.today().isoformat()})
    return {"executed": executed, "skipped": skipped, "session": rec_data.get("session")}


def run(session: str):
    client = Trading212Client()
    portfolio = client.get_portfolio()
    cash = client.get_cash()

    forced_sells = check_stop_losses(client, portfolio, session)
    if forced_sells:
        # re-fetch state since stop-loss sells changed it
        portfolio = client.get_portfolio()
        cash = client.get_cash()

    result = process_recommendations(client, portfolio, cash, session)

    # re-fetch final state for the snapshot/holdings file regardless of what happened above
    final_portfolio = client.get_portfolio()
    final_cash = client.get_cash()
    write_current_holdings_snapshot(final_portfolio, final_cash["total"])
    portfolio_tracker.snapshot(final_cash["total"])

    executed = result.get("executed", [])
    if forced_sells or executed:
        lines = [":chart_with_upwards_trend: *Trade cycle complete* (demo account)"]
        for ticker, loss_pct in forced_sells:
            lines.append(f"• STOP-LOSS SELL {ticker} (was down {loss_pct:.1f}%)")
        for e in executed:
            lines.append(f"• {e['action']} {e['quantity']} {e['ticker']} — {e['reasoning']}")
        if result.get("skipped"):
            lines.append(f"_{len(result['skipped'])} recommendation(s) skipped by risk gate, see data/last_processed_research.json log_")
        lines.append(f"Portfolio value: £{final_cash['total']:.2f}")
        slack_notify.send_message("\n".join(lines))
    else:
        print("No trades executed this cycle (stop-losses clear, no actionable new research). Silent by design.")

    print(json.dumps({"forced_sells": forced_sells, **result}, indent=2, default=str))


if __name__ == "__main__":
    session = sys.argv[1] if len(sys.argv) > 1 else "manual"
    run(session)
