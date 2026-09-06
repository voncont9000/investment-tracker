# Investment Tracker — project memory

Read this first, every session.

## What this project is

A personal Telegram bot that tracks a stock watchlist and the stocks I own. It flags a
stock reaching a technically attractive **entry point** — one of 5 setups based on price
structure (moving averages, multi-day returns, pullback depth) — across both lists, and
flags reasons to **consider selling** a holding: 3 P&L rules against cost basis, 2
technical exit setups, and a weekly Claude-driven thesis check against recent news.
It can also run a full equity research report on demand. Single user (me), driven by
plain English messages, running as a systemd service.

## Tech stack

- **Python 3.14**, `python-telegram-bot` 22.5 — long-polling, one process, no webhook or
  web server. The recurring price check runs on its built-in JobQueue.
- **`yfinance`** for prices and financials (free, no API key), **`rapidfuzz`** for matching
  company names to tickers.
- **`anthropic`** (Claude API): `claude-sonnet-5` for the `Analyse` report (+
  `web_search`/`web_fetch`) and the weekly sell-thesis check (structured output via
  `messages.parse`, no tools). Optional: everything else works with no key.
- **`exa-py`** (Exa search API) does the news retrieval for the weekly thesis check —
  deliberately not Claude's own `web_search`; see the "why Exa" design decision below.
  Optional, alongside `ANTHROPIC_API_KEY`: without both, only the thesis sweep is disabled.
- **`matplotlib`** renders the price chart attached to every price/technical alert.
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
| `bot.py` | Entry point — builds the app, registers handlers, schedules the 15-minute price-poll job, the once-a-day daily-bar cache refresh job, and the weekly sell-thesis sweep job |
| `app/parser.py` | Turns a plain message ("Bought Apple") into an intent |
| `app/commands/` | One file per intent, wired up in `__init__.py` |
| `app/db.py` | Schema and every SQL query — all database access lives here |
| `app/prices.py` | Fetching prices, live quotes, and headline timestamps from Yahoo Finance |
| `app/fundamentals.py` | Financial statements + multiples for the `Analyse` report |
| `app/analysis.py` | The Claude API call for `Analyse` (prompt, tools, report splitting) |
| `app/technicals.py` | Daily-bar caching and technical metric math (moving averages, returns, breakout detection) shared by entry and exit setups |
| `app/setups.py` | The 5 entry-point setup checks (pure functions: `TickerMetrics` in, a match or `None` out) |
| `app/exits.py` | The 3 P&L rules and 2 technical exit setups for held tickers, same shape as `setups.py` |
| `app/setup_thresholds.py` | Numeric thresholds for the 5 entry setups and 2 exit setups, hardcoded and tunable by editing the file |
| `app/charts.py` | Renders the price+moving-average chart attached to each price/technical alert |
| `app/alerts.py` | Orchestrates: builds metrics per ticker, runs entry setups and (for holdings) exit checks, applies once-per-episode dedup, suppresses entry alerts when an exit fires |
| `app/news.py` | One deterministic Exa search per ticker for the weekly thesis check — no Claude tool loop involved |
| `app/thesis.py` | The weekly sweep: a free gate (price move / new headlines / 4-week floor), the Claude verdict call, and its own dedup |
| `tests/` | 216 tests, no network needed — they run in under two seconds |

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
  checked. But the ~35 numeric thresholds behind the 5 entry-point setups
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
- **Sell alerts (added 2026-09-06) suppress entry alerts on the same ticker,
  not the other way round.** A held ticker can satisfy an entry setup and an
  exit rule in the same poll — entry Setup 1 ("uptrend, now pulling back") is
  often literally the start of a trend break. If any exit rule is currently
  true for a ticker, that poll's entry messages for it are dropped; entry
  `alert_state` is still updated underneath so a suppressed entry doesn't
  queue up and fire the moment the exit clears. Full design:
  `docs/superpowers/specs/2026-09-05-sell-alerts-design.md`.
- **Cost basis is an unweighted average across a ticker's lots (`db.avg_cost_by_ticker`),
  and the trailing-stop peak is the max of the entire cached daily-bar history, not
  "since purchase."** Both are simplifications forced by today's schema: there's no share
  quantity (so a weighted average isn't possible yet — see quantity tracking below), and
  `TickerMetrics.daily_closes` is a plain list of closes with no date attached to each bar,
  so "since this lot's purchase date" isn't answerable without adding per-bar dates. Using
  the full ~2-year cache instead only matters for a holding bought more than 2 years ago,
  and even then it just makes the trailing stop arm later — never fires on a peak that
  didn't happen.
- **Sold-ticker cleanup is the one deliberate exception to "nothing is ever deleted."**
  Selling the last lot of a ticker deletes its `alert_state` rows (already true before this
  feature) and now also its `thesis_check_state` row. That rule protects *history* — what I
  bought, sold, and when; alert/check state is neither history nor useful to a future gains
  view, it's a latch on the current moment, and a stale latch would suppress a real alert
  the next time I buy the same ticker.
- **The weekly thesis check uses Exa for news retrieval, not Claude's own `web_search`
  (used by `Analyse`).** Per-search price is nearly identical ($0.007 vs $0.01); the real
  saving is structural. `web_search` is agentic — the model searches, reads, searches again,
  resending the growing conversation each turn (~30K tokens across 3 turns, ~$0.11/check).
  Retrieving once in Python and making a single Claude call collapses that to ~$0.033/check
  — the same "accumulated context is the real cost driver" lesson `Analyse`'s own tuning
  found. Exa's `start_published_date` also gives real recency control that a general web
  search doesn't. `Analyse` keeps `web_search` — its research is genuinely open-ended, which
  a fixed set of retrieved snippets can't support.
- **The thesis verdict uses structured output (`client.messages.parse` +
  `output_format=ThesisVerdict`), not prose-splitting like `Analyse`'s `---` delimiter.** A
  schema-validated `HOLD`/`CONCERN` enum can't be silently misread as the other value the
  way a malformed text split could.
- **A free gate decides which holdings get a paid thesis check each week** — earnings
  move, a new headline, or a 4-week floor so nothing goes unchecked forever. Cuts the
  weekly bill roughly 70% by skipping quiet holdings. Considered dropping this gate when
  Exa's price made checking everyone weekly only ~$17/year, but kept it per my own call.
- **PTB's `run_daily(days=...)` numbers 0-6 as Sunday-Saturday, not Monday-Sunday** (changed
  in PTB 20.0) — confirmed against the installed version before relying on it, since getting
  this backwards would silently run the weekly sweep on the wrong day with no error.
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

**Both the entry-point redesign and sell alerts are now live and verified —
deployed 2026-09-06.** Both had sat committed-but-unpushed on `main` since
their respective build sessions; this session pushed everything, deployed to
the production server, ran the `alert_state`/`thesis_check_state` migration
against the real database (29 watchlist items, 4 holdings — all survived),
and confirmed real alerts fire: the first poll after restart sent 3 real
chart alerts to Telegram. Also fixed on the live box: the systemd unit file
was missing the `MPLCONFIGDIR` line (see below), causing harmless but noisy
matplotlib permission warnings on every restart.

**Every alert message now says BUY or SELL up front (2026-09-06).** Before
this, an entry alert and an exit alert looked the same at a glance — both
just led with a 🔔 bell and the setup name (e.g. "Trend Break" reads nothing
like a sell signal on its own). Every message now starts with 🟢 BUY or 🔴
SELL. `SetupMatch` (app/setups.py) has no `direction` field and defaults to
"BUY" via `getattr` in `alerts._format_message`; `ExitMatch` (app/exits.py)
sets `direction: str = "SELL"` explicitly, same pattern as its `soft_tag`
field.

**Sell alerts added 2026-09-06** (see
`docs/superpowers/specs/2026-09-05-sell-alerts-design.md`). Three P&L rules
against average cost (take-profit, stop-loss, trailing stop — `.env`-tunable),
2 technical exit setups (Trend Break, Momentum Breakdown), and a weekly
Claude+Exa thesis check that only speaks up on `CONCERN`. Exit alerts
suppress that poll's entry alerts on the same ticker. All 216 tests pass.

**Alert engine redesigned — added 2026-09-05** (see
`docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md` and
`docs/superpowers/plans/2026-09-05-entry-point-alerts.md`). Replaces the old
"±10% in 12h" threshold alerts with 5 technical entry-point setups, scanning
both the watchlist and current holdings.

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
- `db.avg_cost_by_ticker` (added for the sell alerts above) — becomes a share-weighted
  average instead of an unweighted one; the only change the sell-alert P&L rules need

Build it tests-first. A "realized gains" summary is also wanted eventually, but it comes
*after* this — quantity changes what those numbers would even mean.
