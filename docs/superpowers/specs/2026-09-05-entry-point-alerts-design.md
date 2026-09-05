# Entry-point alert redesign

Date: 2026-09-05

## Objective

Replace the current alert engine — a single trailing-window % check
("watched stock dropped ≥10% in 12h" / "held stock rose ≥10% in 12h") —
with five technical "entry point" setups that look at daily price
structure (moving averages, multi-day returns, pullback depth, breakout
retests) rather than raw intraday moves. The goal is to flag *temporary,
technically attractive* entries into a stock with a strong or improving
price structure — not to make a fundamental call, and not to fire on
ordinary daily noise.

This replaces the existing rules entirely. Nothing from the old system
(`WATCHLIST_DROP_THRESHOLD`, `HOLDING_GAIN_THRESHOLD`,
`TRAILING_WINDOW_HOURS`, the `watchlist_drop`/`holding_gain` alert types)
survives.

## Scope decisions (confirmed with the user)

- **Universe:** both the watchlist and current holdings are scanned with
  the same 5 setups (a held stock can still show a good add-to-position
  entry).
- **Soft ("prefer"/"ideally") conditions:** never block a trigger. Each
  setup has a set of *hard* conditions (all must hold) and a set of
  *soft* conditions (any that hold upgrade the alert's label, e.g. "ideal
  signal") — matching how the rules are phrased ("prefer an alert
  when...").
- **Setup 3 (Breakout Retest):** implemented with a concrete
  resistance/breakout/retest algorithm (below), not simplified or
  deferred.
- **Data cadence:** daily OHLC history is fetched and cached once a day
  (at startup, then every 24h); the existing 15-minute poll job re-fetches
  only the live current price + today's open/intraday-low and
  re-evaluates all 5 setups against the cached daily metrics + fresh
  price. Avoids re-pulling a year of history from yfinance every 15
  minutes while keeping same-day responsiveness (e.g. "today turned
  positive").
- **Thresholds:** the ~40 numeric parameters across the 5 setups are
  hardcoded as named Python constants (not `.env` variables) — too many
  to expose sanely as environment config for a personal bot. `.env`
  keeps `POLL_INTERVAL_MINUTES` (poll cadence) untouched.
- **Message + chart:** each triggered setup sends one Telegram message —
  a caption (ticker, setup name/labels, key numbers) plus a chart image
  (trailing ~6 months of daily closes with the relevant moving averages
  overlaid, current price marked). Requires adding `matplotlib` as a new
  dependency (approved).

## Architecture

| File | Role |
|---|---|
| `app/technicals.py` (new) | Fetches & caches daily OHLC bars per ticker (in-memory, refreshed daily); computes `TickerMetrics` — SMA20/50/200, N-day returns, recent highs, breakout/retest detection |
| `app/setups.py` (new) | Five pure functions, `check_setup_1`..`check_setup_5(metrics) -> SetupMatch | None`. Each `SetupMatch` carries the setup id/label, whether it's an "ideal"/higher-confidence signal, and the key numbers to put in the message |
| `app/setup_thresholds.py` (new) | All ~40 hardcoded numeric constants, named `SETUP{n}_{FIELD}`, one place to tune by editing code |
| `app/charts.py` (new) | `render_price_chart(metrics) -> bytes` — PNG via matplotlib: daily close line, SMA20/50/200 overlays (whichever have enough history), current price marker |
| `app/alerts.py` (rewritten) | Orchestrates: for every ticker in `watchlist ∪ holdings`, builds `TickerMetrics`, runs all 5 setup checks, applies per-(ticker, setup) episode dedup, sends message+chart for new triggers |
| `app/config.py` | Drops `watchlist_drop_threshold`, `holding_gain_threshold`, `trailing_window_hours` from `Settings` |
| `bot.py` | Adds a daily cache-refresh job (`run_repeating`, interval=86400, `first=0`) alongside the existing 15-min poll job |
| `app/db.py` | Migrates `alert_state` to the 5 new alert types (see Migration below); removes `price_history` table and the functions that only served the old design |

### `TickerMetrics`

```python
@dataclass
class TickerMetrics:
    ticker: str
    current_price: float
    today_open: float
    today_intraday_low: float
    daily_closes: list[float]   # oldest -> newest, most recent = last *completed* session
    daily_lows: list[float]     # same alignment as daily_closes
    sma20: float | None
    sma50: float | None
    sma200: float | None
    sma50_5d_ago: float | None    # for SMA50 slope
    sma200_20d_ago: float | None  # for SMA200 slope
```

Built from: the cached daily-bar history (today's bar excluded — always
just completed sessions, so MAs and returns aren't skewed by a partial
day) plus a fresh `fast_info` current-price + open + day-low fetch, done
every poll. `TickerMetrics` is what every setup function and the chart
renderer consumes — none of them touch yfinance directly.

### Shared helper functions (`app/technicals.py`)

- `return_n(metrics, n) -> float` — `(current_price - closes[-n]) / closes[-n]`
- `return_n_ending(metrics, n, sessions_back) -> float` — same, but as of
  `sessions_back` sessions ago (for "is this improving?" comparisons)
- `improving(metrics, n) -> bool` — `return_n(metrics, n) > return_n_ending(metrics, n, n)`
- `recent_high(metrics, n) -> float` — `max(closes[-n:] + [current_price])`
- `pct_below_high(metrics, n) -> float`
- `stopped_new_lows(metrics, n=5) -> bool` — `current_price >= min(closes[-n:])`
- `find_breakout_retest(metrics) -> BreakoutInfo | None` — see Setup 3
- `swing_low_before_high(metrics, high_window=30, lookback=90) -> float` —
  finds the session where `recent_high(high_window)` was set, then
  returns the lowest close in the `lookback` sessions before it (used
  for Setup 1's retracement check)

## Setup formulas

All conditions below are **hard** (all required to trigger) unless
listed under "Soft" (upgrades the alert's label only, per the
soft-conditions decision above).

### Setup 1 — Uptrend + Pullback (highest priority)

Hard:
- `current_price > sma50`
- `sma50 > sma200` OR `sma200_20d_ago < sma200` (200D clearly rising)
- `sma50 > sma50_5d_ago` (50D slope positive)
- `return_n(30) ∈ [0.07, 0.30]`
- `pct_below_high(20) ∈ [0.02, 0.10]` OR `pct_below_high(30) ∈ [0.02, 0.10]`
  (either window counts — the spec says "20-day *or* 30-day")
- `return_n(5) ∈ [-0.08, -0.01]`
- retracement ≤ ~0.50: `(recent_high(30) - current_price) / (recent_high(30) - swing_low_before_high(30, 90))`

The spec's "avoid triggering if the stock has fallen >10% in 5 days
unless there's a separate reversal signal" is already enforced by the
`return_n(5) ∈ [-0.08, -0.01]` hard bound above — a >10% (or >8%) 5-day
drop can never satisfy that range, so no separate escape-hatch logic is
needed.

Soft ("ideal signal"):
- `improving(3)`
- `current_price > daily_lows[-1]` (yesterday's low)
- `today_intraday_low > daily_lows[-1]`
- today positive (`current_price > daily_closes[-1]`) after ≥2 preceding
  declining sessions

### Setup 2 — Momentum + First Meaningful Dip

Hard:
- `return_n(30) > 0.15`
- `return_n(10) > 0.07`
- `return_n(5) <= -0.02` (must be a genuine pullback, not 0.5–1% noise —
  no upper-magnitude cap here; the orderly-decline check below is what
  guards against a crash rather than a "first dip")
- `current_price > sma20` and `current_price > sma50`
- `pct_below_high(30) <= 0.10`
- orderly decline: no single-day drop > 7% in the trailing 5 sessions

Soft:
- `return_n(5) ∈ [-0.07, -0.02]` (the tighter "ideal" inner band)

### Setup 3 — Breakout Retest

- `resistance = max(daily_closes[-60:-5])` (established over the 60→5
  sessions back window, leaving room for breakout + retest after it)
- breakout confirmed: `max(daily_closes[-5:]) >= resistance * 1.03`
- retest: `current_price ∈ [resistance * 0.98, resistance * 1.05]` (the
  spec says "0-5% above," but a 2% undershoot tolerance is included so a
  brief wick below the old resistance while it's still being tested as
  support doesn't invalidate the setup)
- support holding: no close since the breakout `< resistance * 0.95`

Soft:
- `return_n(3) > 0` (momentum turning back up)

### Setup 4 — Oversold Reversal (labeled "higher-risk")

Hard:
- `return_n(30) ∈ [-0.30, -0.10]`
- `return_n(10) < 0` and decelerating: `abs(return_n(10)) < abs(return_n_ending(10, 10))`
- `return_n(5) ∈ [-0.03, 0.03]`
- `return_n(3) > 0`
- `return_n(1) > 0` OR `current_price > today_open`
- `stopped_new_lows(5)`

Soft:
- `current_price > sma20` (reclaiming the 20D)

### Setup 5 — Deep Pullback in Long-Term Uptrend (labeled "Deep Pullback")

Hard:
- `return_n(60) > 0`
- `current_price >= sma200 * 0.95`
- `return_n(30) ∈ [-0.25, -0.10]`
- `return_n(10) < 0`
- `improving(5)`
- `return_n(3) > 0`
- `stopped_new_lows(5)`

## Dedup / episode logic

Unchanged in spirit from today: `alert_state` keyed by `(ticker,
alert_type)`. A setup fires (and sends) only on the transition from "not
in alert" to "all hard conditions true"; it clears silently (no message)
when the hard conditions stop holding, re-arming for the next episode.
Soft/"ideal" conditions are recomputed and included in the message every
time, but never affect dedup state — the same ticker won't get a second
Setup 1 alert until it exits and re-enters the setup.

`alert_type` values: `setup1_uptrend_pullback`, `setup2_momentum_dip`,
`setup3_breakout_retest`, `setup4_oversold_reversal`,
`setup5_deep_pullback`. A ticker can be "in alert" for multiple setups at
once (e.g. Setup 1 and Setup 2 overlap in their 30D-return range) — each
is tracked and messaged independently.

## Message + chart

One Telegram message per newly-triggered (ticker, setup) pair: a photo
(the chart) with a caption containing the ticker, setup name, any
labels (`(ideal signal)`, `(higher-risk)`, or the `Deep Pullback` name
instead of a generic one for Setup 5), and the key numbers that drove
the trigger (30D/10D/5D/3D returns, % below high, MA relationship —
whichever are relevant to that setup).

Chart: `app/charts.py` renders daily closes over the trailing ~126
sessions (~6 months) with SMA20/50/200 overlaid (only the ones with
enough history), current price marked as a point. Same chart shape for
every setup — showing the full MA picture costs little extra code and
is more useful than a setup-specific view.

## Database migration

`alert_state.alert_type` currently has:

```sql
alert_type TEXT NOT NULL CHECK(alert_type IN ('watchlist_drop','holding_gain')),
```

SQLite can't alter a `CHECK` constraint in place. `db.migrate()` will
detect the old constraint (by checking `sqlite_master` for the old
values, or simply by a schema-version marker) and recreate the table
with the 5 new allowed values, **dropping existing rows** — the old
rows' semantics (`watchlist_drop`/`holding_gain`) no longer mean
anything under the new system, so there's nothing worth preserving.
Worst case after migration: each (ticker, setup) starts "not in alert"
and may send one alert the next time it's genuinely in a matching setup,
same as adding a new ticker today.

## Cleanup (dead code removed by this rewrite)

`price_history` table + index, `db.insert_price_snapshot`,
`db.prune_price_history`, `prices.snapshot_active_tickers`,
`prices.get_price_n_hours_ago`, `alerts.compute_trailing_change` are all
removed. Confirmed via grep that nothing outside the old alert path uses
them — `price_history` was explicitly a fallback/observability table for
the old trailing-window design (see its docstring), not used by `/list`
or any other command.

## Config changes

`Settings` drops `watchlist_drop_threshold`, `holding_gain_threshold`,
`trailing_window_hours`. `.env.example` drops the corresponding three
variables. `POLL_INTERVAL_MINUTES` is unchanged (still governs the
current-price/setup-evaluation poll). A new constant (not `.env`),
`DAILY_CACHE_REFRESH_SECONDS = 86400`, governs the daily history
refresh job in `bot.py`.

## New dependency

`matplotlib` added to `requirements.txt`, used only in `app/charts.py`.

## Testing approach

- `tests/test_technicals.py` — helper functions (`return_n`, `improving`,
  `recent_high`, `stopped_new_lows`, `find_breakout_retest`) against
  synthetic close/low price lists, no network.
- `tests/test_setups.py` — each of the 5 setup functions against
  synthetic `TickerMetrics`, covering: all-hard-true triggers, each hard
  condition individually false (does not trigger), each soft condition
  on/off (label changes but trigger state doesn't).
- `tests/test_alerts.py` — rewritten for the new orchestration: stubs
  `setups.check_setup_N` and asserts dedup/episode behavor per
  (ticker, setup), same style as today's tests (stub `send`, no network).
- `tests/test_charts.py` — smoke test that `render_price_chart` returns
  valid, non-empty PNG bytes for a synthetic `TickerMetrics`; no visual
  assertion.
- `tests/test_db.py` — updated for the new `alert_state` schema/migration,
  `test_price_history_insert_and_prune` removed along with the table.

All new tests run offline against synthetic data, consistent with the
project's existing "150 tests, no network needed" approach.
