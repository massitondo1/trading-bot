# Workflow: Autonomous Trading Bot

## Objective
Run a daily research-and-trade cycle on a global stocks/ETFs universe, aiming
to beat the S&P 500 over the long run, while staying inside fixed risk limits.
Execute trades autonomously (no per-trade confirmation needed) but always
notify the user on Slack after any trade, and log everything.

## Status
Currently running against the **Trading 212 practice/demo account**
(`TRADING212_API_MODE=demo` in `.env`).

**First cycle executed 2026-07-24**: built a 7-position starting portfolio
(GOOGL, NVO, JPM, MSFT, AMZN, V, AZN-LSE) at £42,724 invested / £49,970.96
total value, ~14.4% cash reserve. Baseline snapshot logged against S&P 500
close of 7,414.78 in `data/portfolio_history.csv` -- this is the reference
point all future alpha calculations run from. Trade log in `data/trade_log.csv`.

Do NOT switch to `live` until:
- At least 4-6 weeks of demo trading history exists in `data/trade_log.csv`
- `python tools/portfolio_tracker.py performance` shows the logic behaves as
  intended (no runaway trading, risk limits respected, no duplicate orders)
- The user explicitly says to go live

## Risk limits (hard constraints -- never violate these)
- **Max position size:** 15% of total portfolio value per single holding
- **Min diversification:** hold at least 5-6 distinct positions once portfolio
  value supports it (skip this floor only while capital is too small to split
  further after fees/minimums)
- **Stop-loss:** exit a position if it falls 20% below its average cost basis
- **Instruments:** stocks and ETFs only. No CFDs, no leverage, no options.
- **Universe:** global (US, UK, Europe) equities available on Trading 212
- **Order safety:** always pass a unique `idempotency_key` to
  `place_market_order` (see tools/trading212_client.py docstring) -- the order
  endpoint is beta and not idempotent, retries can duplicate a live order

## Inputs required
- `.env` populated with `TRADING212_API_KEY`, `TRADING212_API_MODE`, `SLACK_WEBHOOK_URL`
- A watchlist / candidate universe (start from S&P 500 + a handful of
  UK/European large caps; can expand over time)

## Tools used
- `tools/market_research.py` -- fundamentals, technicals, news per ticker (no API key needed)
- `tools/trading212_client.py` -- account/cash/portfolio state, place orders
- `tools/portfolio_tracker.py` -- trade log + daily snapshot + performance vs S&P 500
- `tools/slack_notify.py` -- send a message after every trade and on any error

## Daily cycle

1. **Pull current state**
   - `trading212_client.get_cash()` and `get_portfolio()` to know cash available and current holdings
   - `portfolio_tracker.snapshot(total_portfolio_value)` to log today's value vs S&P 500 close

2. **Screen candidates**
   - For each ticker in the watchlist (and any the agent's own research surfaces), run `market_research.research(ticker)`
   - Hybrid approach: use fundamentals (valuation, growth, margins, ROE, debt) to decide if a company is worth owning at all; use technicals (RSI, SMA50/200, distance from 52w high/low) plus recent news to time entries/exits
   - Reasoning/judgment happens here, in the agent -- this is NOT a hardcoded formula. Treat the tool output as evidence, not a verdict.

3. **Apply risk limits before any trade**
   - Would a BUY push this position over 15% of portfolio value? -> size down or skip
   - Does portfolio already hold the pre-agreed minimum diversification? -> factor into how much conviction is needed to concentrate further
   - Is an existing position down >=20% from cost basis? -> exit regardless of thesis (hard stop-loss, no exceptions)

4. **Execute**
   - `trading212_client.place_market_order(ticker, signed_quantity, idempotency_key=f"{date}-{ticker}-{action}")`
   - Record the fill: `portfolio_tracker.log_trade(action, ticker, quantity, price, reasoning)`

5. **Notify**
   - `slack_notify.send_message(...)` after every executed trade: what was bought/sold, quantity, price, and the one-line reasoning
   - Also notify on errors (API failure, rate limit exhausted, risk-limit block) so silence never means "nothing happened" ambiguously

6. **Weekly/periodic**
   - `portfolio_tracker.performance()` to check cumulative alpha vs S&P 500
   - Send a short Slack performance summary periodically (not necessarily daily -- avoid notification fatigue)

## Edge cases / things learned so far
- **Auth is HTTP Basic, not a raw key.** Trading 212 requires
  `Authorization: Basic base64(API_KEY:API_SECRET)` -- newer API keys come
  with both a key and a one-time-shown secret. `trading212_client.py` falls
  back to sending the raw key alone only if no secret is configured (older
  key format). If you ever see 401s after regenerating a key, check both
  `TRADING212_API_KEY` and `TRADING212_API_SECRET` are set.
- **Endpoint paths drifted from older third-party docs/gists**: positions
  live at `/api/v0/equity/positions` (not `/equity/portfolio`), and the
  combined account endpoint is `/api/v0/equity/account/summary` (not
  `/equity/account/info`). `/equity/account/cash` still works separately.
  Always sanity-check a path against a live call before trusting an
  unofficial source -- this API is beta and has changed shape before.
- **Instrument tickers**: Trading 212 uses suffixed tickers per exchange/share
  class (e.g. `GOOGL_US_EQ`, `AZNl_EQ` for the LSE line in GBX pence,
  `MSFd_EQ` for a EUR-denominated line). Match against
  `get_instruments()` by ISIN/name before placing an order -- don't guess the
  suffix, and watch out for leveraged ETP products (`3LMSl_EQ` etc.) with
  similar names to the real stock.
- Trading 212 order endpoint is beta and NOT idempotent -- always use the
  idempotency_key guard in `place_market_order`; it stores a local log at
  `data/order_log.json` and refuses to resend a key already used.
- Rate limits observed from docs: ~1 order request per 1-2s, portfolio reads
  ~1 per 5s, historical data ~6/min. The client backs off automatically on
  HTTP 429 with exponential wait, but don't hammer these endpoints in a tight loop.
- `yfinance` needs no API key but can occasionally return empty data for
  illiquid/foreign tickers -- `compute_technicals` returns `{"error": ...}` in
  that case; treat as "insufficient data, skip" rather than crashing the cycle.
- Trading 212's Public API (Beta) is only available on General Invest and
  Stocks & Shares ISA accounts, not SIPP -- confirm which account type the
  API key belongs to if account endpoints ever 403.

## Escalation
If something fails in a way that could affect real money (unexpected auth
error switching modes, an order result that doesn't match what was requested,
performance sharply diverging from expectations), stop and notify the user on
Slack rather than retrying blindly.
