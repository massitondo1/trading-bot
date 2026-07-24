# Workflow: Autonomous Trading Bot

## Objective
Run a research-and-trade cycle on a global stocks/ETFs universe, three times
a day (pre-market, midday, post-market), aiming to beat the S&P 500 over the
long run, while staying inside fixed risk limits. Execute trades autonomously
(no per-trade confirmation needed) but always notify the user on Slack after
any trade, and log everything.

## Architecture: split across cloud (research) and local (execution)
This bot runs in two halves that never share credentials:

1. **Cloud research agent** (scheduled via claude.ai routines, repo
   `massitondo1/trading-bot`) -- no Trading 212/Slack credentials, does pure
   research and writes `research/latest_recommendations.json`. Its full SOP
   is [`workflows/cloud_research.md`](cloud_research.md) -- read that for the
   research methodology.
2. **Local executor** (`tools/apply_recommendations.py`, run via `launchd` on
   the user's Mac through `tools/run_local_cycle.sh`) -- holds the real
   credentials in `.env`. Reads the cloud's recommendations, applies the hard
   risk limits below (which the cloud does NOT enforce -- it only recommends
   within them), executes what qualifies, logs it, snapshots the portfolio,
   notifies Slack, and pushes updated state back to the repo so the next
   cloud session has fresh context.

Git is the handoff mechanism between the two halves -- see "Data flow" below.
This document describes the local-execution half in detail; this is the only
half that can be reasoned about as a fixed, deterministic procedure. The
research half is intentionally judgment-driven and documented separately.

## Status
Currently running against the **Trading 212 practice/demo account**
(`TRADING212_API_MODE=demo` in `.env`).

**First cycle executed 2026-07-24** (manual, before scheduling existed):
built a 7-position starting portfolio (GOOGL, NVO, JPM, MSFT, AMZN, V,
AZN-LSE) at £42,724 invested / £49,970.96 total value, ~14.4% cash reserve.
Baseline snapshot logged against S&P 500 close of 7,414.78 in
`data/portfolio_history.csv` -- this is the reference point all future alpha
calculations run from. Trade log in `data/trade_log.csv`.

**Scheduling set up 2026-07-24**: 3x/day cloud research routines (pre-market,
midday, post-market) plus matching local `launchd` executor jobs. See
"Schedule" section below for exact times and routine IDs.

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
- `.env` populated with `TRADING212_API_KEY`, `TRADING212_API_SECRET`,
  `TRADING212_API_MODE`, `SLACK_WEBHOOK_URL` (local only -- never present in
  the cloud repo checkout)
- `research/latest_recommendations.json` written by the cloud research agent

## Tools used
- `tools/market_research.py` -- fundamentals, technicals, news per ticker (no API key needed; used by the cloud agent)
- `tools/trading212_client.py` -- account/cash/portfolio state, place orders (local only, needs credentials)
- `tools/apply_recommendations.py` -- the deterministic risk-gated executor described below (local only)
- `tools/refresh_instruments_reference.py` -- weekly refresh of `data/instruments_reference.json`, called automatically by `run_local_cycle.sh`
- `tools/portfolio_tracker.py` -- trade log + daily snapshot + performance vs S&P 500
- `tools/slack_notify.py` -- send a message after every trade and on any error
- `tools/run_local_cycle.sh` -- the launchd entry point: git pull -> refresh instrument reference if stale -> `apply_recommendations.py` -> git add/commit/push `data/`

## Data flow (how cloud and local hand off without sharing credentials)
```
cloud research agent                       local executor (has credentials)
--------------------                       ------------------------------
reads data/current_holdings.json     <---- writes after every run
reads data/watchlist.json                  (git pull happens first)
reads data/instruments_reference.json <--- refreshed weekly
reads data/portfolio_history.csv     <----
writes research/latest_recommendations.json
writes data/watchlist.json
git push                             ----> git pull, then acts on the
                                            recommendations file
```
Both sides only ever write to their own designated files, so conflicts should
be rare; both always `git pull` before doing anything and push immediately
after, keeping the divergence window small.

## Local execution cycle (`apply_recommendations.py`, runs 3x/day)

1. **Pull current state** -- `trading212_client.get_portfolio()` and `get_cash()`

2. **Unconditional stop-loss check** -- runs first, before even looking at
   recommendations. Any holding down >=20% from cost basis is sold
   immediately regardless of what the cloud recommended or didn't. This is a
   hard rule with no override.

3. **Process recommendations** (skipped if `research/latest_recommendations.json`'s
   `generated_at` was already processed -- see `data/last_processed_research.json`):
   - `HOLD` -> no action
   - `SELL` -> full exit, but blocked if it would drop total holdings below 5 (diversification floor) -- unless it's a stop-loss, which is unconditional and already handled in step 2
   - `BUY` -> target weight is capped at 15% of portfolio value regardless of what the recommendation asked for; incremental buy size = capped target value minus what's already held minus insufficient cash; trades under £50 are skipped as noise

4. **Execute** -- `trading212_client.place_market_order(ticker, signed_quantity, idempotency_key=f"{date}-{session}-{ticker}-{action}")`, then `portfolio_tracker.log_trade(...)`

5. **Snapshot + notify** -- `data/current_holdings.json` and a
   `portfolio_tracker.snapshot(...)` are written every run regardless of
   whether trades happened. Slack is notified only if a trade actually
   executed or an error occurred -- **silence means "checked, nothing to do,"
   not "didn't run."** Weekly, also check `portfolio_tracker.performance()`
   and send a short alpha-vs-S&P-500 summary.

## Schedule
Three cloud research routines + three matching local `launchd` jobs, timed
so the local job runs ~15 minutes after its cloud counterpart pushes (git
pull latency + a safety margin):

| Session | Cloud research (UTC) | Local execution (UTC) | Routine ID |
|---|---|---|---|
| premarket | 12:00 (`0 12 * * 1-5`) | 12:15 | `trig_01U4U1PVkfJzGNHumvw32Xwp` |
| midday | 16:00 (`0 16 * * 1-5`) | 16:15 | `trig_01Qkb7eBXkDA1rRCGcSZUiAT` |
| postmarket | 20:45 (`45 20 * * 1-5`) | 21:00 | `trig_01Q3LEQxfeHC8pmLXuDxagtv` |

All weekdays only (`1-5`). See/manage at https://claude.ai/code/routines
(cannot be deleted via API, only there). Local launchd job plists live in
`~/Library/LaunchAgents/com.tradingbot.*.plist`.

**Repo visibility note:** `massitondo1/trading-bot` is currently **public**.
This was required to unblock the cloud routine's git clone (private-repo
access via GitHub connectors didn't work for this CCR checkout path as of
2026-07-24) -- it contains no secrets (`.env` is gitignored, never
committed), but the trading strategy/code and trade history are visible to
anyone with the link.

**Caveat:** cloud routine cron expressions are fixed UTC; US market-hour-relative
times will drift by an hour around US DST transitions (roughly early March and
early November) since the cron doesn't auto-adjust. Revisit twice a year.

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
