"""
Local persistent trade log + daily portfolio snapshots, and performance
comparison against the S&P 500 (^GSPC) benchmark.

Data lives in data/ (persistent, NOT disposable like .tmp/):
    data/trade_log.csv        one row per executed trade
    data/portfolio_history.csv one row per daily snapshot (value + benchmark)

Usage as a script:
    python tools/portfolio_tracker.py log-trade BUY AAPL_US_EQ 2 195.30 "strong FCF growth, hybrid screen pick"
    python tools/portfolio_tracker.py snapshot 10432.11
    python tools/portfolio_tracker.py performance
    python tools/portfolio_tracker.py weekly-performance
    python tools/portfolio_tracker.py position-performance
"""
import json
import sys
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRADE_LOG = DATA_DIR / "trade_log.csv"
HISTORY_LOG = DATA_DIR / "portfolio_history.csv"
CURRENT_HOLDINGS_FILE = DATA_DIR / "current_holdings.json"

TRADE_COLUMNS = ["date", "action", "ticker", "quantity", "price", "reasoning"]
HISTORY_COLUMNS = ["date", "portfolio_value", "sp500_close"]


def _ensure_files():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADE_LOG.exists():
        pd.DataFrame(columns=TRADE_COLUMNS).to_csv(TRADE_LOG, index=False)
    if not HISTORY_LOG.exists():
        pd.DataFrame(columns=HISTORY_COLUMNS).to_csv(HISTORY_LOG, index=False)


def log_trade(action: str, ticker: str, quantity: float, price: float, reasoning: str, trade_date: str = None):
    _ensure_files()
    trade_date = trade_date or date.today().isoformat()
    df = pd.read_csv(TRADE_LOG)
    df.loc[len(df)] = [trade_date, action.upper(), ticker, quantity, price, reasoning]
    df.to_csv(TRADE_LOG, index=False)
    return df.iloc[-1].to_dict()


def snapshot(portfolio_value: float, snapshot_date: str = None):
    _ensure_files()
    snapshot_date = snapshot_date or date.today().isoformat()
    sp500_close = float(yf.Ticker("^GSPC").history(period="5d")["Close"].iloc[-1])
    df = pd.read_csv(HISTORY_LOG)
    df = df[df["date"] != snapshot_date]  # replace same-day snapshot if re-run
    df.loc[len(df)] = [snapshot_date, portfolio_value, sp500_close]
    df = df.sort_values("date")
    df.to_csv(HISTORY_LOG, index=False)
    return df.iloc[-1].to_dict()


def performance() -> dict:
    _ensure_files()
    df = pd.read_csv(HISTORY_LOG)
    if len(df) < 2:
        return {"error": "need at least 2 snapshots to compute performance"}

    first, last = df.iloc[0], df.iloc[-1]
    portfolio_return = (last["portfolio_value"] / first["portfolio_value"] - 1) * 100
    sp500_return = (last["sp500_close"] / first["sp500_close"] - 1) * 100

    return {
        "since": first["date"],
        "as_of": last["date"],
        "portfolio_return_pct": round(float(portfolio_return), 2),
        "sp500_return_pct": round(float(sp500_return), 2),
        "alpha_pct": round(float(portfolio_return - sp500_return), 2),
    }


def weekly_performance(days: int = 7) -> dict:
    """
    Like performance(), but windowed to the trailing N days instead of since
    inception -- used by the weekly wrap-up to report "how did this week go"
    rather than the all-time number. See workflows/weekly_wrapup.md.
    """
    _ensure_files()
    df = pd.read_csv(HISTORY_LOG)
    if df.empty:
        return {"error": "no snapshots yet"}

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    window = df[df["date"] >= cutoff]
    if len(window) < 2:
        return {"error": f"need at least 2 snapshots in the last {days} days"}

    first, last = window.iloc[0], window.iloc[-1]
    portfolio_return = (last["portfolio_value"] / first["portfolio_value"] - 1) * 100

    # yfinance occasionally returns NaN for ^GSPC on the most recent trading
    # day (data not finalized yet) -- fall back to the nearest valid close on
    # either edge of the window rather than propagating NaN into the report.
    valid_sp500 = window.dropna(subset=["sp500_close"])
    result = {
        "window_days": days,
        "since": first["date"],
        "as_of": last["date"],
        "portfolio_return_pct": round(float(portfolio_return), 2),
    }
    if len(valid_sp500) >= 2:
        sp_first, sp_last = valid_sp500.iloc[0], valid_sp500.iloc[-1]
        sp500_return = (sp_last["sp500_close"] / sp_first["sp500_close"] - 1) * 100
        result["sp500_return_pct"] = round(float(sp500_return), 2)
        result["alpha_pct"] = round(float(portfolio_return - sp500_return), 2)
    else:
        result["sp500_return_pct"] = None
        result["alpha_pct"] = None
        result["notes"] = "S&P 500 close missing/NaN for enough of this window to compute alpha"
    return result


def position_performance() -> list:
    """
    Joins data/current_holdings.json (live prices/weights) with the full
    trade_log.csv history for each ticker, so thesis-at-entry can be compared
    against what actually happened. This is the input for retrospective
    learning -- see workflows/cloud_research.md's retrospective step.
    """
    if not CURRENT_HOLDINGS_FILE.exists():
        return []
    holdings = json.loads(CURRENT_HOLDINGS_FILE.read_text())["holdings"]

    _ensure_files()
    trades = pd.read_csv(TRADE_LOG)

    results = []
    for h in holdings:
        ticker = h["ticker"]
        ticker_trades = trades[trades["ticker"] == ticker].sort_values("date")
        buys = ticker_trades[ticker_trades["action"] == "BUY"]
        first_buy_date = buys.iloc[0]["date"] if not buys.empty else None
        days_held = (date.today() - date.fromisoformat(first_buy_date)).days if first_buy_date else None

        results.append({
            "ticker": ticker,
            "first_entry_date": first_buy_date,
            "days_held": days_held,
            "avg_price_paid": h["avg_price_paid"],
            "current_price": h["current_price"],
            "return_pct": h["unrealized_pl_pct"],
            "weight_pct": h["weight_pct"],
            "trade_history": ticker_trades[["date", "action", "quantity", "price", "reasoning"]].to_dict("records"),
        })
    return results


def _main():
    import json
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "log-trade":
        action, ticker, qty, price, reasoning = sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5]), sys.argv[6]
        print(json.dumps(log_trade(action, ticker, qty, price, reasoning), indent=2, default=str))
    elif cmd == "snapshot":
        print(json.dumps(snapshot(float(sys.argv[2])), indent=2, default=str))
    elif cmd == "performance":
        print(json.dumps(performance(), indent=2, default=str))
    elif cmd == "weekly-performance":
        print(json.dumps(weekly_performance(), indent=2, default=str))
    elif cmd == "position-performance":
        print(json.dumps(position_performance(), indent=2, default=str))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    _main()
