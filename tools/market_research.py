"""
Market research data fetcher (no API key required -- uses Yahoo Finance via yfinance).

Provides the raw data the agent reasons over: fundamentals, price history,
technicals, and recent news headlines. This tool does NOT make buy/sell
decisions -- it just fetches and computes deterministic numbers so the
analysis step has real data to work from.

Usage as a script:
    python tools/market_research.py AAPL
    python tools/market_research.py AAPL MSFT NVDA
"""
import sys
import json

import yfinance as yf
import pandas as pd


def get_fundamentals(ticker: str) -> dict:
    info = yf.Ticker(ticker).info
    keys = [
        "shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE",
        "priceToBook", "profitMargins", "revenueGrowth", "earningsGrowth",
        "returnOnEquity", "debtToEquity", "freeCashflow", "dividendYield",
        "currentPrice", "targetMeanPrice", "recommendationKey",
    ]
    return {k: info.get(k) for k in keys}


def get_price_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    return yf.Ticker(ticker).history(period=period)


def compute_technicals(ticker: str) -> dict:
    hist = get_price_history(ticker, period="1y")
    if hist.empty:
        return {"error": "no price history"}

    close = hist["Close"]
    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    rsi14 = (100 - (100 / (1 + rs))).iloc[-1]

    last_close = close.iloc[-1]
    high_52w = close.max()
    low_52w = close.min()

    return {
        "last_close": round(float(last_close), 2),
        "sma50": round(float(sma50), 2) if pd.notna(sma50) else None,
        "sma200": round(float(sma200), 2) if sma200 is not None and pd.notna(sma200) else None,
        "rsi14": round(float(rsi14), 1) if pd.notna(rsi14) else None,
        "pct_from_52w_high": round(float((last_close - high_52w) / high_52w * 100), 1),
        "pct_from_52w_low": round(float((last_close - low_52w) / low_52w * 100), 1),
    }


def get_news(ticker: str, limit: int = 5) -> list:
    news = yf.Ticker(ticker).news or []
    out = []
    for item in news[:limit]:
        content = item.get("content", item)
        out.append({
            "title": content.get("title"),
            "publisher": (content.get("provider") or {}).get("displayName"),
            "link": (content.get("canonicalUrl") or {}).get("url"),
        })
    return out


def research(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "fundamentals": get_fundamentals(ticker),
        "technicals": compute_technicals(ticker),
        "news": get_news(ticker),
    }


def _main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for ticker in sys.argv[1:]:
        print(json.dumps(research(ticker), indent=2, default=str))


if __name__ == "__main__":
    _main()
