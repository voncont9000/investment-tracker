# Sell-side monitoring and alerts

Date: 2026-09-05

## Objective

Tell me when to consider getting **out** of something I hold. The
entry-point redesign
([2026-09-05-entry-point-alerts-design.md](2026-09-05-entry-point-alerts-design.md))
covers getting in; this is the other half.

Three independent sources of sell signal, all scoped to active holdings:

1. **P&L rules** against my cost basis — take-profit, hard stop-loss,
   trailing stop.
2. **Technical exit setups** — trend break and momentum breakdown, in the
   same style as the entry setups.
3. **A weekly thesis check** — Claude reads recent news on each holding and
   says whether the reason to own it still stands.

This is purely additive. Nothing in the entry-point design changes.

## Dependency on the entry-point redesign

**This spec assumes the entry-point redesign has shipped.** It reuses that
work rather than duplicating it: `TickerMetrics`, the daily-bar cache, the
15-minute poll, `app/charts.py`, and the `alert_state` episode-dedup
pattern. Implementing this before the entry redesign is not viable — the
technical exits and the trailing-stop peak both read from the daily-bar
cache that redesign introduces.

## Scope decisions (confirmed with the user)

- **Universe:** active holdings only. The entry setups already scan
  holdings for add-to-position entries; exits never apply to the watchlist.
- **Multiple lots:** collapsed to one **average cost per ticker** — the
  unweighted mean of `purchase_price` across a ticker's active lots. One
  cost basis, one peak, one alert per ticker per rule. Unweighted because
  the schema has no share quantity yet; it is exact only when lots are
  equal-sized (see "Interaction with share quantity tracking").
- **P&L thresholds live in `.env`,** unlike the entry setups' ~40
  hardcoded constants. There are only three and they are exactly the
  numbers worth tuning without editing code.
- **Technical exit thresholds are hardcoded constants,** consistent with
  the entry setups, in `app/setup_thresholds.py`.
- **Thesis check uses Exa for retrieval, not Claude's `web_search`.** See
  "Why Exa" below. `Analyse` is untouched and keeps `web_search`.
- **A free pre-filter gates the thesis check,** so a quiet holding costs
  nothing, with a 28-day floor so nothing goes stale.
- **Soft conditions** follow the entry-point convention: they change the
  alert's label, never whether it fires.

## Architecture

| File | Role |
|---|---|
| `app/exits.py` (new) | Pure functions: three P&L rules and two technical exit setups. `check_exit_*(...) -> ExitMatch \| None` |
| `app/news.py` (new) | Exa client — one `search_and_contents` call per ticker, date-filtered. No Claude involvement |
| `app/thesis.py` (new) | The weekly sweep: the free gate, the Claude verdict call, and dedup |
| `app/alerts.py` | Extended with an exit pass over holdings, plus the entry-suppression precedence rule |
| `app/technicals.py` | Gains `daily_highs` on `TickerMetrics`; held tickers fetch history back to purchase date |
| `app/setup_thresholds.py` | Gains the technical exit constants (`EXIT_A_*`, `EXIT_B_*`) |
| `app/db.py` | Six new `alert_state` types; new `thesis_check_state` table; average-cost helper |
| `app/config.py` | Three new `.env` settings plus optional `EXA_API_KEY` |
| `bot.py` | Adds the weekly thesis job |

## P&L rules

Cost basis is `avg_cost(ticker)` = mean `purchase_price` over active lots.

| Rule | Fires when | `.env` setting | Default |
|---|---|---|---|
| Take-profit | `price >= avg_cost * (1 + TAKE_PROFIT_PCT)` | `TAKE_PROFIT_PCT` | 0.30 |
| Hard stop-loss | `price <= avg_cost * (1 - STOP_LOSS_PCT)` | `STOP_LOSS_PCT` | 0.20 |
| Trailing stop | `price <= peak * (1 - TRAILING_STOP_PCT)` | `TRAILING_STOP_PCT` | 0.15 |

### The peak is computed, not stored

`peak(ticker) = max(daily_highs since the earliest active lot's
purchase_date, current_price)`, read from the cached daily bars.

Deliberately **not** a stored column updated every poll. Computing it means
there is no state to keep in sync, it survives restarts and gaps in
polling, and it is correct for holdings bought long before this feature
existed — a stored column would start tracking from first run and quietly
under-report the peak for every existing position.

The cost is that held tickers need daily bars back to their purchase date,
not the entry design's fixed ~1 year. `app/technicals.py` therefore fetches
`max(1 year, since earliest active purchase_date)` for held tickers. This
runs once a day in the existing cache-refresh job.

### Trailing-stop arming guard

The trailing stop only arms if `peak >= avg_cost * 1.10`.

Without it, a stock that fell steadily from the day it was bought would
fire a "you've given back 15% of your gains" alert when there were never
any gains — the peak would just be the purchase-day price. The hard
stop-loss is the correct rule for that case. `1.10` is a hardcoded
constant, not `.env`; it is a correctness guard, not a preference.

## Technical exit setups

Constants named `EXIT_A_*` / `EXIT_B_*` in `app/setup_thresholds.py`.

### Exit A — Trend break

The slow signal: the uptrend that justified holding has ended.

Hard (all required):
- was recently in an uptrend: `max(daily_closes[-20:]) > sma50` (it traded
  above the 50-day at some point in the last 20 completed sessions)
- `current_price < sma50 * 0.98` (a clear break, not a 1% poke)
- `daily_closes[-1] < sma50` and `daily_closes[-2] < sma50` (the last two
  *completed* sessions confirm it, so one bad afternoon can't trigger it)
- `sma50 < sma50_5d_ago` (the 50-day is rolling over, not just being
  brushed)

Soft (upgrades the label to "confirmed"):
- `current_price < sma200`
- `return_n(20) < -0.10`

### Exit B — Momentum breakdown

The fast signal: something happened. Two branches, either one triggers.

*News branch* (hard, all required):
- a single-session drop worse than `-0.07` within the last 3 sessions,
  computed from consecutive `daily_closes`
- `current_price < sma20`

*Slide branch* (hard, all required):
- `return_n(5) <= -0.12`
- `pct_below_high(30) >= 0.15`

Soft (either branch):
- `current_price < sma50`
- `return_n(1) < 0` (still falling, not already bouncing)

Both branches map to the single alert type `exit_momentum_breakdown`; the
message names which branch fired.

## Weekly thesis check

Runs Sunday evening via `job_queue.run_daily(time=..., days=(<Sunday>,))` —
a fixed calendar day rather than a 7-day repeating interval, which would
drift with every process restart.

**Confirm the weekday indexing against the installed python-telegram-bot
version before writing the constant.** `run_daily`'s `days` numbering has
differed across PTB major versions (Sunday-first vs Monday-first), and
getting it wrong schedules the sweep on the wrong day with no error —
it would simply run on a Saturday forever.

The whole sweep runs in a background `asyncio` task. Sequential API calls
across every holding would otherwise stall the price-poll job for minutes,
the same lesson `Analyse` already taught.

### The free gate

Before spending anything, each active holding is checked against four
cheap conditions. It is examined if **any** hold:

- earnings reported within the last 7 days (yfinance `get_earnings_dates`)
- `abs(return_n(5)) >= 0.05` (from the cached daily bars — free)
- a headline newer than `last_headline_at` (yfinance `Ticker.news`)
- `last_checked_at` older than 28 days (the floor — nothing goes stale)

A quiet holding costs nothing. In a typical week roughly a third of
holdings pass.

### Retrieval — `app/news.py`

One Exa `search_and_contents` call per gated ticker:

- query: company name plus ticker, oriented at recent company news
- `start_published_date`: `last_checked_at`, or 7 days ago on first run
- `num_results`: 5, with text content

Cost: $0.007 per search plus $0.001 per page of content ≈ **$0.012** per
ticker.

### Verdict — `app/thesis.py`

One Claude call per gated ticker. **No tools.**

- model `claude-sonnet-5`, `output_config: {"effort": "low"}`
- `max_tokens` 2000
- prompt: ticker, company name, purchase date, average cost, current
  price, % change since purchase, and the Exa snippets
- structured output via `output_config.format`, schema
  `{verdict: "HOLD" | "CONCERN", reason: string, sources: string[]}`

Schema-validated structured output rather than parsing prose — a step up
from `Analyse`'s `---` splitting, and it removes any chance of a
malformed verdict silently reading as `HOLD`.

Cost: ~8K input + ~500 output at Sonnet 5's $2/$10 per MTok ≈ **$0.021**.
Total per gated ticker ≈ **$0.033**; at ten holdings with the gate, roughly
**$0.10–0.15 a week**.

### Why Exa rather than Claude's `web_search`

Per-search prices are nearly identical ($0.007 vs $0.01). The saving is
structural: `web_search` is agentic, so the model searches, reads, searches
again, and every turn resends the accumulated conversation — about 30K
input tokens across three turns, ≈$0.11 per check. Retrieving in Python
collapses that to one turn with nothing accumulating, ≈$0.033. Roughly 90%
of the saving comes from the turn collapse, not the search fee. This is the
same finding as the `Analyse` cost work: accumulated context is the cost
driver, not per-token price.

Two non-cost reasons matter more:

- **Recency control.** `start_published_date` asks for the last N days
  directly, instead of hoping a general web search doesn't return a
  three-year-old article.
- **It matches the pattern already chosen.** `Analyse` fetches the
  deterministic parts in Python and asks the model only for judgment. News
  retrieval by ticker and date is deterministic; whether the news breaks
  the thesis is judgment.

The accepted trade-off: the model sees one fixed set of snippets and
cannot follow a thread it finds interesting. For a weekly screening
verdict that is fine. For `Analyse`, where open-ended research is the
whole point, it would not be — so **`Analyse` keeps `web_search`
unchanged**.

### Sweep dedup

The verdict goes through the same episode dedup as every other alert,
`alert_state` type `thesis_break`. `CONCERN` sends a message only on the
transition from not-in-alert; the state clears silently when a later
verdict returns `HOLD`, re-arming. Without this, one guidance cut would
generate an identical message every Sunday for as long as it stayed true.

`HOLD` verdicts are never messaged. Silence means nothing found.

### Graceful degradation

The sweep needs both `ANTHROPIC_API_KEY` and `EXA_API_KEY`. If either is
missing the weekly job is not scheduled and a single line is logged at
startup. Everything else in the bot — every command, the price poll, the
P&L rules, the technical exits — continues to work with no keys at all,
preserving the project's "optional: everything else works with no key"
rule.

## Messaging, dedup, and precedence

### New alert types

`exit_take_profit`, `exit_stop_loss`, `exit_trailing_stop`,
`exit_trend_break`, `exit_momentum_breakdown`, `thesis_break`.

All follow the existing episode rule: fire on entry into the condition,
clear silently on exit, never repeat within an episode.

### Message shape

Price-based exits send a photo — the same `app/charts.py` renderer as the
entry alerts — captioned with the rule name, the numbers that drove it,
and the position: average cost, current price, % versus cost.

Thesis alerts are text only, carrying the reason and its source links.
There is no chart worth drawing for a news event.

### Precedence: exits suppress entries

A held ticker can satisfy an entry setup and an exit rule in the same
poll. This is not an edge case — entry Setup 1 is "uptrend, now pulling
back", which is the same tape as the start of a trend break.

**If any exit rule fires for a held ticker, entry alerts for that ticker
are suppressed for that poll.** One coherent message beats two
contradictory ones arriving together. Suppression affects only the
message: entry `alert_state` is still updated, so a suppressed entry does
not re-fire later as though it were new.

### Selling clears state

When the last active lot of a ticker is sold, its `alert_state` rows and
its `thesis_check_state` row are deleted. Buying back in later then starts
from a clean slate rather than inheriting a stale "already alerted" flag
that would swallow the first real alert.

This is a deliberate exception to the project's "nothing is ever deleted"
rule. That rule protects *history* — what I bought, at what price, when I
sold. Alert state is neither history nor something a future "total gains"
view could use; it is a latch describing the current moment, and a stale
latch actively suppresses correct alerts.

## Database changes

```sql
CREATE TABLE IF NOT EXISTS thesis_check_state (
    ticker TEXT PRIMARY KEY,
    last_checked_at TEXT NOT NULL,
    last_headline_at TEXT
);
```

`alert_state.alert_type`'s `CHECK` constraint gains the six new values.
SQLite cannot alter a `CHECK` in place, so `db.migrate()` recreates the
table — the entry-point redesign already performs exactly this rewrite for
its five setup types, so **this spec widens the constraint that redesign
introduces rather than performing a second rewrite**. Implemented after
that migration, this is a single edit to the value list it already writes.

New helper: `db.avg_cost_by_ticker(conn) -> dict[str, float]`, averaging
`purchase_price` over rows with `active = 1`.

## Config changes

`Settings` gains `take_profit_pct` (0.30), `stop_loss_pct` (0.20),
`trailing_stop_pct` (0.15), and optional `exa_api_key`. `.env.example`
gains the four entries.

## New dependency

`exa-py` in `requirements.txt`, used only by `app/news.py`.

## Interaction with share quantity tracking

CLAUDE.md names share quantity as the next planned feature. The two
interact but do not block each other:

- The P&L rules here are percentage-based and work correctly today.
- When quantity lands, `avg_cost_by_ticker` becomes a **share-weighted**
  average — a change confined to that one function — and the exit messages
  can quote dollar amounts alongside percentages.

Nothing else in this design depends on quantity. Building this first is
safe; building quantity first is also safe.

## Testing approach

All offline against synthetic data, no network, consistent with the
existing suite.

- `tests/test_exits.py` — the three P&L rules and two exit setups against
  synthetic `TickerMetrics` and lot lists. Each hard condition
  individually falsified; each soft condition on and off (label changes,
  trigger state does not). Specific cases: the trailing stop not arming
  below the `peak >= avg_cost * 1.10` guard; average cost across two and
  three lots; both branches of Exit B independently; Exit A not firing on
  a single sub-SMA50 close.
- `tests/test_thesis.py` — the gate (each of the four conditions
  individually sufficient; all four false skips the ticker), verdict
  handling against a stubbed structured response, and the `CONCERN` →
  `HOLD` → `CONCERN` dedup transition. Exa and Claude clients both
  stubbed.
- `tests/test_news.py` — the Exa query and `start_published_date` are
  built correctly from a holding's last-check date, against a stubbed
  client.
- `tests/test_alerts.py` — the exit pass, the entry-suppression precedence
  rule, and state clearing on sell.
- `tests/test_db.py` — the widened `alert_type` constraint, the
  `thesis_check_state` table, and `avg_cost_by_ticker`.