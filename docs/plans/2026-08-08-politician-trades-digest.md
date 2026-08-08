# Daily politician-trades digest

## Context

The bot already has an on-demand `Analyse COMPANY` command (Opus 5 + web search/fetch,
delivering a quick-take message plus a full `.md` report). This feature adds a second,
*proactive* surface on top of the same research pipeline: every morning the bot looks at
what members of Congress disclosed trading in the last 24 hours, scores the purchases
itself (cheaply, deterministically), and messages a ranked shortlist. You then reply with
which ones are worth the full Opus deep-dive, and the bot runs exactly the existing
`Analyse`-style pipeline on just those.

Two things surfaced during research that shape the design:

1. **"Trades placed in the last 24h" isn't real data — "disclosed in the last 24h" is.**
   The STOCK Act gives members up to 45 days to file. The digest is built on *filing* date,
   with the actual *trade* date shown alongside it, so the lag is never hidden.
2. **Most of the well-known free congressional-trades APIs are dead.** Tested live:
   House Stock Watcher (403), Senate Stock Watcher on GitHub (last commit 2021), Capitol
   Trades' internal API (503), Senate's own site (403, Akamai-blocked). The one source that
   actually works is the **official House Clerk site** — a daily-refreshed ZIP/XML index,
   free, no key. There's no working free equivalent for the Senate. Rather than pay for
   Quiver/FMP (~$75/mo) up front, the trade source is a small adapter interface: House
   Clerk ships working today, Senate is a documented stub you wire up later if it's worth
   it to you.

## Cost approach

Instead of trying to make the Opus research cheap, the shape of the feature makes it cheap:
**the bot never runs a full Opus report automatically.** It screens every disclosed
purchase with a deterministic Python score (no LLM call at all for screening), sends a
short digest of the top 3, and only runs `analysis.py`'s existing Opus pipeline on the ones
explicitly picked in the reply. Most days this is $0 in Opus spend unless asked for.

The one place an LLM is used cheaply is turning a scanned PTR PDF into structured trade
data — tested live, these PDFs have no extractable text (image-based scans; decompressing
every content stream in a sample PDF found zero text-drawing operators), so OCR would mean
a new dependency (pytesseract + poppler). Instead: the PDF goes straight to Claude
**Haiku 4.5** ($1/$5 per Mtok) with a JSON schema (structured outputs) and comes back as
ticker, buy/sell, amount band, trade date. At ~30 filings on a heavy day this is well under
$0.10/day.

**Assumption:** the shortlist ranks **purchases only** — a member selling isn't a
"recommended trade" in the sense this feature is framed around. Sales are still fetched
(so nothing is silently dropped) but excluded from ranking.

## Architecture

```
07:00 Europe/London (JobQueue.run_daily, DST-aware)
  -> digest.run_daily_digest()
       -> sources.house_clerk.fetch_recent_filings()   [free, XML index]
       -> sources.senate.fetch_recent_filings()        [stub -> []  until configured]
       -> pdf_extract.extract_trades(pdf)               [Haiku + structured output, per new filing]
       -> scoring.rank_purchases()                      [pure Python, no LLM]
       -> save top 3 (or fewer) to daily_picks table
       -> send Telegram digest message, or "no qualifying filings" if none

Reply "1 3" / "all" / "skip"
  -> parser "select_picks" intent -> app/commands/picks.py
       -> for each selected pick: same ack -> asyncio.create_task -> analysis.run_analysis()
          pipeline as the existing Analyse command -> quick take + .md file
```

### Trade-source adapter (`app/politician_trades/sources/`)

- `base.py` — `TradeSource` protocol: `fetch_recent_filings(since: date) -> list[RawFiling]`,
  where `RawFiling` is `(doc_id, chamber, politician_name, filed_date, pdf_url)`.
- `house_clerk.py` — downloads `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip`,
  parses the XML, filters `FilingType == "P"` (Periodic Transaction Report) filed on or
  after `since`. Checks both `since.year` and the current year to handle the January
  year-boundary edge case.
- `senate.py` — same interface, returns `[]` unconditionally. Docstring explains why
  (Senate's own site 403s automated access, every free mirror is dead) and how to wire a
  real provider later (`SENATE_SOURCE_PROVIDER`, currently unused) without touching
  anything else in the pipeline.

### PDF → structured trades (`app/politician_trades/pdf_extract.py`)

One Claude Haiku 4.5 call per new filing: the PDF as a `document` content block (base64,
native PDF understanding, no OCR library), `output_config.format` with a JSON schema
returning `{trades: [{asset_description, transaction_type, amount_band, trade_date}]}`.
Same error-swallowing style as `app/fundamentals.py` — a filing that fails to download or
parse is skipped, never crashes the digest.

### Scoring (`app/politician_trades/scoring.py`)

Pure functions, unit-tested without network:

- **Amount band score** — STOCK Act disclosures use fixed bands (`$1,001 - $15,000` …
  `Over $50,000,000`); each band maps to a fixed 1–10 score (parsing and midpointing a
  free-text range would be less reliable than matching the exact checkbox strings the PTR
  form uses).
- **Freshness score** — exponential decay on the gap between trade date and filing date
  (14-day half-life), so a trade disclosed fast scores higher than one filed near the
  45-day deadline.
- **Cluster bonus** — when more than one politician bought the same ticker in the current
  batch, each extra buyer adds a fixed bonus (a conviction signal) and the digest line says
  so explicitly.
- Purchases only feed the ranking; sales pass through unscored. Ticker resolution reuses
  `app.ticker_resolver.resolve_ticker` — the same fuzzy-match/Yahoo lookup every other
  command uses, so a PTR's free-text asset description ("Apple Inc. Common Stock") resolves
  the same way "Analyse Apple" would.

### New DB tables (`app/db.py`)

- `processed_filings(doc_id TEXT PRIMARY KEY, source TEXT, processed_at TEXT)` — a re-run
  of the daily job (crash/restart) never reprocesses or re-shows a filing.
- `daily_picks(id, pick_date, rank, ticker, company_name, politician_name, chamber,
  transaction_type, amount_band, trade_date, filed_date, score, status, report_path)` —
  `status` starts `'pending'`, flips to `'selected'`/`'skipped'` once a reply resolves it.

### Interactive selection

- `app/parser.py` — a narrow pattern matching only digits/"all"/"skip"/"none"
  combinations (`1`, `1 3`, `1, 3`, `1 and 3`, `all`, `skip`), intent `"select_picks"`.
  Anchored to the whole message on purpose so it can never swallow a company name.
- `app/commands/picks.py` — looks up today's `daily_picks` rows with `status='pending'`;
  replies "no pending picks" if none exist, so an unrelated numeric message never silently
  misfires. For each selected ticker, runs the **exact same**
  `fundamentals.build_fundamentals_snapshot` + `analysis.run_analysis` +
  `analysis.split_report` pipeline `app/commands/analyze.py` already uses — `analysis.py`
  itself is untouched. Reports save to `reports/politician-trades/{TICKER}-{date}.md`.

### Scheduling (`bot.py`)

```python
application.job_queue.run_daily(
    politician_trades_digest_job,
    time=dt_time(digest_hour, digest_minute, tzinfo=ZoneInfo(settings.timezone)),
    name="politician_trades_digest",
)
```
`zoneinfo` is stdlib (Python 3.14) — no new dependency. `python-telegram-bot`'s JobQueue
(APScheduler-based) handles DST correctly for a `tzinfo`-aware time.

### Config (`app/config.py`, `.env.example`)

- `TIMEZONE` (default `"Europe/London"`).
- `DAILY_DIGEST_TIME` (default `"07:00"`).
- `SENATE_SOURCE_PROVIDER` — optional, unset by default; `senate.py` stays a no-op stub
  until it's configured.

## Files

- **New package `app/politician_trades/`**: `__init__.py`, `sources/__init__.py`,
  `sources/base.py`, `sources/house_clerk.py`, `sources/senate.py`, `sources/whitehouse.py`,
  `pdf_extract.py`, `scoring.py`, `digest.py`.
- **New:** `app/commands/picks.py`.
- **Modified:** `app/parser.py` (select_picks intent), `app/commands/__init__.py`
  (registry line), `app/db.py` (2 new tables + query helpers), `app/config.py`
  (`timezone`, `daily_digest_time`, `senate_source_provider`), `bot.py` (register the
  daily job).
- **Modified:** `.env.example`, `.gitignore` (`reports/**/*.md` — the digest writes into a
  `reports/politician-trades/` subfolder), `README.md`, `CLAUDE.md`.

## Tests (no network)

- `tests/test_scoring.py` — band scoring, freshness scoring, cluster bonus, ranking.
- `tests/test_pdf_extract.py` — mocked `requests` + Anthropic client, degrades gracefully.
- `tests/test_house_clerk_source.py` — mocked `requests`, filters `FilingType`/date window.
- `tests/test_senate_source.py` — confirms the stub is a no-op.
- `tests/test_whitehouse_source.py` — mocked `requests`, filters to Trump's PTRs only
  (excludes staff filings and annual reports), parses dotted filename dates.
- `tests/test_digest.py` — end-to-end orchestration with every dependency mocked: quiet-day
  message, ranked digest, filing dedup, purchases-only filtering, unresolvable-ticker
  skip, missing-API-key skip.
- `tests/test_parser.py` — `select_picks` parsing (numbers, `all`, `skip`/`none`,
  dedup+sort) and non-collision with existing commands.
- `tests/test_db.py` — `processed_filings` / `daily_picks` CRUD.
- `tests/test_picks_command.py` — the reply-dispatch path (mirrors `test_commands.py`).

## Verification

1. `.venv/bin/python -m pytest tests/ -q` — all tests pass.
2. **Live check (can't be done from tests):** with `ANTHROPIC_API_KEY` set, manually
   invoke `digest.run_daily_digest()` against the live House Clerk feed and confirm a real
   digest arrives in Telegram (or the "no qualifying filings" message on a quiet day).
   Reply with a selection and confirm the quick-take + `.md` file arrive. Confirm the 7am
   job fires at the right wall-clock time in `Europe/London`.

## Addendum (same day): President Trump's trades, via whitehouse.gov not Quiver

Asked to also pull trades from Quiver Quantitative's `Donald-Trump-Stock-Trades` page.
Investigated before building anything:

- **Quiver's Terms of Service explicitly prohibit it**, for any purpose, not just
  commercial: *"Use any robot, spider, or other automatic device, process, or means to
  access the Website... for any purpose, including monitoring or copying any of the
  material"* — plus a separate ban on redistributing "Quiver Data" at all.
- **It wouldn't have worked anyway.** Pulled the raw HTML: the trade table
  (`id="tradeTable"`) ships with zero data rows. The real data loads via a client-side call
  to an endpoint `robots.txt` disallows (`/get_*`, `/api/`), almost certainly gated behind
  their paid account. A scraper would just get an empty table.
- Quiver's own page points at the intended path: *"Use the Quiver API to track Donald
  Trump's stock trades for your own analysis or use case"* — a paid product, same tier
  already declined for Senate coverage.

The underlying data is public regardless of Quiver's paywall on top of it. The President
isn't in Congress, so his trades don't come through PTR filings under the STOCK Act — they
come through **OGE Form 278-T**, the equivalent periodic-transaction disclosure required of
the executive branch under the Ethics in Government Act. The White House publishes every
one directly: **whitehouse.gov/disclosures/** — open `robots.txt`, plain server-rendered
HTML linking straight to dated PDFs, no ToS restriction on access. Confirmed live: same
scanned-image situation as the House Clerk PDFs (has `/Image`, no `/Font`, zero
text-drawing operators), so `pdf_extract.py`'s existing Claude Haiku pipeline needed no
changes — it doesn't care which source found the PDF.

**New:** `app/politician_trades/sources/whitehouse.py` — same `TradeSource` interface as
`house_clerk.py`. The disclosures page lists PTRs for dozens of White House staff, not just
the President, so it filters to filenames containing "trump" + "periodic-transaction-report"
and excludes "annual" (the yearly Form 278 holdings disclosure, a different document).
Dates come from the filename (`...-4.20.26.pdf`, `...-05.08.26-1.pdf`) rather than
structured metadata — a regex anchored to the last three dot-separated number groups before
`.pdf` handles the occasional inconsistently-formatted filename
(`...-0.6.25.26-1.pdf` → parses as `06.25.26`, ignoring the stray leading group).

**Modified:** `app/politician_trades/digest.py` (added `whitehouse` to the source list),
`app/politician_trades/sources/base.py` (`RawFiling.chamber` comment now includes
`"Executive"` — no schema/type change, since `daily_picks.chamber` was already free text),
`README.md`, `CLAUDE.md`.

**New tests:** `tests/test_whitehouse_source.py` (6 tests); `tests/test_digest.py` gained
one new test plus every existing orchestration test now mocks the third source too.
150 tests total, still 0 network calls.

**Known limitation, accepted for a personal tool:** amendment filings
(`...-Amendment-...pdf`) are processed as their own filing rather than being matched back
to the original they amend, so an amended trade could be counted (and scored) twice. Not
worth the added complexity to dedupe by trade content rather than by filing — the ranking
is a screening aid, not a ledger.
