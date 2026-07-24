# Workflow: Cloud Research Session (research-only, no execution)

## Context
You are running as a scheduled cloud agent with NO access to the user's local
machine, `.env`, or Trading 212/Slack credentials. Your job is pure research
and recommendation -- you never place trades or send notifications directly.
A separate process running locally (which does hold the credentials) reads
your output, applies hard risk limits, executes what qualifies, and notifies
the user on Slack. Trust that pipeline to do its job; your only output is a
well-reasoned recommendations file.

This repo is `massitondo1/trading-bot` (public -- required to make cloud
checkout work, contains no secrets, `.env` is gitignored). You have a full
checkout. Read `CLAUDE.md` first for the overall WAT framework this project
follows.

## Objective
Research the market (global stocks/ETFs, hybrid fundamentals+technicals
approach) and produce a recommendations file the local executor can act on.
Also keep growing and refining `data/watchlist.json` over time -- this run is
one of several per day, and the point is continuous, compounding research,
not a one-shot screen.

## Inputs to read before doing anything
- `data/instruments_reference.json` -- a static snapshot of Trading 212's
  tradeable stock universe (ticker, name, currency, ISIN), refreshed weekly
  by the local executor. You have NO Trading 212 API credentials, so this
  file is your only way to resolve a company to its exact Trading 212
  ticker (e.g. "Novo Nordisk" -> `NVO_US_EQ`). Match by name/ISIN. A company
  often has multiple lines (USD/GBX/EUR) -- prefer the one matching where
  you did your fundamentals research (e.g. use the USD line for a US-primary
  listing). If a name genuinely isn't in this file, don't recommend a BUY
  for it -- note it in `notes` instead so the user knows it's unavailable.
- `data/watchlist.json` -- tickers currently being tracked, with your own
  notes/conviction from past sessions. If this file doesn't exist yet,
  bootstrap it from the tickers in `data/trade_log.csv`.
- `data/current_holdings.json` -- live snapshot of what's actually held right
  now (ticker, quantity, avg cost, current weight %), refreshed by the local
  executor after each run. This is your source of truth for "what do we
  already own" -- don't recommend a fresh BUY on something already near its
  15% cap, and don't recommend closing something that isn't actually held.
- `data/portfolio_history.csv` -- daily value vs S&P 500, so you know how the
  strategy is actually doing before suggesting changes.
- `research/archive/` -- your own past reasoning. Skim the last few files so
  you're not repeating the same screen from scratch or contradicting a recent
  thesis without acknowledging it changed.
- `data/lessons_learned.json` -- dated retrospective entries from past
  sessions (see the Retrospective step below). This is the whole point of
  the learning loop: read it BEFORE screening anything, and let it actually
  change your behavior -- e.g. if a past entry says "oversold-RSI-plus-cheap-
  PE entries have been getting the timing wrong on Financials specifically,"
  weight that against a new Financials pick that fits the same pattern.

## Risk limits (you don't enforce these -- the local executor does -- but
## recommend within them so nothing you suggest gets silently dropped)
- Max 15% of portfolio value per position
- Maintain at least 5-6 distinct holdings
- 20% stop-loss is enforced locally regardless of your recommendation --
  you don't need to re-flag positions already down 20%+, the executor
  handles that unconditionally
- Stocks and ETFs only, no leverage/CFDs/options
- Global universe (US, UK, Europe)

## What to actually do this session
1. Install deps if needed: `pip install -r requirements.txt` (or use a venv).

2. **Retrospective (do this every session, it's the learning loop the user
   explicitly asked for):**
   Run `python tools/portfolio_tracker.py position-performance` -- for each
   currently held position, this joins live return% with the exact reasoning
   that was written at entry. For each holding:
   - Is it up or down, and does the *reason* match what was expected, or did
     it move for an unrelated reason (e.g. up on a sector-wide rally rather
     than the specific catalyst you cited)? Being right for the wrong reason
     is still worth flagging -- it means the thesis wasn't actually tested.
   - If a position has been held more than a few sessions and the original
     thesis has clearly played out (correctly or not), or a position closed
     (check `data/trade_log.csv` for SELL/STOPLOSS rows no longer in
     `current_holdings.json`), write a dated entry to
     `data/lessons_learned.json`: what happened, whether the original
     reasoning holds up, and what (if anything) should change about how you
     evaluate similar setups going forward. Use the `category` field
     (`thesis-correct | thesis-wrong | timing-issue | risk-management |
     data-quality | process`) so patterns become greppable over time.
   - Don't force a lesson out of every session if nothing new happened --
     an empty retrospective (nothing held long enough yet, no closed
     positions, no surprises) is a valid outcome. Don't manufacture insight
     to fill the file.
   - This mirrors the technical self-improvement loop in `CLAUDE.md` (fix a
     tool, verify, document in the workflow) but for strategy/outcome
     learning instead of code bugs -- if you also hit a technical problem
     (a data source failing, a ticker mapping issue), document that as its
     own edge case in this workflow file directly, the same way
     `workflows/trading_bot.md`'s "Edge cases" section already does.

3. Re-evaluate every current holding from `data/current_holdings.json` using
   `python tools/market_research.py <yahoo_ticker>` -- has the thesis
   changed? Note any that look like SELL/TRIM candidates. Let step 2's
   retrospective inform how skeptically you re-check each one.
4. Screen new candidates: pull tickers from `data/watchlist.json` plus at
   least a few genuinely new ones you haven't looked at recently (expand
   coverage over time -- sectors/geographies underrepresented in current
   holdings are good places to look). Run `market_research.py` on each.
5. Apply the hybrid approach: fundamentals (valuation, growth, margins, ROE,
   debt) decide whether a company is worth owning; technicals (RSI, SMA50/200,
   distance from 52w range) plus recent news inform entry/exit timing. This is
   your judgment call, not a formula -- explain your reasoning, and weigh it
   against any relevant pattern from `data/lessons_learned.json`.
6. Update `data/watchlist.json` with what you looked at, your conviction, and
   why (even for names you're rejecting -- that's useful memory for next time).
7. Write `research/latest_recommendations.json` (schema below), overwriting
   the previous one.
8. Also write an archival copy to
   `research/archive/<UTC timestamp>_<session>.json` (never overwrite these).
9. Commit and push: `git add data/watchlist.json data/lessons_learned.json research/ && git commit -m "..." && git push`

## `research/latest_recommendations.json` schema
```json
{
  "generated_at": "2026-07-25T08:00:00Z",
  "session": "premarket | midday | postmarket",
  "recommendations": [
    {
      "ticker": "GOOGL_US_EQ",
      "action": "BUY | SELL | HOLD",
      "target_weight_pct": 12.5,
      "conviction": "high | medium | low",
      "reasoning": "one or two sentences, cite the specific numbers"
    }
  ],
  "notes": "free-text market commentary / anything the executor or user should know"
}
```
- `target_weight_pct` is your suggested target weight of total portfolio
  value; it only matters for BUY. The executor caps it at 15% regardless of
  what you put here, so don't inflate it hoping to get more -- put your
  actual view.
- `SELL` means close the entire position. There's no partial-trim action in
  this schema yet -- if you want a trim, say SELL and explain in reasoning
  that you'd size back in later, or just recommend HOLD if a full exit is
  too strong a call.
- `HOLD` entries are still useful -- they show you evaluated something and
  chose not to act, with reasoning, rather than silence meaning "didn't look."

## What you must NOT do
- Do not attempt to call the Trading 212 or Slack APIs -- you have no
  credentials and it will just fail.
- Do not fabricate `data/current_holdings.json` if it's missing -- note that
  it's missing in `notes` and fall back to inferring from `trade_log.csv`.
- Do not recommend CFDs, leverage, or options regardless of how compelling
  the setup looks.
