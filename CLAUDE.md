# Investment Tracker — project memory

Read this first, every session.

## What this project is

A personal Telegram bot that tracks a stock watchlist and the stocks I own, and messages
me when a watched stock **drops ≥10%** or a stock I hold **rises ≥10%** within 12 hours.
It can also run a full equity research report on demand. Single user (me), driven by
plain English messages, running as a systemd service.

## Tech stack

- **Python 3.14**, `python-telegram-bot` 22.5 — long-polling, one process, no webhook or
  web server. The recurring price check runs on its built-in JobQueue.
- **`yfinance`** for prices and financials (free, no API key), **`rapidfuzz`** for matching
  company names to tickers.
- **`anthropic`** (Claude API): `claude-sonnet-5` (+ `web_search`/`web_fetch`) for the
  `Analyse` report. Optional: everything else works with no key.
- **`matplotlib`** renders the price chart attached to every entry-point alert.
- **`sqlite3` from the standard library** — no ORM, no database server. One file in `data/`.
- **`pytest`** for tests; deployed with systemd (`deploy/investment-tracker.service`).

Setup, deployment, and the full command list are in [README.md](README.md) — don't
duplicate them here.

## Always do

- **Explain what you're about to do before writing code**, in plain language. I'm learning
  as this gets built, so skip the jargon and say what the change actually does.
- **Prefer the simple version.** If a clever or advanced pattern is genuinely necessary,
  leave a comment saying why.
- **Run the tests before saying anything works, and show me the output:**
  ```
  .venv/bin/python -m pytest tests/ -q
  ```
  No "this should work" or "that's fixed" without a real passing run.
- **Add new commands through the existing seam** — a pattern in `app/parser.py`, a handler
  in `app/commands/`, one line in the `COMMAND_HANDLERS` registry. See "Adding a command"
  in the README. Nothing else should need to change.
- **Keep this file current.** It's the project's memory, so update it in the same session
  the change happens — not later. Specifically:
  - **New feature shipped** → update "Where things stand, and what's next", and the file
    map if new files appeared.
  - **A decision with lasting consequences** (schema shape, a library choice, an approach
    rejected for a reason worth remembering) → add it to "Design decisions worth knowing",
    with the *why*, not just the what.
  - **Something learned the hard way** (a constraint, a gotcha, a wrong assumption that
    cost time) → write it down wherever it fits, so it isn't rediscovered later.
  - **A new rule I give you about how to work** → add it to "Always do" or "Never do".

  Say what you added when you do it. If a change makes something here wrong, fix the wrong
  part — don't just append. Keep it tight; this gets read every session.

## Never do

- **Don't add features, commands, or options I didn't ask for.** If you spot something
  worth adding, suggest it and let me decide.
- **Don't claim work is done, fixed, or passing without having actually run it.**

## How the code is organized

| File | Does |
|---|---|
| `bot.py` | Entry point — builds the app, registers handlers, schedules the price job |
| `app/parser.py` | Turns a plain message ("Bought Apple") into an intent |
| `app/commands/` | One file per intent, wired up in `__init__.py` |
| `app/db.py` | Schema and every SQL query — all database access lives here |
| `app/prices.py` | Fetching prices from Yahoo Finance |
| `app/fundamentals.py` | Financial statements + multiples for the `Analyse` report |
| `app/analysis.py` | The Claude API call for `Analyse` (prompt, tools, report splitting) |
| `app/technicals.py` | Daily-bar caching and technical metric math (moving averages, returns, breakout detection) for the alert setups |
| `app/setups.py` | The 5 entry-point setup checks (pure functions: `TickerMetrics` in, a match or `None` out) |
| `app/setup_thresholds.py` | All ~40 numeric thresholds the setups use, hardcoded and tunable by editing the file |
| `app/charts.py` | Renders the price+moving-average chart attached to each alert |
| `app/alerts.py` | Orchestrates: builds metrics per ticker, runs all 5 setup checks, applies once-per-episode dedup |
| `tests/` | 150 tests, no network needed — they run in under a second |

## Design decisions worth knowing

These are the "why"s that aren't obvious from any single file:

- **Nothing is ever deleted.** Removing from the watchlist or selling a stock flips an
  `active` flag to 0; sold holdings also record `sell_price` and `sell_date`. That history
  is deliberately kept so a "total gains" view can be built later without losing past data.
  **Consequence:** every query that reads rows must filter on `active = 1`.
- **Built for one person.** There's one `TELEGRAM_CHAT_ID` and no user table — tickers are
  global. Supporting more people would mean changing three database tables, not flipping
  a setting.
- **No ORM and no migration tool.** The schema is created with `CREATE TABLE IF NOT EXISTS`,
  and new columns get added by `db.migrate()`, which re-checks the table on every startup
  so it's safe to run repeatedly. The bot runs against a live database with real rows, so
  **only additive changes** — dropping or retyping a column needs a hand-written plan.
- **Alert *cadence* is configurable; the 5 entry-point setups' thresholds are
  not.** `POLL_INTERVAL_MINUTES` (`.env`) still governs how often prices are
  checked. But the ~40 numeric thresholds behind the 5 entry-point setups
  (see below) are hardcoded constants in `app/setup_thresholds.py` — too many
  to expose sanely as `.env` variables for a personal bot. Tuning them is a
  code edit plus a restart, not a `.env` edit.
- **Alerts moved from a blunt "±10% in 12h" threshold to 5 technical entry-
  point setups (2026-09-05).** The old system alerted on any sharp move,
  which doesn't distinguish a stock that's *technically attractive to buy*
  from one that's just noisy. The new system (Uptrend Pullback, Momentum +
  First Dip, Breakout Retest, Oversold Reversal, Deep Pullback) looks at
  daily price structure — moving averages, multi-day returns, pullback
  depth — instead. Full rationale and exact formulas:
  `docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md`.
  **Consequence:** `alert_state`'s `alert_type` `CHECK` constraint had to be
  recreated (SQLite can't alter a `CHECK` in place) — the one approved
  exception to the "only additive changes" rule above, since the old
  `watchlist_drop`/`holding_gain` rows had no meaning under the new system
  anyway.
- **Daily history is cached in memory, refreshed once a day.** The 5 setups
  need ~2 years of daily OHLC bars per ticker (for 200-day moving averages
  and 60-day breakout lookbacks) — re-fetching that from yfinance on every
  15-minute poll would be wasteful and mostly redundant, since daily bars
  barely change intraday. A separate `bot.py` job refreshes the cache once a
  day (and once at startup); the poll job re-fetches only the live price and
  recombines it with the cached bars.
- **`Analyse` fetches financials itself; the model only researches what's genuinely
  open-ended.** Five years of statements and current multiples are known facts the moment
  a ticker is picked — fetched deterministically via `app/fundamentals.py` and embedded in
  the prompt, with every margin/ratio computed in Python rather than asked of the model.
  Claude's own tool use (`web_search` + `web_fetch`) is reserved for competitors, moat,
  TAM, management, and analyst coverage — things that actually require judgment and can't
  be looked up by ticker alone. This is deliberately simpler than the custom-tools/Tool
  Runner architecture sketched in the original plan; see
  `docs/plans/2026-08-08-analyse-company-command.md` for the full reasoning.
- **`Analyse` runs in a background `asyncio` task, not inline in the handler.** A report
  takes 1-3 minutes (multiple web searches/fetches plus the model call) — running it inline
  would stall the price-poll job and every other command for that whole time. The handler
  sends an immediate ack and returns; the report and file are sent from the background task
  when done.
- **`Analyse` runs on Sonnet 5 at `effort: "medium"`, with capped search/fetch budgets and
  prompt caching.** Started on Opus 5 at `effort: "high"`; the first real report cost $6.53.
  The model's job here is research/synthesis (the actual financials are already
  deterministic Python), so Sonnet 5 does the job at a fraction of the price. The real cost
  driver turned out to be total accumulated context (over a million tokens read from cache
  per report) rather than per-token price — caching the growing conversation and lowering
  effort (which makes Sonnet 5 use fewer, more-consolidated tool calls) cut a report from
  $6.53 to roughly $1-1.50. See `docs/plans/2026-08-08-analyse-company-command.md` for the
  full tuning history and the exact numbers at each step.

## Where things stand, and what's next

**Alert engine redesigned — added 2026-09-05** (see
`docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md` and
`docs/superpowers/plans/2026-09-05-entry-point-alerts.md`). Replaces the old
"±10% in 12h" threshold alerts with 5 technical entry-point setups, scanning
both the watchlist and current holdings. All tests pass. **Not yet
live-verified end-to-end** — needs a real run against real tickers to confirm
a genuine setup match produces a correctly-formatted Telegram photo+caption
alert (the logic is fully covered by offline tests, but nothing has sent a
real chart through the real bot yet).

**Built and running.** Every command in the README table works end-to-end against the
live bot, including `Analyse` — added 2026-08-08, live-verified with real reports, and
cost-tuned down from $6.53 to ~$1-1.50/report (see
`docs/plans/2026-08-08-analyse-company-command.md` for the full history).

**Removed: daily politician-trades digest.** Built 2026-08-08 (House + President Trump
disclosures, PDF extraction via Claude Haiku, deterministic scoring, a `select_picks`
reply flow that ran `Analyse`-style reports on chosen picks), fully tested and deployed —
then removed 2026-09-05. Full context (why it was built, the sourcing decisions like
whitehouse.gov over Quiver Quantitative) is in git history if this ever comes up again;
not reproduced here since the code and design doc are gone.

**Next up: share quantity tracking.** This is the biggest known gap. A holding records
what I paid *per share* but not how many shares, so every profit/loss figure is a
percentage rather than a real dollar amount. Adding quantity would touch:

- the `holdings` table (new column, via `db.migrate()`)
- `app/parser.py` — to understand "Bought 10 Apple"
- `app/commands/purchase.py` and `app/commands/sell.py` — dollar totals instead of
  per-share deltas
- the `/list` reply

Build it tests-first. A "realized gains" summary is also wanted eventually, but it comes
*after* this — quantity changes what those numbers would even mean.
