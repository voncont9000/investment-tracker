# Investment Tracker — project memory

Read this first, every session.

## What this project is

A personal Telegram bot that tracks a stock watchlist and the stocks I own, and messages
me when a watched stock **drops ≥10%** or a stock I hold **rises ≥10%** within 12 hours.
It also sends a daily digest of politician stock trades and can run a full equity research
report on demand or from that digest. Single user (me), driven by plain English messages,
running as a systemd service.

## Tech stack

- **Python 3.14**, `python-telegram-bot` 22.5 — long-polling, one process, no webhook or
  web server. The recurring price check runs on its built-in JobQueue.
- **`yfinance`** for prices and financials (free, no API key), **`rapidfuzz`** for matching
  company names to tickers.
- **`anthropic`** (Claude API): `claude-opus-5` (+ `web_search`/`web_fetch`) for the
  `Analyse` report and for the picks selected from the politician-trades digest;
  `claude-haiku-4-5` (structured outputs, native PDF input) for turning a scanned
  disclosure PDF into structured trade data. Optional: everything else works with no key.
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
| `app/alerts.py` | Threshold checks and the "only alert once per move" logic |
| `app/politician_trades/sources/` | Trade-disclosure adapters — `house_clerk.py`, `whitehouse.py` (free, live), `senate.py` (stub) |
| `app/politician_trades/pdf_extract.py` | Scanned PTR PDF → structured trades, via Claude Haiku |
| `app/politician_trades/scoring.py` | Deterministic ranking of disclosed purchases — no LLM call |
| `app/politician_trades/digest.py` | Orchestrates the 7am digest: fetch → extract → score → message |
| `app/commands/picks.py` | Handles the digest reply ("1 3" / "all" / "skip") and runs the Analyse pipeline on picks |
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
- **Alert sensitivity is configurable, not hardcoded.** `WATCHLIST_DROP_THRESHOLD` (-0.10),
  `HOLDING_GAIN_THRESHOLD` (+0.10), `TRAILING_WINDOW_HOURS` (12), and
  `POLL_INTERVAL_MINUTES` (15) all default in `app/config.py`. Tuning them is a `.env`
  edit plus a service restart.
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
- **The politician-trades digest never spends on Opus by itself.** Screening every morning's
  disclosed purchases is pure Python arithmetic (amount band, filing speed, whether
  multiple members bought the same ticker) — no LLM call. The only Anthropic call in the
  screening path is Claude Haiku reading a scanned PTR PDF into structured data, which
  costs a fraction of a cent. The expensive `Analyse`-style deep dive only runs on the
  picks you explicitly select from the digest reply, so most days cost $0 beyond the Haiku
  PDF reads. See `docs/plans/2026-08-08-politician-trades-digest.md`.
- **Congressional disclosure PDFs are scanned images, not text** — confirmed by
  decompressing every content stream in a real filing and finding zero text-drawing
  operators. Rather than add an OCR dependency (pytesseract + a system poppler/tesseract
  install), the PDF is sent straight to Claude, which reads scanned documents natively.
- **No free source exists for Senate trade disclosures.** The Senate's own site
  (efdsearch.senate.gov) 403s automated access even with a normal browser User-Agent, and
  every free third-party mirror that used to cover it is dead (Senate Stock Watcher's data
  hasn't updated since 2021; Capitol Trades' internal API returned 503 in testing). The
  House Clerk's own site is the one source that's free, official, and actually works.
  Rather than pay for a provider (~$75/mo) up front, `app/politician_trades/sources/` is a
  small adapter interface — `senate.py` is a documented no-op stub, wireable to a real
  provider later without touching the rest of the pipeline.
- **The digest ranks purchases only, not sales.** A member selling isn't a "recommended
  trade" the way this feature is framed — sales are still fetched and stored so nothing is
  silently dropped, just excluded from the ranked shortlist.
- **The President's trades come from whitehouse.gov, not a scraped Quiver Quantitative
  page.** I was asked to add Trump's trades from Quiver's "Donald Trump Stock Trades"
  tracker. Declined that specific approach: Quiver's Terms of Service explicitly ban
  automated access ("Use any robot, spider... for any purpose") and forbid redistributing
  their data, and their trade table isn't even server-rendered — it loads via a
  `robots.txt`-disallowed endpoint that needs a paid session, so a scraper wouldn't have
  worked anyway. The underlying data is public regardless: the President's trades are OGE
  Form 278-T filings (same disclosure law as the House/Senate PTRs, just the executive-branch
  form), and the White House publishes every one directly and freely at
  `whitehouse.gov/disclosures/` — no ToS restriction, open `robots.txt`, same scanned-PDF
  situation as House Clerk filings. `app/politician_trades/sources/whitehouse.py` scrapes
  *that* page instead, filtered to just Trump's own filings (the page also lists PTRs for
  many White House staff, out of scope for this feature). `RawFiling.chamber` gained an
  `"Executive"` value alongside `"House"`/`"Senate"` — no schema change needed, since
  `daily_picks.chamber` was already free text.

## Where things stand, and what's next

**Built and running.** Every command in the README table works end-to-end against the
live bot, including `Analyse` — added 2026-08-08 (see
`docs/plans/2026-08-08-analyse-company-command.md`). **Not yet live-verified end-to-end**:
the API key in `.env` at the time this was built returned 401 (invalid/expired) — the
request shape itself was confirmed against the real API, but no full report has actually
been generated and sent through the bot yet. Do that once a working key is in place.

**Daily politician-trades digest — added 2026-08-08, House + President Trump** (see
`docs/plans/2026-08-08-politician-trades-digest.md`). All 150 tests pass; the job is wired
into `bot.py`'s JobQueue. **Not yet live-verified end-to-end** — same blocker as `Analyse`
above (no working API key at build time), plus this needs a first real run against the
live House Clerk and whitehouse.gov feeds to confirm a real digest message and reply flow
work outside of mocks. Senate coverage is intentionally not built — no free source exists
until `SENATE_SOURCE_PROVIDER` is wired up (see the design decision above).

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
