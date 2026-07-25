# Workflow: Weekly Wrap-Up (research-only, no execution)

## Context
You are running as a scheduled cloud agent with NO access to the user's local
machine, `.env`, or Trading 212/Slack credentials -- same constraints as
[`workflows/cloud_research.md`](cloud_research.md). You run once a week
(Friday, ~30 min after the Friday postmarket research session and its local
executor pass have both completed), not three times a day. Your job is not
another screen -- it's a narrative reflection on the week that already
happened, written for the user to read, not for the local executor to act on.

This repo is `massitondo1/trading-bot` (public -- no secrets, `.env` is
gitignored). You have a full checkout. Read `CLAUDE.md` first for the overall
WAT framework this project follows.

## Objective
Write a human-readable Friday wrap-up covering: what trades happened this
week and why, whether the market backed up those theses or not, the major
news that mattered, and what you're planning to look at next week. This is
delivered to the user over Slack by a separate local process that only reads
the file you write -- it does no reasoning of its own, so anything you want
the user to know needs to actually be in the file.

## Inputs to read before doing anything
- `data/trade_log.csv` -- filter to rows from the last 7 days. For each,
  the `reasoning` column already has the thesis written at entry; your job
  is to explain it in plain language and say whether the week's price action
  and news support it.
- `data/portfolio_history.csv` and `python tools/portfolio_tracker.py
  weekly-performance` -- the week's portfolio return vs S&P 500 (trailing
  7 days, not since-inception -- that command exists specifically for this
  workflow). If it reports `alpha_pct: null`, say so plainly rather than
  omitting performance -- it usually means yfinance hasn't finalized the
  latest S&P close yet, not that anything is broken.
- `data/current_holdings.json` -- what's held right now and at what weight.
- `data/lessons_learned.json` -- filter to entries dated this week; fold
  any new lessons into your reflection (e.g. "this confirms/contradicts the
  pattern noted on <date>").
- `research/archive/` -- this week's premarket/midday/postmarket session
  files (filenames are UTC timestamps -- filter to the last 7 days). These
  are your own past reasoning; don't re-derive from scratch what's already
  written there, synthesize it.
- `data/watchlist.json` -- what's being tracked and why, for the "next week"
  section.

## What to actually do this session
1. Install deps if needed: `pip install -r requirements.txt` (or use a venv).
2. Pull this week's trades from `data/trade_log.csv` and, for each, write a
   short plain-English "why we did this" -- pull the original reasoning,
   don't just restate the ticker and action.
3. Run `python tools/portfolio_tracker.py weekly-performance` for the
   week's return vs S&P 500, and `python tools/portfolio_tracker.py
   position-performance` to check whether each current holding is tracking
   its original thesis or not.
4. Research the major news of the week:
   - Per-ticker: `python tools/market_research.py <ticker>` for anything
     traded or held this week (its `news` field pulls recent headlines).
   - Market-wide: use WebSearch to check for macro events that moved markets
     this week (Fed/rate decisions, CPI/jobs prints, major earnings surprises,
     geopolitical events) -- yfinance's per-ticker news won't surface these,
     and they're often the real reason a sector-wide move happened rather
     than anything specific to a single position.
5. Write the synthesis: for each trade, does the week's news/price action
   confirm the thesis, contradict it, or is it too early to tell? Call out
   anything that moved for a reason unrelated to the stated thesis (right for
   the wrong reason is still worth flagging, same principle as the daily
   retrospective in `cloud_research.md`).
6. Write a short "next week" section: what's on the watchlist for the next
   screening pass, any upcoming catalysts (earnings dates, macro releases)
   worth watching, and anything from this week's lessons that should change
   how you evaluate something next week.
7. Write the wrap-up to `research/weekly_wrapup/<YYYY-MM-DD>.md` (Friday's
   date, UTC) as a well-formatted markdown document meant to be read
   directly -- this is not a JSON schema like the daily recommendations
   file. Suggested structure:
   ```markdown
   # Weekly Wrap-Up -- <date range>

   ## Performance
   <portfolio return vs S&P 500 this week, current holdings snapshot>

   ## What happened this week and why
   <one entry per trade -- what, why, did it play out>

   ## Major news
   <market-wide and position-specific news that mattered>

   ## Lessons / thesis checks
   <anything from lessons_learned.json or this week's re-evaluations worth
   flagging>

   ## Next week
   <watchlist focus, upcoming catalysts, anything changing in approach>
   ```
8. Commit and push: `git add research/weekly_wrapup/ && git commit -m "..." && git push`

## What you must NOT do
- Do not attempt to call the Trading 212 or Slack APIs -- you have no
  credentials and it will just fail. The local executor delivers this file
  to Slack; you only need to write it.
- Do not recommend trades or overwrite `research/latest_recommendations.json`
  -- that's the daily research session's file, this is a separate report.
- Don't manufacture drama if the week was quiet -- "nothing much happened,
  holdings tracked broadly with the market, no new lessons" is a valid and
  honest wrap-up.
