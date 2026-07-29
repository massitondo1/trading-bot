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
   research methodology. A second, weekly cloud routine writes a narrative
   Friday wrap-up instead of trade recommendations -- see
   [`workflows/weekly_wrapup.md`](weekly_wrapup.md).
2. **Local executor** (`tools/apply_recommendations.py`, run via `launchd`
   jobs on this Mac through [`tools/run_local_cycle.sh`](../tools/run_local_cycle.sh))
   -- holds the real credentials in `.env`, never committed. Each scheduled
   run pulls the repo, refreshes the instrument reference if stale, and
   invokes `apply_recommendations.py`. Reads the cloud's recommendations,
   applies the hard risk limits below (which the cloud does NOT enforce --
   it only recommends within them), executes what qualifies, logs it,
   snapshots the portfolio, notifies Slack, and pushes updated state back to
   the repo so the next cloud session has fresh context. A separate weekly
   `launchd` job ([`tools/deliver_weekly_wrapup.sh`](../tools/deliver_weekly_wrapup.sh))
   does no trading logic -- it just reads the cloud's wrap-up file and posts
   it to Slack.

   [`.github/workflows/execution-cycle.yml`](../.github/workflows/execution-cycle.yml)
   and [`.github/workflows/weekly-wrapup-delivery.yml`](../.github/workflows/weekly-wrapup-delivery.yml)
   briefly ran this on a schedule (2026-07-27 to 2026-07-29) but execution
   moved back to local `launchd` -- see "Status" below for why. Both
   workflows are retained with their `schedule:` triggers commented out,
   kept only as a manual (`workflow_dispatch`) fallback for when this Mac is
   off; re-enabling their crons alongside live local `launchd` jobs would
   double-execute every trade.

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

**Weekly wrap-up added 2026-07-25**: a Friday cloud routine writes a
narrative reflection (trades made and why, major news, thesis checks, next
week's plan) to `research/weekly_wrapup/<date>.md`; a matching weekly local
`launchd` job delivers it to Slack. See "Schedule" below.

**Execution moved to GitHub Actions 2026-07-27, then back to local
`launchd` 2026-07-29**: briefly ran execution + weekly delivery unattended
on GitHub Actions so no laptop/process needed to stay running. Moved back to
local `launchd` by user request; cloud research scheduling is unaffected
either way (it was never on GitHub Actions -- see "Architecture" above).
Same trip also fixed two real bugs found along the way: a trade executing
successfully but then crashing before its record got committed (any
unhandled error after `place_market_order()` succeeds could leave that
trade's `data/` changes -- including its idempotency key -- unpushed, risking
re-execution on the next run), and a pence-quote (GBX) assumption that was
wrongly applied to a handful of non-GBX-quoted London-listed tickers. Both
`run_local_cycle.sh`/`deliver_weekly_wrapup.sh` and `apply_recommendations.py`
now guard against these -- see "Edge cases" below.

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
- `TRADING212_API_KEY`, `TRADING212_API_SECRET`, `TRADING212_API_MODE`,
  `SLACK_WEBHOOK_URL` set in `.env` on this Mac (gitignored, never
  committed) -- read by `run_local_cycle.sh`/`deliver_weekly_wrapup.sh` via
  `.venv`. The same four values are also kept as GitHub Actions repository
  secrets for the manual-fallback workflows, never present in the cloud
  research checkout either way.
- `research/latest_recommendations.json` written by the cloud research agent

## Tools used
- `tools/market_research.py` -- fundamentals, technicals, news per ticker (no API key needed; used by the cloud agent)
- `tools/trading212_client.py` -- account/cash/portfolio state, place orders (execution path only, needs credentials)
- `tools/apply_recommendations.py` -- the deterministic risk-gated executor described below (execution path only)
- `tools/refresh_instruments_reference.py` -- weekly refresh of `data/instruments_reference.json`, called automatically by `run_local_cycle.sh`
- `tools/portfolio_tracker.py` -- trade log + daily snapshot + performance vs S&P 500
- `tools/slack_notify.py` -- send a message after every trade and on any error (also used directly for failure alerts, since the local path has no CI-level failure hook)
- `tools/run_local_cycle.sh` -- the live scheduled entry point (via `launchd`, see "Schedule" below): git pull -> refresh instrument reference if stale -> `apply_recommendations.py` -> git add/commit/push `data/` (always, even if the previous step failed, so a trade that executed isn't lost) -> Slack-alert and exit non-zero on failure. [`.github/workflows/execution-cycle.yml`](../.github/workflows/execution-cycle.yml) re-expresses the same steps as CI steps, kept only as a manual fallback.
- `tools/deliver_weekly_wrapup.py` / `tools/deliver_weekly_wrapup.sh` -- `deliver_weekly_wrapup.sh` is the live scheduled entry point (via `launchd`): git pull -> find latest `research/weekly_wrapup/*.md` -> post to Slack (chunked, idempotent via `data/last_delivered_wrapup.json`) -> commit/push that marker (always) -> Slack-alert on failure. [`.github/workflows/weekly-wrapup-delivery.yml`](../.github/workflows/weekly-wrapup-delivery.yml) is the manual fallback.

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
Three cloud research routines (fixed UTC, unaffected by anything below) +
four local `launchd` jobs on this Mac, timed so each execution run fires
~15 minutes after its cloud counterpart pushes (git pull latency + a safety
margin). `launchd` runs on the Mac's local wall-clock time (Europe/Rome),
so the local column shifts by 1h relative to UTC across DST -- the plists
were written for CEST (UTC+2, in effect as of 2026-07-29); revisit around
the March/October DST transitions.

| Session | Cloud research (UTC) | Local launchd (Europe/Rome, currently CEST) | Routine ID | launchd Label |
|---|---|---|---|---|
| premarket | 12:00 (`0 12 * * 1-5`) | 14:15 | `trig_01U4U1PVkfJzGNHumvw32Xwp` | `com.massimilianotondo.tradingbot.premarket` |
| midday | 16:00 (`0 16 * * 1-5`) | 18:15 | `trig_01Qkb7eBXkDA1rRCGcSZUiAT` | `com.massimilianotondo.tradingbot.midday` |
| postmarket | 20:45 (`45 20 * * 1-5`) | 23:00 | `trig_01Q3LEQxfeHC8pmLXuDxagtv` | `com.massimilianotondo.tradingbot.postmarket` |
| weekly wrap-up | Fri 21:15 (`15 21 * * 5`) | Fri 23:45 | `trig_013W8Zr3saCYr9UWvmqJsYQr` | `com.massimilianotondo.tradingbot.weeklywrapup` |

All weekdays only (`1-5`), weekly wrap-up Fridays only. Cloud routines are
managed at https://claude.ai/code/routines (cannot be deleted via API, only
there). Local `launchd` plists live in `~/Library/LaunchAgents/` (not
committed to the repo -- they're machine-local config); check/reload with
`launchctl list | grep tradingbot` / `launchctl load -w <plist>`. GitHub
Actions retains the same schedule commented out, in
[`.github/workflows/execution-cycle.yml`](../.github/workflows/execution-cycle.yml)
(the three daily sessions) and
[`.github/workflows/weekly-wrapup-delivery.yml`](../.github/workflows/weekly-wrapup-delivery.yml)
(Friday) -- both `schedule:` blocks are commented out (see "Architecture"
above); only `workflow_dispatch` (manual) is live there.

**Repo visibility note:** `massitondo1/trading-bot` is currently **public**.
This was required to unblock the cloud routine's git clone (private-repo
access via GitHub connectors didn't work for this CCR checkout path as of
2026-07-24) -- it contains no secrets (`.env` is gitignored, never
committed), but the trading strategy/code and trade history are visible to
anyone with the link.

**Caveat:** the cloud routine's cron is fixed UTC and doesn't auto-adjust,
while local `launchd` follows the Mac's local clock and does auto-adjust for
Europe/Rome DST -- but Europe/Rome DST and US market-hour-relative UTC times
don't move together, so the ~15-minute buffer between cloud push and local
execution will still drift by up to an hour around DST transitions (EU:
late March/late October; US: early March/early November -- they don't
switch on the same day). Revisit the local plist times twice a year.

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
- **Connection resets happen and aren't fatal on their own.** Two back-to-back
  local cycles (2026-07-26) died with an uncaught `ConnectionResetError` on
  the very first Trading 212 API call, with no retry -- turned out
  `_request()`'s retry loop only handled HTTP 429, not transport-level
  failures. Fixed by also catching `requests.exceptions.ConnectionError`
  (which wraps OS-level resets), `Timeout`, and `ChunkedEncodingError` with
  the same exponential backoff. This matters more now that execution runs
  unattended on GitHub Actions -- nobody's around to notice a silent crash
  and manually retry.
- `yfinance` needs no API key but can occasionally return empty data for
  illiquid/foreign tickers -- `compute_technicals` returns `{"error": ...}` in
  that case; treat as "insufficient data, skip" rather than crashing the cycle.
- Trading 212's Public API (Beta) is only available on General Invest and
  Stocks & Shares ISA accounts, not SIPP -- confirm which account type the
  API key belongs to if account endpoints ever 403.
- **A crash after a trade executes can lose that trade's record, not just
  fail loudly.** Retrying transport errors (previous bullet) helps, but two
  cycles (2026-07-26) still died on the closing portfolio re-fetch used only
  for the snapshot file -- and because the shell wrapper aborted on that
  exit code before reaching `git commit`/`push`, the trade that already
  executed (and its `data/order_log.json` idempotency key) would have stayed
  uncommitted, risking re-execution on the next run. Fixed in two places:
  `apply_recommendations.py`'s `run()` now wraps both post-trade re-fetches
  (and `get_live_price()`'s yfinance call) in try/except so a transient
  failure there degrades gracefully instead of aborting; `run_local_cycle.sh`
  / `deliver_weekly_wrapup.sh` now always attempt the `git commit`/`push`
  regardless of the Python step's exit status, and Slack-alert + exit
  non-zero afterward if it did fail. The lesson: for anything that fires a
  real trade, "commit what happened" must never be conditional on "the rest
  of the run also succeeded."
- **Not every London-listed (`l_EQ`) ticker is pence-quoted (GBX).** The
  ticker-to-Yahoo mapping and the GBX x100 budget conversion both used to
  assume any `l_EQ`-suffixed ticker was GBX -- true for the vast majority,
  but checking the real `data/instruments_reference.json` universe turned up
  ~57 London-listed instruments actually quoted in USD/EUR/GBP (e.g. Bank of
  Ireland, `BIRG1l_EQ`, in EUR). Sizing one of those with the pence
  conversion would have been ~100x too large. `apply_recommendations.py` now
  looks up the ticker's real currency from `data/instruments_reference.json`
  before sizing a BUY, and skips (rather than guesses) anything that isn't
  confirmed plain US (`_US_EQ`/USD) or GBX/GBP London (`l_EQ`) -- per
  decision, other markets (Xetra, Swiss, etc., already using distinct
  suffixes the code never matched) stay unsupported rather than building out
  full multi-exchange handling.

## Learning loop (strategy side)
Every cloud research session starts with a retrospective step (see
`workflows/cloud_research.md` step 2): it runs
`tools/portfolio_tracker.py position-performance` to compare each holding's
actual return against the exact reasoning written at entry, and writes dated
findings to `data/lessons_learned.json` -- what worked, what didn't, and
whether it was right for the right reason. Future research sessions read
this file before screening anything, so patterns compound instead of
resetting every run. This is the strategy/outcome equivalent of this file's
own "Edge cases" section below, which captures technical/process lessons
(API quirks, auth issues) instead -- see `CLAUDE.md`'s self-improvement loop
for that side.

## Escalation
If something fails in a way that could affect real money (unexpected auth
error switching modes, an order result that doesn't match what was requested,
performance sharply diverging from expectations), stop and notify the user on
Slack rather than retrying blindly.
