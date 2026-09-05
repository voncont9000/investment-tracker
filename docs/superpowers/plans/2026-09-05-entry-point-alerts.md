# Entry-Point Alert Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the trailing-window drop/gain alert engine with five daily-technical "entry point" setups (Uptrend Pullback, Momentum + First Dip, Breakout Retest, Oversold Reversal, Deep Pullback), each sent as a chart + message, deduped per (ticker, setup) episode.

**Architecture:** A daily job caches ~2 years of daily OHLC bars per ticker in memory; the existing 15-minute poll job combines that cache with a fresh live price into a `TickerMetrics` snapshot, runs it through five pure setup-check functions, and fires a deduped Telegram photo+caption alert on each new match. Full design rationale: `docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md`.

**Tech Stack:** Python 3.14, `yfinance` (daily bars + live price), `matplotlib` (new dependency, chart rendering), `sqlite3` stdlib (alert-state dedup), `pytest` (all new tests run offline against synthetic data).

## Global Constraints

- Python 3.14, existing project conventions (no ORM, `sqlite3` stdlib, `python-telegram-bot` 22.5 long-polling).
- All new tests run offline — no real network/yfinance calls in the test suite, matching `tests/`'s existing "no network needed" property.
- Setup thresholds (~40 constants) are hardcoded Python constants in `app/setup_thresholds.py`, not `.env` variables (confirmed decision).
- `alert_state`'s `CHECK` constraint is being changed (not purely additive) — this is a deliberate, approved exception documented in the design spec; the migration recreates the table and discards old rows, which is safe because the old alert semantics no longer apply.
- `matplotlib` and `pandas` are new dependencies (`pandas` because tests construct yfinance-shaped DataFrames directly); pin floors consistent with the existing `requirements.txt` style.
- Every task must leave `.venv/bin/python -m pytest tests/ -q` fully green before it's considered done.
- Follow CLAUDE.md's "Always do" rules: explain each change in plain language, run tests before claiming anything works, and update CLAUDE.md itself in the same session (final task here).

---

### Task 1: `TickerMetrics` and core return/high helpers

**Files:**
- Create: `app/technicals.py`
- Test: `tests/test_technicals.py`

**Interfaces:**
- Produces: `TickerMetrics` dataclass (`ticker: str, current_price: float, today_open: float, today_intraday_low: float, daily_closes: list[float], daily_lows: list[float], sma20: float | None, sma50: float | None, sma200: float | None, sma50_5d_ago: float | None, sma200_20d_ago: float | None`); `MIN_HISTORY_SESSIONS = 220`; `build_metrics(ticker: str, daily_closes: list[float], daily_lows: list[float], current_price: float, today_open: float, today_intraday_low: float) -> TickerMetrics | None`; `return_n(metrics, n: int) -> float`; `return_n_ending(metrics, n: int, sessions_back: int) -> float`; `improving(metrics, n: int) -> bool`; `recent_high(metrics, n: int) -> float`; `pct_below_high(metrics, n: int) -> float`; `stopped_new_lows(metrics, n: int = 5) -> bool`; `max_single_day_drop(metrics, n: int = 5) -> float`; `rolling_sma(values: list[float], window: int) -> list[float | None]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_technicals.py`:

```python
import pytest

from app import technicals


def _metrics(closes, lows=None, current=None, today_open=None, today_low=None):
    lows = lows if lows is not None else [c - 1 for c in closes]
    current = current if current is not None else closes[-1]
    today_open = today_open if today_open is not None else current
    today_low = today_low if today_low is not None else current - 1
    return technicals.TickerMetrics(
        ticker="TEST",
        current_price=current,
        today_open=today_open,
        today_intraday_low=today_low,
        daily_closes=closes,
        daily_lows=lows,
        sma20=None,
        sma50=None,
        sma200=None,
        sma50_5d_ago=None,
        sma200_20d_ago=None,
    )


# --- build_metrics ---

def test_build_metrics_returns_none_when_history_too_short():
    closes = [100.0] * 219
    lows = [99.0] * 219
    assert technicals.build_metrics("AAPL", closes, lows, 100.0, 100.0, 99.0) is None


def test_build_metrics_computes_moving_averages_and_slopes():
    closes = [100.0] * 220
    lows = [99.0] * 220
    metrics = technicals.build_metrics(
        "AAPL", closes, lows, current_price=105.0, today_open=104.0, today_intraday_low=103.0
    )
    assert metrics is not None
    assert metrics.sma20 == 100.0
    assert metrics.sma50 == 100.0
    assert metrics.sma200 == 100.0
    assert metrics.sma50_5d_ago == 100.0
    assert metrics.sma200_20d_ago == 100.0
    assert metrics.current_price == 105.0


def test_build_metrics_sma_slopes_reflect_a_rising_series():
    closes = [100.0 + i * 0.5 for i in range(220)]
    lows = [c - 1 for c in closes]
    metrics = technicals.build_metrics(
        "AAPL", closes, lows,
        current_price=closes[-1] + 1, today_open=closes[-1], today_intraday_low=closes[-1] - 1,
    )
    assert metrics.sma50 > metrics.sma50_5d_ago
    assert metrics.sma200 > metrics.sma200_20d_ago


# --- return_n / return_n_ending / improving ---

def test_return_n():
    closes = [90.0, 91.0, 92.0, 93.0, 94.0, 95.0]
    m = _metrics(closes, current=100.0)
    assert technicals.return_n(m, 5) == pytest.approx((100.0 - 91.0) / 91.0)


def test_return_n_ending():
    closes = [90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 96.0, 97.0, 98.0, 99.0]
    m = _metrics(closes)
    assert technicals.return_n_ending(m, 5, 5) == pytest.approx((95.0 - 90.0) / 90.0)


def test_improving_true_when_recent_window_beats_prior_window():
    closes = [90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 96.0, 97.0, 98.0, 99.0]
    m = _metrics(closes, current=105.0)
    assert technicals.improving(m, 5) is True


def test_improving_false_when_recent_window_is_worse():
    closes = [90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 96.0, 97.0, 98.0, 99.0]
    m = _metrics(closes, current=96.0)
    assert technicals.improving(m, 5) is False


# --- recent_high / pct_below_high ---

def test_recent_high_includes_current_price_as_candidate():
    closes = [100.0, 105.0, 102.0]
    m = _metrics(closes, current=110.0)
    assert technicals.recent_high(m, 3) == 110.0


def test_pct_below_high():
    closes = [100.0, 105.0, 102.0]
    m = _metrics(closes, current=99.75)
    assert technicals.pct_below_high(m, 3) == pytest.approx((105.0 - 99.75) / 105.0)


# --- stopped_new_lows ---

def test_stopped_new_lows_true_when_current_at_or_above_recent_min():
    closes = [100.0, 98.0, 96.0, 97.0, 99.0]
    m = _metrics(closes, current=96.5)
    assert technicals.stopped_new_lows(m, n=5) is True


def test_stopped_new_lows_false_when_current_is_a_new_low():
    closes = [100.0, 98.0, 96.0, 97.0, 99.0]
    m = _metrics(closes, current=95.0)
    assert technicals.stopped_new_lows(m, n=5) is False


# --- max_single_day_drop ---

def test_max_single_day_drop():
    closes = [100.0, 98.0, 97.0, 96.5, 96.0]
    m = _metrics(closes, current=90.0)
    drops = [
        (100.0 - 98.0) / 100.0,
        (98.0 - 97.0) / 98.0,
        (97.0 - 96.5) / 97.0,
        (96.5 - 96.0) / 96.5,
        (96.0 - 90.0) / 96.0,
    ]
    assert technicals.max_single_day_drop(m, n=4) == pytest.approx(max(drops))


# --- rolling_sma ---

def test_rolling_sma_fills_none_until_window_reached():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = technicals.rolling_sma(values, window=3)
    assert result == [None, None, 2.0, 3.0, 4.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_technicals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.technicals'`

- [ ] **Step 3: Write the implementation**

Create `app/technicals.py`:

```python
"""Daily price-history caching and technical metric calculations for the
entry-point alert setups (see app/setups.py). Pure computation only —
fetching from yfinance lives in app/prices.py; this module turns fetched
bars into TickerMetrics and answers technical questions about them.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_HISTORY_SESSIONS = 220


@dataclass
class TickerMetrics:
    ticker: str
    current_price: float
    today_open: float
    today_intraday_low: float
    daily_closes: list[float]  # oldest -> newest, completed sessions only
    daily_lows: list[float]    # same alignment as daily_closes
    sma20: float | None
    sma50: float | None
    sma200: float | None
    sma50_5d_ago: float | None
    sma200_20d_ago: float | None


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def build_metrics(
    ticker: str,
    daily_closes: list[float],
    daily_lows: list[float],
    current_price: float,
    today_open: float,
    today_intraday_low: float,
) -> TickerMetrics | None:
    """Build a TickerMetrics from completed-session daily bars plus a live
    price. Returns None if there isn't enough history for the longest
    moving average plus its slope lookback — the ticker is simply skipped
    for this poll rather than evaluated with a partial picture."""
    if len(daily_closes) < MIN_HISTORY_SESSIONS or len(daily_lows) < MIN_HISTORY_SESSIONS:
        return None

    return TickerMetrics(
        ticker=ticker,
        current_price=current_price,
        today_open=today_open,
        today_intraday_low=today_intraday_low,
        daily_closes=daily_closes,
        daily_lows=daily_lows,
        sma20=_sma(daily_closes, 20),
        sma50=_sma(daily_closes, 50),
        sma200=_sma(daily_closes, 200),
        sma50_5d_ago=_sma(daily_closes[:-5], 50),
        sma200_20d_ago=_sma(daily_closes[:-20], 200),
    )


def return_n(metrics: TickerMetrics, n: int) -> float:
    """(current - close n sessions ago) / close n sessions ago."""
    baseline = metrics.daily_closes[-n]
    return (metrics.current_price - baseline) / baseline


def return_n_ending(metrics: TickerMetrics, n: int, sessions_back: int) -> float:
    """The n-day return as of `sessions_back` sessions ago, instead of
    today — used to compare "is this getting better or worse"."""
    closes = metrics.daily_closes
    end_price = closes[-sessions_back]
    start_price = closes[-sessions_back - n]
    return (end_price - start_price) / start_price


def improving(metrics: TickerMetrics, n: int) -> bool:
    return return_n(metrics, n) > return_n_ending(metrics, n, n)


def recent_high(metrics: TickerMetrics, n: int) -> float:
    return max(metrics.daily_closes[-n:] + [metrics.current_price])


def pct_below_high(metrics: TickerMetrics, n: int) -> float:
    high = recent_high(metrics, n)
    return (high - metrics.current_price) / high


def stopped_new_lows(metrics: TickerMetrics, n: int = 5) -> bool:
    return metrics.current_price >= min(metrics.daily_closes[-n:])


def max_single_day_drop(metrics: TickerMetrics, n: int = 5) -> float:
    """Largest one-day percentage decline (as a positive fraction) among
    the trailing n sessions, including today's live price vs yesterday's
    close as the most recent "day". Used to tell an orderly pullback from
    a sudden breakdown."""
    closes = metrics.daily_closes[-(n + 1):] + [metrics.current_price]
    drops = [
        max(0.0, (prev - curr) / prev)
        for prev, curr in zip(closes, closes[1:])
        if prev > 0
    ]
    return max(drops) if drops else 0.0


def rolling_sma(values: list[float], window: int) -> list[float | None]:
    """SMA at each index of `values`; None where there isn't enough
    history yet. Used by app/charts.py to draw MA lines."""
    result: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            result.append(None)
        else:
            result.append(sum(values[i + 1 - window : i + 1]) / window)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_technicals.py -v`
Expected: PASS (all tests green)

- [ ] **Step 5: Commit**

```bash
git add app/technicals.py tests/test_technicals.py
git commit -m "$(cat <<'EOF'
Add TickerMetrics and core technical helper functions

First piece of the entry-point alert redesign: pure computation over
daily price bars (returns, moving averages, recent highs) with no
network or DB dependency, so every setup check can be unit tested
against synthetic data.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Swing-low retracement helper and breakout/retest detection

**Files:**
- Modify: `app/technicals.py`
- Test: `tests/test_technicals.py`

**Interfaces:**
- Consumes: `TickerMetrics` (Task 1)
- Produces: `swing_low_before_high(metrics, high_window: int = 30, lookback: int = 90) -> float`; `BreakoutInfo` dataclass (`resistance: float, breakout_confirmed: bool, support_holding: bool`); `find_breakout_retest(metrics: TickerMetrics) -> BreakoutInfo | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_technicals.py`:

```python
# --- swing_low_before_high ---

def test_swing_low_before_high_finds_the_low_before_the_peak():
    closes = [80.0, 85.0, 90.0, 95.0, 100.0, 120.0, 115.0, 110.0, 108.0, 106.0]
    m = _metrics(closes, current=106.0)
    assert technicals.swing_low_before_high(m, high_window=10, lookback=10) == 80.0


# --- find_breakout_retest ---

def test_find_breakout_retest_returns_none_when_not_enough_history():
    closes = [100.0] * 59
    m = _metrics(closes, current=100.0)
    assert technicals.find_breakout_retest(m) is None


def test_find_breakout_retest_detects_a_confirmed_breakout():
    closes = [100.0] * 55 + [101.0, 103.0, 105.0, 104.0, 103.0]
    m = _metrics(closes, current=102.0)
    result = technicals.find_breakout_retest(m)
    assert result is not None
    assert result.resistance == 100.0
    assert result.breakout_confirmed is True
    assert result.support_holding is True


def test_find_breakout_retest_flags_broken_support():
    closes = [100.0] * 55 + [101.0, 103.0, 105.0, 104.0, 90.0]
    m = _metrics(closes, current=90.0)
    result = technicals.find_breakout_retest(m)
    assert result.support_holding is False


def test_find_breakout_retest_no_breakout_when_resistance_not_cleared():
    closes = [100.0] * 55 + [100.5, 101.0, 100.8, 101.2, 101.0]
    m = _metrics(closes, current=101.0)
    result = technicals.find_breakout_retest(m)
    assert result.breakout_confirmed is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_technicals.py -v -k "swing_low or breakout"`
Expected: FAIL with `AttributeError: module 'app.technicals' has no attribute 'swing_low_before_high'`

- [ ] **Step 3: Write the implementation**

Append to `app/technicals.py`:

```python
def swing_low_before_high(metrics: TickerMetrics, high_window: int = 30, lookback: int = 90) -> float:
    """Find the completed session (within the trailing `high_window`)
    where the recent high was set, then return the lowest close in the
    `lookback` sessions strictly before it — the "swing low" the
    preceding rally started from, used for Setup 1's retracement check."""
    closes = metrics.daily_closes
    window = closes[-high_window:]
    high_value = max(window)
    window_start_idx = len(closes) - high_window
    high_idx = window_start_idx + window.index(high_value)
    lookback_start = max(0, high_idx - lookback)
    before = closes[lookback_start:high_idx]
    if not before:
        return closes[0]
    return min(before)


@dataclass
class BreakoutInfo:
    resistance: float
    breakout_confirmed: bool
    support_holding: bool


def find_breakout_retest(metrics: TickerMetrics) -> BreakoutInfo | None:
    """Look for a resistance level (the highest close from 60 down to 5
    sessions back), a breakout at least 3% above it within the most
    recent 5 sessions, and whether price has held above it since (support
    not decisively broken). Returns None if there isn't 60 sessions of
    history to look back over."""
    closes = metrics.daily_closes
    if len(closes) < 60:
        return None

    resistance_window = closes[-60:-5]
    if not resistance_window:
        return None
    resistance = max(resistance_window)

    breakout_window = closes[-5:]
    breakout_confirmed = max(breakout_window) >= resistance * 1.03

    since_breakout = closes[-5:] + [metrics.current_price]
    support_holding = all(price >= resistance * 0.95 for price in since_breakout)

    return BreakoutInfo(
        resistance=resistance,
        breakout_confirmed=breakout_confirmed,
        support_holding=support_holding,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_technicals.py -v`
Expected: PASS (all tests green, including Task 1's)

- [ ] **Step 5: Commit**

```bash
git add app/technicals.py tests/test_technicals.py
git commit -m "$(cat <<'EOF'
Add breakout/retest detection and retracement helper

Completes the technicals helper set: swing_low_before_high (for Setup
1's retracement check) and find_breakout_retest (Setup 3's resistance/
breakout/support-holding detection).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: yfinance daily-bar and live-price fetch functions

**Files:**
- Modify: `app/prices.py`
- Modify: `requirements.txt`
- Test: `tests/test_prices.py` (new)

**Interfaces:**
- Produces: `fetch_daily_bars(ticker: str) -> tuple[list[float], list[float]] | None` (closes, lows — completed sessions only, oldest to newest); `fetch_live_price(ticker: str) -> tuple[float, float, float] | None` (current_price, today_open, today_intraday_low)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prices.py`:

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from app import prices


def test_fetch_daily_bars_excludes_todays_partial_session():
    now = datetime.now(timezone.utc)
    two_days_ago = now - timedelta(days=2)
    yesterday = now - timedelta(days=1)
    df = pd.DataFrame(
        {"Close": [100.0, 101.0, 102.0], "Low": [99.0, 100.0, 101.0]},
        index=pd.DatetimeIndex([two_days_ago, yesterday, now], tz="UTC"),
    )
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = df

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        result = prices.fetch_daily_bars("AAPL")

    assert result == ([100.0, 101.0], [99.0, 100.0])


def test_fetch_daily_bars_returns_none_when_history_is_empty():
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame()

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_daily_bars("AAPL") is None


def test_fetch_daily_bars_returns_none_on_exception():
    fake_ticker = MagicMock()
    fake_ticker.history.side_effect = RuntimeError("network error")

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_daily_bars("AAPL") is None


def test_fetch_live_price_returns_current_open_and_day_low():
    fake_ticker = MagicMock()
    fake_ticker.fast_info = {"lastPrice": 150.0, "open": 148.0, "dayLow": 147.5}

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        result = prices.fetch_live_price("AAPL")

    assert result == (150.0, 148.0, 147.5)


def test_fetch_live_price_returns_none_when_a_field_is_missing():
    fake_ticker = MagicMock()
    fake_ticker.fast_info = {"lastPrice": 150.0}

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_live_price("AAPL") is None


def test_fetch_live_price_returns_none_on_exception():
    # A plain class (not MagicMock) so the raising property lives only on
    # this one object, instead of leaking onto MagicMock's shared class.
    class ExplodingTicker:
        @property
        def fast_info(self):
            raise RuntimeError("boom")

    with patch("app.prices.yf.Ticker", return_value=ExplodingTicker()):
        assert prices.fetch_live_price("AAPL") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prices.py -v`
Expected: FAIL with `AttributeError: module 'app.prices' has no attribute 'fetch_daily_bars'`

- [ ] **Step 3: Write the implementation**

Add to the top of `requirements.txt`'s dependency list (after `yfinance>=0.2.54`):

```
pandas>=2.0
```

Modify `app/prices.py` — add after the existing `get_price_n_hours_ago` function (keep all existing functions as-is):

```python
def fetch_daily_bars(ticker: str) -> tuple[list[float], list[float]] | None:
    """Fetch ~2 years of daily close/low history for completed sessions
    only — an in-progress "today" bar (if yfinance includes one) is
    dropped so moving averages and returns are never skewed by a partial
    day. ~2 years comfortably covers the 200-session SMA, the 60-session
    breakout lookback, and the ~126-session chart window with room to
    spare. Oldest to newest."""
    try:
        history = yf.Ticker(ticker).history(period="2y", interval="1d")
        if history.empty:
            return None
        today = datetime.now(timezone.utc).date()
        index_dates = history.index.tz_convert("UTC").date
        history = history[index_dates < today]
        if history.empty:
            return None
        closes = [float(c) for c in history["Close"].tolist()]
        lows = [float(l) for l in history["Low"].tolist()]
        return closes, lows
    except Exception:
        return None


def fetch_live_price(ticker: str) -> tuple[float, float, float] | None:
    """Return (current_price, today_open, today_intraday_low), or None
    if the live quote or any of those three fields is unavailable."""
    try:
        info = yf.Ticker(ticker).fast_info
        current = info.get("lastPrice")
        today_open = info.get("open")
        today_low = info.get("dayLow")
        if current is None or today_open is None or today_low is None:
            return None
        return float(current), float(today_open), float(today_low)
    except Exception:
        return None
```

- [ ] **Step 4: Install the new dependency and run tests**

Run: `.venv/bin/pip install -r requirements.txt`
Run: `.venv/bin/python -m pytest tests/test_prices.py -v`
Expected: PASS (all tests green)

- [ ] **Step 5: Commit**

```bash
git add app/prices.py requirements.txt tests/test_prices.py
git commit -m "$(cat <<'EOF'
Add daily-bar and live-price fetch functions

New yfinance wrappers for the entry-point alert redesign: fetch_daily_bars
(completed-session-only daily OHLC history) and fetch_live_price (current
price + today's open/intraday low). Adds pandas as an explicit dependency
since tests construct yfinance-shaped DataFrames directly.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: In-memory daily-bar cache

**Files:**
- Modify: `app/technicals.py`
- Test: `tests/test_technicals.py`

**Interfaces:**
- Consumes: `app.prices.fetch_daily_bars` (Task 3)
- Produces: `refresh_daily_cache(tickers: list[str]) -> None`; `get_cached_daily_bars(ticker: str) -> tuple[list[float], list[float]] | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_technicals.py`:

```python
from unittest.mock import patch


# --- daily cache ---

def test_get_cached_daily_bars_returns_none_before_any_refresh():
    assert technicals.get_cached_daily_bars("NEVER_CACHED_XYZ") is None


def test_refresh_daily_cache_populates_and_get_cached_daily_bars_reads_it():
    with patch("app.technicals.prices.fetch_daily_bars", return_value=([100.0, 101.0], [99.0, 100.0])):
        technicals.refresh_daily_cache(["AAPL"])
    assert technicals.get_cached_daily_bars("AAPL") == ([100.0, 101.0], [99.0, 100.0])


def test_refresh_daily_cache_skips_tickers_that_fail_to_fetch():
    def fake_fetch(ticker):
        return None if ticker == "BADTICKER" else ([100.0], [99.0])

    with patch("app.technicals.prices.fetch_daily_bars", side_effect=fake_fetch):
        technicals.refresh_daily_cache(["BADTICKER", "GOODTICKER"])

    assert technicals.get_cached_daily_bars("BADTICKER") is None
    assert technicals.get_cached_daily_bars("GOODTICKER") == ([100.0], [99.0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_technicals.py -v -k cache`
Expected: FAIL with `AttributeError: module 'app.technicals' has no attribute 'get_cached_daily_bars'`

- [ ] **Step 3: Write the implementation**

Modify `app/technicals.py` — add the import at the top and the cache at the bottom:

```python
from app import prices
```

(Add this import line right after `from dataclasses import dataclass`.)

Append to the end of `app/technicals.py`:

```python
_daily_cache: dict[str, tuple[list[float], list[float]]] = {}


def refresh_daily_cache(tickers: list[str]) -> None:
    """Fetch and cache daily (closes, lows) bars for each ticker.
    Failures for individual tickers are swallowed (same pattern as the
    old snapshot_active_tickers) so one bad ticker doesn't block the
    rest."""
    for ticker in tickers:
        bars = prices.fetch_daily_bars(ticker)
        if bars is not None:
            _daily_cache[ticker] = bars


def get_cached_daily_bars(ticker: str) -> tuple[list[float], list[float]] | None:
    return _daily_cache.get(ticker)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_technicals.py -v`
Expected: PASS (all tests green)

- [ ] **Step 5: Commit**

```bash
git add app/technicals.py tests/test_technicals.py
git commit -m "$(cat <<'EOF'
Add in-memory daily-bar cache

Lets the 15-minute poll job reuse a day's worth of fetched history
instead of re-pulling ~2 years of daily bars from yfinance on every
poll; a separate daily job (wired up in a later task) calls
refresh_daily_cache.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Setup threshold constants

**Files:**
- Create: `app/setup_thresholds.py`
- Test: `tests/test_setup_thresholds.py`

**Interfaces:**
- Produces: all constants listed below, importable as `from app import setup_thresholds as t`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_setup_thresholds.py`:

```python
from app import setup_thresholds as t


def test_setup1_return_band_matches_spec():
    assert t.SETUP1_RETURN_30D_MIN == 0.07
    assert t.SETUP1_RETURN_30D_MAX == 0.30
    assert t.SETUP1_PULLBACK_MIN == 0.02
    assert t.SETUP1_PULLBACK_MAX == 0.10


def test_setup2_return_band_matches_spec():
    assert t.SETUP2_RETURN_30D_MIN == 0.15
    assert t.SETUP2_RETURN_10D_MIN == 0.07


def test_setup3_breakout_band_matches_spec():
    assert t.SETUP3_BREAKOUT_MIN_PCT == 0.03
    assert t.SETUP3_RETEST_OVERSHOOT == 0.05


def test_setup4_is_labeled_higher_risk_via_its_threshold_module_docstring():
    # Sanity check that the module actually defines all 5 setups' constants.
    assert t.SETUP4_RETURN_30D_MIN == -0.30
    assert t.SETUP5_RETURN_30D_MIN == -0.25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_setup_thresholds.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.setup_thresholds'`

- [ ] **Step 3: Write the implementation**

Create `app/setup_thresholds.py`:

```python
"""Numeric thresholds for the 5 entry-point alert setups (app/setups.py).

Hardcoded rather than .env-configurable — there are ~40 of them, too many
to expose sanely as environment variables for a personal bot. Tune by
editing this file and restarting the bot.
"""

# --- Setup 1: Uptrend + Pullback ---
SETUP1_RETURN_30D_MIN = 0.07
SETUP1_RETURN_30D_MAX = 0.30
SETUP1_PULLBACK_MIN = 0.02
SETUP1_PULLBACK_MAX = 0.10
SETUP1_RETURN_5D_MIN = -0.08
SETUP1_RETURN_5D_MAX = -0.01
SETUP1_MAX_RETRACEMENT = 0.50
SETUP1_HIGH_WINDOW_A = 20
SETUP1_HIGH_WINDOW_B = 30
SETUP1_SWING_LOOKBACK = 90
SETUP1_STABILIZATION_WINDOW = 3

# --- Setup 2: Momentum + First Meaningful Dip ---
SETUP2_RETURN_30D_MIN = 0.15
SETUP2_RETURN_10D_MIN = 0.07
SETUP2_RETURN_5D_MAX = -0.02
SETUP2_PCT_BELOW_HIGH_MAX = 0.10
SETUP2_MAX_SINGLE_DAY_DROP = 0.07
SETUP2_IDEAL_RETURN_5D_MIN = -0.07
SETUP2_IDEAL_RETURN_5D_MAX = -0.02
SETUP2_HIGH_WINDOW = 30

# --- Setup 3: Breakout Retest ---
SETUP3_BREAKOUT_MIN_PCT = 0.03
SETUP3_RETEST_UNDERSHOOT = 0.02
SETUP3_RETEST_OVERSHOOT = 0.05

# --- Setup 4: Oversold Reversal (higher-risk) ---
SETUP4_RETURN_30D_MIN = -0.30
SETUP4_RETURN_30D_MAX = -0.10
SETUP4_RETURN_5D_MIN = -0.03
SETUP4_RETURN_5D_MAX = 0.03
SETUP4_STOPPED_LOWS_WINDOW = 5
SETUP4_DECEL_WINDOW = 10

# --- Setup 5: Deep Pullback in Long-Term Uptrend ---
SETUP5_RETURN_30D_MIN = -0.25
SETUP5_RETURN_30D_MAX = -0.10
SETUP5_SMA200_PROXIMITY = 0.05
SETUP5_STOPPED_LOWS_WINDOW = 5
SETUP5_IMPROVING_WINDOW = 5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_setup_thresholds.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/setup_thresholds.py tests/test_setup_thresholds.py
git commit -m "$(cat <<'EOF'
Add setup threshold constants

All ~40 numeric parameters across the 5 entry-point setups, hardcoded
per the design decision to avoid a huge .env surface for a personal bot.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `SetupMatch` and Setup 1 (Uptrend + Pullback)

**Files:**
- Create: `app/setups.py`
- Test: `tests/test_setups.py`

**Interfaces:**
- Consumes: `app.technicals.{TickerMetrics, build_metrics, return_n, recent_high, pct_below_high, improving, swing_low_before_high}` (Tasks 1-2), `app.setup_thresholds` (Task 5)
- Produces: `SetupMatch` dataclass (`setup_id: str, label: str, is_ideal: bool, ideal_reasons: list[str], numbers: dict[str, float], risk_label: str | None = None`); `check_setup_1(metrics: TickerMetrics) -> SetupMatch | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_setups.py`:

```python
from app import setups, technicals


def _lerp_path(control_points, length):
    """control_points: [(day_index, price), ...], day_index 0..length-1,
    sorted, first index 0, last index length-1. Straight-line
    interpolation between consecutive points."""
    out = [None] * length
    for (i0, p0), (i1, p1) in zip(control_points, control_points[1:]):
        span = i1 - i0
        for d in range(i0, i1 + 1):
            frac = (d - i0) / span if span else 0
            out[d] = p0 + (p1 - p0) * frac
    return out


def _series(tail, flat_days=200, flat_price=100.0):
    return [flat_price] * flat_days + tail


def _build(closes, current, today_open, today_low, lows=None):
    lows = lows if lows is not None else [c * 0.99 for c in closes]
    metrics = technicals.build_metrics("TEST", closes, lows, current, today_open, today_low)
    assert metrics is not None, "test fixture must produce at least MIN_HISTORY_SESSIONS closes"
    return metrics


# ---------- Setup 1 ----------

_SETUP1_TAIL = _lerp_path([(0, 100), (30, 110), (45, 135), (55, 130), (59, 125)], 60)
_SETUP1_CLOSES = _series(_SETUP1_TAIL)


def test_setup1_triggers_on_uptrend_pullback():
    metrics = _build(_SETUP1_CLOSES, current=123.5, today_open=123.6, today_low=123.0)
    match = setups.check_setup_1(metrics)
    assert match is not None
    assert match.setup_id == "setup1_uptrend_pullback"
    assert match.is_ideal is False
    assert match.ideal_reasons == []


def test_setup1_is_ideal_when_stabilization_signals_hold():
    metrics = _build(_SETUP1_CLOSES, current=126.0, today_open=124.5, today_low=124.8)
    match = setups.check_setup_1(metrics)
    assert match is not None
    assert match.is_ideal is True
    assert "3-day return improving" in match.ideal_reasons
    assert "turning positive after a decline" in match.ideal_reasons


def test_setup1_does_not_trigger_when_pullback_is_too_shallow():
    # Price only just off its high — not a real pullback.
    metrics = _build(_SETUP1_CLOSES, current=133.0, today_open=133.5, today_low=132.5)
    assert setups.check_setup_1(metrics) is None


def test_setup1_does_not_trigger_when_trend_is_broken():
    # Price has crashed well below its moving averages, not pulled back.
    metrics = _build(_SETUP1_CLOSES, current=90.0, today_open=95.0, today_low=88.0)
    assert setups.check_setup_1(metrics) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.setups'`

- [ ] **Step 3: Write the implementation**

Create `app/setups.py`:

```python
"""The 5 entry-point setups. Each check_setup_N is a pure function:
TickerMetrics in, a SetupMatch (or None) out. No I/O, no DB — fully
testable against synthetic metrics.
"""

from __future__ import annotations

from dataclasses import dataclass

from app import setup_thresholds as t
from app.technicals import (
    TickerMetrics,
    improving,
    pct_below_high,
    recent_high,
    return_n,
    swing_low_before_high,
)


@dataclass
class SetupMatch:
    setup_id: str
    label: str
    is_ideal: bool
    ideal_reasons: list[str]
    numbers: dict[str, float]
    risk_label: str | None = None


def check_setup_1(metrics: TickerMetrics) -> SetupMatch | None:
    if None in (metrics.sma50, metrics.sma200, metrics.sma50_5d_ago, metrics.sma200_20d_ago):
        return None

    return_30d = return_n(metrics, 30)
    trend_ok = (
        metrics.current_price > metrics.sma50
        and (metrics.sma50 > metrics.sma200 or metrics.sma200_20d_ago < metrics.sma200)
        and metrics.sma50 > metrics.sma50_5d_ago
        and t.SETUP1_RETURN_30D_MIN <= return_30d <= t.SETUP1_RETURN_30D_MAX
    )
    if not trend_ok:
        return None

    pullback_a = pct_below_high(metrics, t.SETUP1_HIGH_WINDOW_A)
    pullback_b = pct_below_high(metrics, t.SETUP1_HIGH_WINDOW_B)
    within_band = (t.SETUP1_PULLBACK_MIN <= pullback_a <= t.SETUP1_PULLBACK_MAX) or (
        t.SETUP1_PULLBACK_MIN <= pullback_b <= t.SETUP1_PULLBACK_MAX
    )
    return_5d = return_n(metrics, 5)

    high = recent_high(metrics, t.SETUP1_HIGH_WINDOW_B)
    swing_low = swing_low_before_high(metrics, t.SETUP1_HIGH_WINDOW_B, t.SETUP1_SWING_LOOKBACK)
    rally = high - swing_low
    retracement_ok = True
    if rally > 0:
        retracement = (high - metrics.current_price) / rally
        retracement_ok = retracement <= t.SETUP1_MAX_RETRACEMENT

    pullback_ok = (
        within_band
        and (t.SETUP1_RETURN_5D_MIN <= return_5d <= t.SETUP1_RETURN_5D_MAX)
        and retracement_ok
    )
    if not pullback_ok:
        return None

    ideal_reasons = []
    if improving(metrics, t.SETUP1_STABILIZATION_WINDOW):
        ideal_reasons.append("3-day return improving")
    if metrics.current_price > metrics.daily_lows[-1]:
        ideal_reasons.append("above yesterday's low")
    if metrics.today_intraday_low > metrics.daily_lows[-1]:
        ideal_reasons.append("higher day low than yesterday")
    if (
        metrics.current_price > metrics.daily_closes[-1]
        and metrics.daily_closes[-1] < metrics.daily_closes[-2] < metrics.daily_closes[-3]
    ):
        ideal_reasons.append("turning positive after a decline")

    return SetupMatch(
        setup_id="setup1_uptrend_pullback",
        label="Uptrend Pullback",
        is_ideal=bool(ideal_reasons),
        ideal_reasons=ideal_reasons,
        numbers={
            "30D return": return_30d,
            "5D return": return_5d,
            "% below high": min(pullback_a, pullback_b),
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v`
Expected: PASS (all 4 tests green)

- [ ] **Step 5: Commit**

```bash
git add app/setups.py tests/test_setups.py
git commit -m "$(cat <<'EOF'
Add SetupMatch and Setup 1 (Uptrend Pullback)

First of the 5 entry-point setups: strong uptrend + shallow pullback,
the highest-priority signal per the design spec. Soft "stabilization"
conditions upgrade the alert's label without gating the trigger.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Setup 2 (Momentum + First Meaningful Dip)

**Files:**
- Modify: `app/setups.py`
- Test: `tests/test_setups.py`

**Interfaces:**
- Consumes: `app.technicals.max_single_day_drop` (Task 1), same imports as Task 6
- Produces: `check_setup_2(metrics: TickerMetrics) -> SetupMatch | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setups.py`:

```python
# ---------- Setup 2 ----------

_SETUP2_TAIL = _lerp_path([(0, 100), (30, 105), (50, 115), (55, 131), (57, 133), (59, 127)], 60)
_SETUP2_CLOSES = _series(_SETUP2_TAIL)


def test_setup2_triggers_on_momentum_and_first_dip():
    metrics = _build(_SETUP2_CLOSES, current=125.0, today_open=126.0, today_low=124.0)
    match = setups.check_setup_2(metrics)
    assert match is not None
    assert match.setup_id == "setup2_momentum_dip"
    assert match.is_ideal is True  # 5D return falls in the -2% to -7% ideal band


def test_setup2_does_not_trigger_on_a_tiny_dip():
    # Barely off the peak — not a genuine pullback.
    metrics = _build(_SETUP2_CLOSES, current=132.5, today_open=133.0, today_low=132.0)
    assert setups.check_setup_2(metrics) is None


def test_setup2_does_not_trigger_without_enough_prior_momentum():
    # Flat history, no 30D/10D momentum at all.
    flat_closes = _series([100.0] * 60)
    metrics = _build(flat_closes, current=100.0, today_open=100.0, today_low=99.5)
    assert setups.check_setup_2(metrics) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v -k setup2`
Expected: FAIL with `AttributeError: module 'app.setups' has no attribute 'check_setup_2'`

- [ ] **Step 3: Write the implementation**

Modify `app/setups.py` — update the import block at the top to add `max_single_day_drop`:

```python
from app.technicals import (
    TickerMetrics,
    improving,
    max_single_day_drop,
    pct_below_high,
    recent_high,
    return_n,
    swing_low_before_high,
)
```

Append to `app/setups.py`:

```python
def check_setup_2(metrics: TickerMetrics) -> SetupMatch | None:
    if metrics.sma20 is None or metrics.sma50 is None:
        return None

    return_30d = return_n(metrics, 30)
    return_10d = return_n(metrics, 10)
    return_5d = return_n(metrics, 5)

    hard_ok = (
        return_30d > t.SETUP2_RETURN_30D_MIN
        and return_10d > t.SETUP2_RETURN_10D_MIN
        and return_5d <= t.SETUP2_RETURN_5D_MAX
        and metrics.current_price > metrics.sma20
        and metrics.current_price > metrics.sma50
        and pct_below_high(metrics, t.SETUP2_HIGH_WINDOW) <= t.SETUP2_PCT_BELOW_HIGH_MAX
        and max_single_day_drop(metrics, 5) <= t.SETUP2_MAX_SINGLE_DAY_DROP
    )
    if not hard_ok:
        return None

    is_ideal = t.SETUP2_IDEAL_RETURN_5D_MIN <= return_5d <= t.SETUP2_IDEAL_RETURN_5D_MAX
    return SetupMatch(
        setup_id="setup2_momentum_dip",
        label="Momentum + First Dip",
        is_ideal=is_ideal,
        ideal_reasons=["5-day pullback in the ideal -2% to -7% band"] if is_ideal else [],
        numbers={"30D return": return_30d, "10D return": return_10d, "5D return": return_5d},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v`
Expected: PASS (all tests green, including Task 6's)

- [ ] **Step 5: Commit**

```bash
git add app/setups.py tests/test_setups.py
git commit -m "$(cat <<'EOF'
Add Setup 2 (Momentum + First Meaningful Dip)

Catches a strongly-trending stock's first real pullback, distinguished
from noise by a minimum 5-day decline plus a no-single-day-crash
"orderly decline" guard.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Setup 3 (Breakout Retest)

**Files:**
- Modify: `app/setups.py`
- Test: `tests/test_setups.py`

**Interfaces:**
- Consumes: `app.technicals.find_breakout_retest` (Task 2)
- Produces: `check_setup_3(metrics: TickerMetrics) -> SetupMatch | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setups.py`:

```python
# ---------- Setup 3 ----------

_SETUP3_TAIL = _lerp_path([(0, 100), (40, 118), (54, 118), (56, 124), (58, 120), (59, 119)], 60)
_SETUP3_CLOSES = _series(_SETUP3_TAIL)


def test_setup3_triggers_on_breakout_retest():
    metrics = _build(_SETUP3_CLOSES, current=119.5, today_open=119.0, today_low=118.5)
    match = setups.check_setup_3(metrics)
    assert match is not None
    assert match.setup_id == "setup3_breakout_retest"
    assert match.numbers["resistance"] == 118.0


def test_setup3_does_not_trigger_without_a_breakout():
    # Flat the whole way — never actually broke out.
    flat_closes = _series([100.0] * 60)
    metrics = _build(flat_closes, current=100.0, today_open=100.0, today_low=99.5)
    assert setups.check_setup_3(metrics) is None


def test_setup3_does_not_trigger_when_price_has_run_too_far_past_the_retest():
    # Broke out and kept running, well past the 0-5% retest band.
    metrics = _build(_SETUP3_CLOSES, current=140.0, today_open=138.0, today_low=137.0)
    assert setups.check_setup_3(metrics) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v -k setup3`
Expected: FAIL with `AttributeError: module 'app.setups' has no attribute 'check_setup_3'`

- [ ] **Step 3: Write the implementation**

Modify `app/setups.py` — update the import block to add `find_breakout_retest`:

```python
from app.technicals import (
    TickerMetrics,
    find_breakout_retest,
    improving,
    max_single_day_drop,
    pct_below_high,
    recent_high,
    return_n,
    swing_low_before_high,
)
```

Append to `app/setups.py`:

```python
def check_setup_3(metrics: TickerMetrics) -> SetupMatch | None:
    breakout = find_breakout_retest(metrics)
    if breakout is None or not breakout.breakout_confirmed or not breakout.support_holding:
        return None

    retest_low = breakout.resistance * (1 - t.SETUP3_RETEST_UNDERSHOOT)
    retest_high = breakout.resistance * (1 + t.SETUP3_RETEST_OVERSHOOT)
    if not (retest_low <= metrics.current_price <= retest_high):
        return None

    ideal_reasons = []
    if return_n(metrics, 3) > 0:
        ideal_reasons.append("3-day momentum turning up")

    return SetupMatch(
        setup_id="setup3_breakout_retest",
        label="Breakout Retest",
        is_ideal=bool(ideal_reasons),
        ideal_reasons=ideal_reasons,
        numbers={"resistance": breakout.resistance, "current price": metrics.current_price},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v`
Expected: PASS (all tests green)

- [ ] **Step 5: Commit**

```bash
git add app/setups.py tests/test_setups.py
git commit -m "$(cat <<'EOF'
Add Setup 3 (Breakout Retest)

Detects a resistance level broken by 3%+ and now being retested from
above — preferred over chasing the initial breakout per the design spec.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Setup 4 (Oversold Reversal)

**Files:**
- Modify: `app/setups.py`
- Test: `tests/test_setups.py`

**Interfaces:**
- Consumes: `app.technicals.{return_n_ending, stopped_new_lows}`
- Produces: `check_setup_4(metrics: TickerMetrics) -> SetupMatch | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setups.py`:

```python
# ---------- Setup 4 ----------

_SETUP4_TAIL = _lerp_path([(0, 100), (30, 90), (50, 73.0), (55, 72.8), (57, 72.4), (59, 72.5)], 60)
_SETUP4_CLOSES = _series(_SETUP4_TAIL)


def test_setup4_triggers_on_oversold_reversal():
    metrics = _build(_SETUP4_CLOSES, current=72.6, today_open=72.5, today_low=72.0)
    match = setups.check_setup_4(metrics)
    assert match is not None
    assert match.setup_id == "setup4_oversold_reversal"
    assert match.risk_label == "higher-risk"


def test_setup4_does_not_trigger_without_a_reversal():
    # Still falling hard, no sign of stabilization.
    metrics = _build(_SETUP4_CLOSES, current=65.0, today_open=68.0, today_low=64.0)
    assert setups.check_setup_4(metrics) is None


def test_setup4_does_not_trigger_on_a_mild_dip():
    # Nowhere near the -10% to -30% 30-day range this setup requires.
    flat_closes = _series([100.0] * 60)
    metrics = _build(flat_closes, current=99.0, today_open=99.5, today_low=98.5)
    assert setups.check_setup_4(metrics) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v -k setup4`
Expected: FAIL with `AttributeError: module 'app.setups' has no attribute 'check_setup_4'`

- [ ] **Step 3: Write the implementation**

Modify `app/setups.py` — update the import block to add `return_n_ending` and `stopped_new_lows`:

```python
from app.technicals import (
    TickerMetrics,
    find_breakout_retest,
    improving,
    max_single_day_drop,
    pct_below_high,
    recent_high,
    return_n,
    return_n_ending,
    stopped_new_lows,
    swing_low_before_high,
)
```

Append to `app/setups.py`:

```python
def check_setup_4(metrics: TickerMetrics) -> SetupMatch | None:
    return_10d = return_n(metrics, 10)
    return_10d_prior = return_n_ending(metrics, t.SETUP4_DECEL_WINDOW, t.SETUP4_DECEL_WINDOW)

    hard_ok = (
        t.SETUP4_RETURN_30D_MIN <= return_n(metrics, 30) <= t.SETUP4_RETURN_30D_MAX
        and return_10d < 0
        and abs(return_10d) < abs(return_10d_prior)
        and t.SETUP4_RETURN_5D_MIN <= return_n(metrics, 5) <= t.SETUP4_RETURN_5D_MAX
        and return_n(metrics, 3) > 0
        and (return_n(metrics, 1) > 0 or metrics.current_price > metrics.today_open)
        and stopped_new_lows(metrics, t.SETUP4_STOPPED_LOWS_WINDOW)
    )
    if not hard_ok:
        return None

    ideal_reasons = []
    if metrics.sma20 is not None and metrics.current_price > metrics.sma20:
        ideal_reasons.append("reclaiming the 20-day moving average")

    return SetupMatch(
        setup_id="setup4_oversold_reversal",
        label="Oversold Reversal",
        is_ideal=bool(ideal_reasons),
        ideal_reasons=ideal_reasons,
        numbers={
            "30D return": return_n(metrics, 30),
            "10D return": return_10d,
            "5D return": return_n(metrics, 5),
        },
        risk_label="higher-risk",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v`
Expected: PASS (all tests green)

- [ ] **Step 5: Commit**

```bash
git add app/setups.py tests/test_setups.py
git commit -m "$(cat <<'EOF'
Add Setup 4 (Oversold Reversal)

Higher-risk setup: a sharp 10-30% decline showing genuine signs of
short-term reversal (decelerating decline, positive 1D/3D, no new lows),
labeled "higher-risk" in the alert per the design spec.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Setup 5 (Deep Pullback) and `ALL_SETUPS`

**Files:**
- Modify: `app/setups.py`
- Test: `tests/test_setups.py`

**Interfaces:**
- Produces: `check_setup_5(metrics: TickerMetrics) -> SetupMatch | None`; `ALL_SETUPS: list[tuple[str, Callable[[TickerMetrics], SetupMatch | None]]]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setups.py`:

```python
# ---------- Setup 5 ----------

_SETUP5_TAIL = _lerp_path([(0, 85), (30, 110), (50, 91), (55, 88), (57, 87.5), (59, 88)], 60)
_SETUP5_CLOSES = _series(_SETUP5_TAIL, flat_price=90.0)


def test_setup5_triggers_on_deep_pullback():
    metrics = _build(_SETUP5_CLOSES, current=89.0, today_open=88.2, today_low=87.8)
    match = setups.check_setup_5(metrics)
    assert match is not None
    assert match.setup_id == "setup5_deep_pullback"
    assert match.label == "Deep Pullback"


def test_setup5_does_not_trigger_without_a_prior_uptrend():
    # No positive 60-day return backing up the "long-term uptrend" premise.
    flat_closes = _series([100.0] * 60, flat_price=100.0)
    metrics = _build(flat_closes, current=80.0, today_open=81.0, today_low=79.0)
    assert setups.check_setup_5(metrics) is None


def test_setup5_does_not_trigger_when_still_making_new_lows():
    metrics = _build(_SETUP5_CLOSES, current=80.0, today_open=82.0, today_low=79.0)
    assert setups.check_setup_5(metrics) is None


# ---------- ALL_SETUPS ----------

def test_all_setups_lists_all_five_in_priority_order():
    ids = [setup_id for setup_id, _ in setups.ALL_SETUPS]
    assert ids == [
        "setup1_uptrend_pullback",
        "setup2_momentum_dip",
        "setup3_breakout_retest",
        "setup4_oversold_reversal",
        "setup5_deep_pullback",
    ]


def test_all_setups_functions_match_their_ids():
    metrics = _build(_SETUP1_CLOSES, current=126.0, today_open=124.5, today_low=124.8)
    results = {setup_id: check(metrics) for setup_id, check in setups.ALL_SETUPS}
    assert results["setup1_uptrend_pullback"] is not None
    assert results["setup1_uptrend_pullback"].setup_id == "setup1_uptrend_pullback"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v -k "setup5 or all_setups"`
Expected: FAIL with `AttributeError: module 'app.setups' has no attribute 'check_setup_5'`

- [ ] **Step 3: Write the implementation**

Append to `app/setups.py`:

```python
def check_setup_5(metrics: TickerMetrics) -> SetupMatch | None:
    if metrics.sma200 is None:
        return None

    hard_ok = (
        return_n(metrics, 60) > 0
        and metrics.current_price >= metrics.sma200 * (1 - t.SETUP5_SMA200_PROXIMITY)
        and t.SETUP5_RETURN_30D_MIN <= return_n(metrics, 30) <= t.SETUP5_RETURN_30D_MAX
        and return_n(metrics, 10) < 0
        and improving(metrics, t.SETUP5_IMPROVING_WINDOW)
        and return_n(metrics, 3) > 0
        and stopped_new_lows(metrics, t.SETUP5_STOPPED_LOWS_WINDOW)
    )
    if not hard_ok:
        return None

    return SetupMatch(
        setup_id="setup5_deep_pullback",
        label="Deep Pullback",
        is_ideal=False,
        ideal_reasons=[],
        numbers={
            "60D return": return_n(metrics, 60),
            "30D return": return_n(metrics, 30),
            "10D return": return_n(metrics, 10),
        },
    )


ALL_SETUPS = [
    ("setup1_uptrend_pullback", check_setup_1),
    ("setup2_momentum_dip", check_setup_2),
    ("setup3_breakout_retest", check_setup_3),
    ("setup4_oversold_reversal", check_setup_4),
    ("setup5_deep_pullback", check_setup_5),
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_setups.py -v`
Expected: PASS (all tests green — Setups 1 through 5)

- [ ] **Step 5: Commit**

```bash
git add app/setups.py tests/test_setups.py
git commit -m "$(cat <<'EOF'
Add Setup 5 (Deep Pullback) and the ALL_SETUPS registry

Completes the 5 entry-point setups. ALL_SETUPS is what app/alerts.py
(next tasks) iterates to check every ticker against every setup.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Price chart rendering

**Files:**
- Create: `app/charts.py`
- Modify: `requirements.txt`
- Test: `tests/test_charts.py`

**Interfaces:**
- Consumes: `app.technicals.{TickerMetrics, rolling_sma}` (Task 1)
- Produces: `render_price_chart(metrics: TickerMetrics) -> bytes`

- [ ] **Step 1: Write the failing test**

Create `tests/test_charts.py`:

```python
from app import charts, technicals


def _metrics(n=220):
    closes = [100.0 + i * 0.1 for i in range(n)]
    lows = [c * 0.99 for c in closes]
    metrics = technicals.build_metrics(
        "AAPL", closes, lows,
        current_price=closes[-1] + 1, today_open=closes[-1], today_intraday_low=closes[-1] - 1,
    )
    assert metrics is not None
    return metrics


def test_render_price_chart_returns_valid_png_bytes():
    png_bytes = charts.render_price_chart(_metrics())
    assert isinstance(png_bytes, bytes)
    assert len(png_bytes) > 0
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_price_chart_works_with_minimum_history():
    # Exactly MIN_HISTORY_SESSIONS closes — the shortest history any
    # triggered alert could ever have.
    png_bytes = charts.render_price_chart(_metrics(n=technicals.MIN_HISTORY_SESSIONS))
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.charts'`

- [ ] **Step 3: Write the implementation**

Add to `requirements.txt` (after the `pandas>=2.0` line added in Task 3):

```
matplotlib>=3.8
```

Create `app/charts.py`:

```python
"""Renders the price chart attached to every entry-point alert. Pure
rendering — takes a TickerMetrics and returns PNG bytes, no I/O beyond
the in-memory buffer.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.technicals import TickerMetrics, rolling_sma

CHART_WINDOW_SESSIONS = 126
_MA_COLORS = {20: "#ff7f0e", 50: "#2ca02c", 200: "#d62728"}


def render_price_chart(metrics: TickerMetrics) -> bytes:
    window_closes = metrics.daily_closes[-CHART_WINDOW_SESSIONS:]
    closes = window_closes + [metrics.current_price]
    x = list(range(len(closes)))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(x, closes, label="Price", color="#1f77b4", linewidth=1.5)

    for window, color in _MA_COLORS.items():
        sma_series = rolling_sma(window_closes, window)
        sma_series = sma_series + [sma_series[-1] if sma_series else None]
        if any(v is not None for v in sma_series):
            ax.plot(x, sma_series, label=f"SMA{window}", color=color, linewidth=1.0)

    ax.scatter([x[-1]], [metrics.current_price], color="black", zorder=5, label="Current")
    ax.set_title(f"{metrics.ticker} — trailing {len(window_closes)} sessions")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()
```

- [ ] **Step 4: Install the new dependency and run tests**

Run: `.venv/bin/pip install -r requirements.txt`
Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: PASS (both tests green)

- [ ] **Step 5: Commit**

```bash
git add app/charts.py requirements.txt tests/test_charts.py
git commit -m "$(cat <<'EOF'
Add price chart rendering for entry-point alerts

Renders trailing ~6-month price + SMA20/50/200 overlays as a PNG,
attached to every alert so the setup's trigger is visible at a glance.
Adds matplotlib as a new dependency (Agg backend, no display needed).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: `alert_state` migration and `price_history` removal

**Files:**
- Modify: `app/db.py`
- Modify: `tests/test_db.py`
- Modify: `app/commands/remove.py`, `app/commands/sell.py`, `tests/test_commands.py`
  (added mid-execution — see amendment below; not part of the original plan text)

**Interfaces:**
- Produces: `alert_state` table now accepts the 5 new `alert_type` values instead of `watchlist_drop`/`holding_gain`; `price_history` table, `insert_price_snapshot`, and `prune_price_history` are removed; new `db.clear_all_alert_state(conn, ticker)`.

**Amendment (discovered during execution, not in the original plan):** tightening
`alert_state`'s `CHECK` constraint breaks two call sites the original plan
never accounted for — `app/commands/remove.py:37` and `app/commands/sell.py:56`
call `db.clear_alert_state(conn, ticker, "watchlist_drop"/"holding_gain")`
directly, outside `app/alerts.py`, to clean up alert state when a stock is
unwatched or sold. Under the new system a ticker can be in-alert for any of
5 setup types at once, so a single hardcoded old-style key no longer makes
sense. Fix: add `db.clear_all_alert_state(conn, ticker)` — deletes every
`alert_state` row for that ticker regardless of `alert_type` — and have both
command handlers call it instead of the old single-key `clear_alert_state`
call. This keeps `db.py` self-contained (no need to import `setups.ALL_SETUPS`
to enumerate the 5 ids from a command handler).

```python
def clear_all_alert_state(conn: sqlite3.Connection, ticker: str) -> None:
    """Delete every alert_state row for a ticker, regardless of alert_type.

    Called when a stock leaves the watchlist or is sold. A ticker can be
    "in alert" for any of the 5 setup types at once, so this clears all of
    them rather than requiring the caller to enumerate setup ids.
    """
    conn.execute("DELETE FROM alert_state WHERE ticker = ?", (ticker,))
    conn.commit()
```

In `app/commands/remove.py:37`, replace `db.clear_alert_state(conn, ticker, "watchlist_drop")`
with `db.clear_all_alert_state(conn, ticker)`. In `app/commands/sell.py:56`,
replace `db.clear_alert_state(conn, ticker, "holding_gain")` with
`db.clear_all_alert_state(conn, ticker)`.

Update the two tests in `tests/test_commands.py` that exercise this
(`test_sold_clears_holding_gain_alert_state`, `test_remove_clears_watchlist_alert_state`)
to seed and assert against a new-style setup id (e.g. `"setup1_uptrend_pullback"`)
instead of `"holding_gain"`/`"watchlist_drop"`, e.g.:

```python
def test_sold_clears_holding_gain_alert_state(conn):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    update, context = make_update_and_context(conn, "Sold Apple")

    with patch("app.commands.sell.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")), \
         patch("app.commands.sell.prices.get_current_price", return_value=120.0):
        run(handlers.handle_text(update, context))

    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback") is None


def test_remove_clears_watchlist_alert_state(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    update, context = make_update_and_context(conn, "Remove Apple")

    with patch("app.commands.remove.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")):
        run(handlers.handle_text(update, context))

    # Stale state would otherwise suppress the first alert after re-adding.
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback") is None
```

**Second amendment (discovered during Task 12's review, not in the original plan):**
removing `insert_price_snapshot`/`prune_price_history` from `app/db.py` (this
task's own scope) leaves two more unaccounted-for callers that will crash at
runtime: `bot.py`'s `poll_job` calls `db.prune_price_history(conn, 36)`
directly, and also calls `prices.snapshot_active_tickers(conn, all_tickers)`,
which itself calls the now-deleted `db.insert_price_snapshot`. Neither is
touched by Task 12's original scope, and Task 15 (which fully rewrites
`bot.py`) is what was originally going to clean this up — but that leaves
`bot.py` broken on every poll cycle for the tasks in between, with no test
coverage to catch it (bot.py isn't unit tested). Fix now, as part of Task 12,
rather than leaving it dangling:

- In `app/prices.py`, delete the `snapshot_active_tickers` function entirely
  (it's now orphaned — its only reason to exist was calling
  `insert_price_snapshot`). This means Task 15's own "remove
  `snapshot_active_tickers` from `app/prices.py`" step becomes a no-op
  verification (confirm via grep that it's already gone) rather than new
  work when that task runs.
- In `bot.py`'s `poll_job`, remove the `all_tickers` computation, the
  `await asyncio.to_thread(prices.snapshot_active_tickers, conn, all_tickers)`
  call, and the `await asyncio.to_thread(db.prune_price_history, conn, 36)`
  call. Leave everything else in `bot.py` — including the still-valid
  3-argument `alerts.check_and_fire_alerts(conn, settings, send)` call and
  the single-argument `send(text: str)` closure — untouched; those are only
  supposed to change when Task 13/15 land, not now. Update the module
  docstring's first sentence (currently "Snapshot prices for every active
  ticker, check for threshold breaches, and prune old snapshot history.") to
  drop the now-false snapshot/prune claims, e.g. "Check for threshold
  breaches and fire alerts." Also drop `prices` from bot.py's
  `from app import alerts, db, handlers, prices` import line — it's no
  longer used there.

- [ ] **Step 1: Write the failing tests**

Modify `tests/test_db.py` — replace the existing `test_price_history_insert_and_prune` test (lines 47-59) with:

```python
def test_migrate_removes_price_history_table(conn):
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='price_history'"
    ).fetchone() is None
    db.migrate(conn)  # must not raise even though the table is already gone
```

Replace `test_alert_state_set_and_clear` (lines 62-72) with:

```python
def test_alert_state_set_and_clear(conn):
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback") is None

    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    state = db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback")
    assert state["in_alert"] == 1
    assert state["last_alerted_at"] is not None

    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", False)
    state = db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback")
    assert state["in_alert"] == 0
```

Replace `test_clear_alert_state_removes_the_row` and `test_clear_alert_state_is_scoped_to_alert_type` (lines 150-163) with:

```python
def test_clear_alert_state_removes_the_row(conn):
    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    db.clear_alert_state(conn, "AAPL", "setup1_uptrend_pullback")
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback") is None


def test_clear_alert_state_is_scoped_to_alert_type(conn):
    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    db.set_in_alert(conn, "AAPL", "setup2_momentum_dip", True)
    db.clear_alert_state(conn, "AAPL", "setup1_uptrend_pullback")
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback") is None
    assert db.get_alert_state(conn, "AAPL", "setup2_momentum_dip") is not None
```

Append to the end of `tests/test_db.py` (under the existing "Migration" section):

```python
def test_migrate_recreates_alert_state_with_new_alert_types(conn):
    # Simulate a live DB still on the old alert_state schema/values.
    conn.execute("DROP TABLE alert_state")
    conn.execute(
        """CREATE TABLE alert_state (
               ticker TEXT NOT NULL,
               alert_type TEXT NOT NULL CHECK(alert_type IN ('watchlist_drop','holding_gain')),
               in_alert INTEGER NOT NULL DEFAULT 0,
               last_alerted_at TEXT,
               PRIMARY KEY (ticker, alert_type)
           )"""
    )
    conn.execute(
        "INSERT INTO alert_state (ticker, alert_type, in_alert) VALUES ('AAPL', 'watchlist_drop', 1)"
    )
    conn.commit()

    db.migrate(conn)

    # The old row's semantics no longer apply under the new system.
    assert conn.execute("SELECT * FROM alert_state").fetchall() == []

    # New alert types are accepted...
    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback")["in_alert"] == 1

    # ...and the old ones are now rejected by the CHECK constraint.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alert_state (ticker, alert_type, in_alert) VALUES ('TSLA', 'watchlist_drop', 1)"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: FAIL — `test_migrate_removes_price_history_table` fails because `price_history` still exists; the `alert_state` tests fail with `sqlite3.IntegrityError` (old CHECK still rejects the new values); `test_migrate_recreates_alert_state_with_new_alert_types` fails on the `db.set_in_alert(..., "setup1_uptrend_pullback", ...)` line

- [ ] **Step 3: Write the implementation**

Modify `app/db.py` — add a new module-level constant right before `SCHEMA_SQL` (so both `SCHEMA_SQL` and `migrate()` build the `alert_state` table from the same column definition instead of duplicating it):

```python
_ALERT_STATE_COLUMNS_SQL = """
    ticker TEXT NOT NULL,
    alert_type TEXT NOT NULL CHECK(alert_type IN (
        'setup1_uptrend_pullback',
        'setup2_momentum_dip',
        'setup3_breakout_retest',
        'setup4_oversold_reversal',
        'setup5_deep_pullback'
    )),
    in_alert INTEGER NOT NULL DEFAULT 0,
    last_alerted_at TEXT,
    PRIMARY KEY (ticker, alert_type)
"""
```

Then replace the `price_history` and `alert_state` table definitions inside `SCHEMA_SQL` (lines 32-46) with:

```python
CREATE TABLE IF NOT EXISTS alert_state (
{_ALERT_STATE_COLUMNS_SQL}
);
"""
```

This means `SCHEMA_SQL` itself must become an f-string — change its opening line from `SCHEMA_SQL = """` to `SCHEMA_SQL = f"""`. (This removes the `price_history` `CREATE TABLE` and its index entirely, and replaces the `alert_state` `CHECK` values. Keep the `watchlist` and `holdings` table definitions above it unchanged.)

Modify `migrate()` (currently lines 74-89) — add the price_history drop and alert_state recreation after the existing holdings-column logic, before the final `conn.commit()`:

```python
def migrate(conn: sqlite3.Connection) -> None:
    """Apply schema changes to an existing database. Each change checks
    the current schema first, making this idempotent and safe to run on
    every startup."""
    holdings_columns = {row["name"] for row in conn.execute("PRAGMA table_info(holdings)")}

    if "sell_price" not in holdings_columns:
        conn.execute("ALTER TABLE holdings ADD COLUMN sell_price REAL")
    if "sell_date" not in holdings_columns:
        conn.execute("ALTER TABLE holdings ADD COLUMN sell_date TEXT")

    # price_history existed only to serve the old trailing-window alert
    # design (see git history) and is unused by anything else.
    conn.execute("DROP TABLE IF EXISTS price_history")

    # SQLite can't alter a CHECK constraint in place. If alert_state is
    # still on the old (watchlist_drop/holding_gain) values, recreate it
    # with the new setup-based ones (same _ALERT_STATE_COLUMNS_SQL as
    # SCHEMA_SQL) — dropping existing rows, since the old alert semantics
    # don't mean anything under the new system.
    alert_state_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alert_state'"
    ).fetchone()
    if alert_state_row is not None and "watchlist_drop" in alert_state_row["sql"]:
        conn.execute("ALTER TABLE alert_state RENAME TO alert_state_old")
        conn.execute(f"CREATE TABLE alert_state ({_ALERT_STATE_COLUMNS_SQL})")
        conn.execute("DROP TABLE alert_state_old")

    conn.commit()
```

Remove the entire "Price history" section from `app/db.py` (currently lines 187-202: the `# --- Price history ---` comment header, `insert_price_snapshot`, and `prune_price_history`) — delete all of it, including the header comment.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: PASS (all tests green)

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "$(cat <<'EOF'
Migrate alert_state to the 5 new setup types, remove price_history

alert_state's CHECK constraint can't be altered in place, so migrate()
recreates the table with the new setup-based alert_type values,
discarding old rows (their watchlist_drop/holding_gain semantics no
longer apply). price_history and its two functions are removed — they
only ever served the old trailing-window design.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Rewrite `app/alerts.py` orchestration

**Files:**
- Modify: `app/alerts.py` (full rewrite)
- Modify: `tests/test_alerts.py` (full rewrite)

**Interfaces:**
- Consumes: `app.technicals.{TickerMetrics, get_cached_daily_bars}` (Tasks 1, 4), `app.prices.fetch_live_price` (Task 3), `app.setups.{SetupMatch, ALL_SETUPS}` (Task 10), `app.charts.render_price_chart` (Task 11), `app.db.{list_watchlist, list_distinct_holding_tickers, get_alert_state, set_in_alert}` (existing)
- Produces: `SendFn = Callable[[str, bytes], Awaitable[None]]`; `build_ticker_metrics(ticker: str) -> TickerMetrics | None`; `check_and_fire_alerts(conn, send: SendFn) -> None` (note: no longer takes a `settings` argument — thresholds are hardcoded now)

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `tests/test_alerts.py`:

```python
import asyncio
import sqlite3
from unittest.mock import patch

import pytest

from app import alerts, db, setups, technicals


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)
    yield connection
    connection.close()


class FakeSender:
    def __init__(self):
        self.messages: list[tuple[str, bytes]] = []

    async def send(self, text: str, chart_png: bytes) -> None:
        self.messages.append((text, chart_png))


def run(coro):
    return asyncio.run(coro)


_DUMMY_METRICS = technicals.TickerMetrics(
    ticker="AAPL",
    current_price=100.0,
    today_open=100.0,
    today_intraday_low=99.0,
    daily_closes=[100.0] * technicals.MIN_HISTORY_SESSIONS,
    daily_lows=[99.0] * technicals.MIN_HISTORY_SESSIONS,
    sma20=100.0,
    sma50=100.0,
    sma200=100.0,
    sma50_5d_ago=100.0,
    sma200_20d_ago=100.0,
)


def _match():
    return setups.SetupMatch(
        setup_id="fake_setup",
        label="Fake Setup",
        is_ideal=False,
        ideal_reasons=[],
        numbers={"30D return": 0.1},
    )


def test_setup_match_fires_once_per_episode(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=_DUMMY_METRICS), \
         patch("app.setups.ALL_SETUPS", [("fake_setup", lambda m: _match())]), \
         patch("app.charts.render_price_chart", return_value=b"PNGDATA"):
        run(alerts.check_and_fire_alerts(conn, sender.send))
        run(alerts.check_and_fire_alerts(conn, sender.send))  # still matching

    assert len(sender.messages) == 1
    text, chart = sender.messages[0]
    assert "AAPL" in text
    assert "Fake Setup" in text
    assert chart == b"PNGDATA"


def test_setup_clears_and_can_refire(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    sender = FakeSender()
    matching = True

    def fake_check(metrics):
        return _match() if matching else None

    with patch("app.alerts.build_ticker_metrics", return_value=_DUMMY_METRICS), \
         patch("app.setups.ALL_SETUPS", [("fake_setup", fake_check)]), \
         patch("app.charts.render_price_chart", return_value=b"PNGDATA"):
        run(alerts.check_and_fire_alerts(conn, sender.send))
        assert len(sender.messages) == 1

        matching = False
        run(alerts.check_and_fire_alerts(conn, sender.send))
        assert len(sender.messages) == 1  # cleared silently

        matching = True
        run(alerts.check_and_fire_alerts(conn, sender.send))
        assert len(sender.messages) == 2  # new episode


def test_holding_and_watchlist_tickers_are_both_scanned(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=_DUMMY_METRICS), \
         patch("app.setups.ALL_SETUPS", [("fake_setup", lambda m: _match())]), \
         patch("app.charts.render_price_chart", return_value=b"PNGDATA"):
        run(alerts.check_and_fire_alerts(conn, sender.send))

    tickers_alerted = {text.split()[1] for text, _ in sender.messages}
    assert tickers_alerted == {"AAPL", "TSLA"}


def test_no_alert_when_metrics_unavailable(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=None), \
         patch("app.setups.ALL_SETUPS", [("fake_setup", lambda m: _match())]):
        run(alerts.check_and_fire_alerts(conn, sender.send))

    assert sender.messages == []


def test_build_ticker_metrics_returns_none_without_a_cached_bars(conn):
    with patch("app.technicals.get_cached_daily_bars", return_value=None):
        assert alerts.build_ticker_metrics("AAPL") is None


def test_build_ticker_metrics_returns_none_without_a_live_price():
    bars = ([100.0] * technicals.MIN_HISTORY_SESSIONS, [99.0] * technicals.MIN_HISTORY_SESSIONS)
    with patch("app.technicals.get_cached_daily_bars", return_value=bars), \
         patch("app.prices.fetch_live_price", return_value=None):
        assert alerts.build_ticker_metrics("AAPL") is None


def test_build_ticker_metrics_combines_cache_and_live_price():
    bars = ([100.0] * technicals.MIN_HISTORY_SESSIONS, [99.0] * technicals.MIN_HISTORY_SESSIONS)
    with patch("app.technicals.get_cached_daily_bars", return_value=bars), \
         patch("app.prices.fetch_live_price", return_value=(105.0, 104.0, 103.0)):
        metrics = alerts.build_ticker_metrics("AAPL")
    assert metrics is not None
    assert metrics.current_price == 105.0
    assert metrics.today_open == 104.0
    assert metrics.today_intraday_low == 103.0


def test_format_message_includes_tags_and_numbers():
    match = setups.SetupMatch(
        setup_id="setup4_oversold_reversal",
        label="Oversold Reversal",
        is_ideal=True,
        ideal_reasons=["reclaiming the 20-day moving average"],
        numbers={"30D return": -0.15, "5D return": 0.01},
        risk_label="higher-risk",
    )
    text = alerts._format_message("AAPL", match)
    assert "AAPL" in text
    assert "Oversold Reversal" in text
    assert "ideal signal" in text
    assert "higher-risk" in text
    assert "-15.0%" in text
    assert "reclaiming the 20-day moving average" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_alerts.py -v`
Expected: FAIL — `AttributeError: <module 'app.alerts'> does not have the attribute 'build_ticker_metrics'` (and `check_and_fire_alerts` still has the old 3-argument signature)

- [ ] **Step 3: Write the implementation**

Replace the entire contents of `app/alerts.py`:

```python
"""Entry-point alert orchestration: builds technical metrics for every
watchlist/holding ticker, runs the 5 setup checks (app/setups.py), and
fires (deduped) alerts via `send`.

`send` (the outbound-message callable) is injected rather than imported,
so this module's logic is fully testable with a stub — no Telegram
involved.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app import charts, db, prices, setups, technicals

SendFn = Callable[[str, bytes], Awaitable[None]]

_DOLLAR_METRICS = {"resistance", "current price"}


def build_ticker_metrics(ticker: str) -> technicals.TickerMetrics | None:
    """Combine the cached daily bars with a fresh live price into a
    TickerMetrics, or None if either is unavailable."""
    bars = technicals.get_cached_daily_bars(ticker)
    if bars is None:
        return None
    live = prices.fetch_live_price(ticker)
    if live is None:
        return None
    daily_closes, daily_lows = bars
    current_price, today_open, today_intraday_low = live
    return technicals.build_metrics(
        ticker, daily_closes, daily_lows, current_price, today_open, today_intraday_low
    )


def _format_message(ticker: str, match: setups.SetupMatch) -> str:
    tags = []
    if match.is_ideal:
        tags.append("ideal signal")
    if match.risk_label:
        tags.append(match.risk_label)
    tag_suffix = f" ({', '.join(tags)})" if tags else ""

    lines = [f"\U0001f514 {ticker} — {match.label}{tag_suffix}"]
    for name, value in match.numbers.items():
        if name in _DOLLAR_METRICS:
            lines.append(f"{name}: {value:.2f}")
        else:
            lines.append(f"{name}: {value:.1%}")
    if match.ideal_reasons:
        lines.append("Also: " + "; ".join(match.ideal_reasons))
    return "\n".join(lines)


async def _handle_setup_match(
    conn,
    send: SendFn,
    ticker: str,
    metrics: technicals.TickerMetrics,
    match: setups.SetupMatch | None,
    setup_id: str,
) -> None:
    """Apply the "once per episode" dedup rule for one (ticker, setup)."""
    state = db.get_alert_state(conn, ticker, setup_id)
    currently_in_alert = bool(state["in_alert"]) if state is not None else False

    if match is not None and not currently_in_alert:
        text = _format_message(ticker, match)
        chart_png = charts.render_price_chart(metrics)
        await send(text, chart_png)
        db.set_in_alert(conn, ticker, setup_id, True)
    elif match is None and currently_in_alert:
        db.set_in_alert(conn, ticker, setup_id, False)


async def check_and_fire_alerts(conn, send: SendFn) -> None:
    """Check every active watchlist/holding ticker against all 5 entry-
    point setups and fire (deduped) alerts via `send`."""
    watchlist_tickers = {row["ticker"] for row in db.list_watchlist(conn)}
    holding_tickers = set(db.list_distinct_holding_tickers(conn))
    tickers = sorted(watchlist_tickers | holding_tickers)

    for ticker in tickers:
        metrics = await asyncio.to_thread(build_ticker_metrics, ticker)
        if metrics is None:
            continue
        for setup_id, check in setups.ALL_SETUPS:
            match = check(metrics)
            await _handle_setup_match(conn, send, ticker, metrics, match, setup_id)
```

Remove `get_price_n_hours_ago` from `app/prices.py` — the old trailing-window design was its only caller, and that's gone now that `app/alerts.py` has been fully replaced above. (Leave `get_current_price`, `fetch_daily_bars`, and `fetch_live_price` in place — `get_current_price` is still used by `app/commands/purchase.py` and `app/commands/sell.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_alerts.py -v`
Expected: PASS (all tests green)

Also verify the cleanup: `grep -rn "get_price_n_hours_ago\|compute_trailing_change" app/ tests/` should print nothing.

- [ ] **Step 5: Commit**

```bash
git add app/alerts.py app/prices.py tests/test_alerts.py
git commit -m "$(cat <<'EOF'
Rewrite alerts.py for the 5-setup entry-point orchestration

check_and_fire_alerts now builds a TickerMetrics per ticker (cached
daily bars + a fresh live price), runs it through all 5 setup checks,
and applies the same once-per-episode dedup as before, per (ticker,
setup) this time. send() now takes a chart alongside the message text.
Also removes prices.get_price_n_hours_ago, orphaned now that the old
trailing-window compute_trailing_change is gone.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Drop old threshold settings from config and `.env.example`

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `Settings` no longer has `watchlist_drop_threshold`, `holding_gain_threshold`, or `trailing_window_hours` fields.

- [ ] **Step 1: Make the change**

Modify `app/config.py` — remove the three fields from the `Settings` dataclass:

```python
@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: int
    db_path: str
    poll_interval_minutes: int
    anthropic_api_key: str | None
```

And remove the corresponding three lines from `load_settings()`'s `return Settings(...)` call:

```python
    return Settings(
        telegram_bot_token=require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=int(require("TELEGRAM_CHAT_ID")),
        db_path=os.environ.get("DB_PATH", "data/tracker.db"),
        poll_interval_minutes=int(os.environ.get("POLL_INTERVAL_MINUTES", "15")),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
    )
```

Modify `.env.example` — remove these three lines:

```
WATCHLIST_DROP_THRESHOLD=-0.10
HOLDING_GAIN_THRESHOLD=0.10
TRAILING_WINDOW_HOURS=12
```

- [ ] **Step 2: Verify nothing else references the removed fields**

Run: `grep -rn "watchlist_drop_threshold\|holding_gain_threshold\|trailing_window_hours" app/ bot.py tests/`
Expected: no output (Tasks 12 and 13 already removed every consumer)

- [ ] **Step 3: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all tests green)

- [ ] **Step 4: Commit**

```bash
git add app/config.py .env.example
git commit -m "$(cat <<'EOF'
Drop the old trailing-window threshold settings

watchlist_drop_threshold, holding_gain_threshold, and
trailing_window_hours have no remaining consumers now that alerts.py
runs the 5 hardcoded entry-point setups instead.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Wire the daily cache-refresh job into `bot.py`

**Files:**
- Modify: `bot.py`

**Interfaces:**
- Consumes: `app.technicals.refresh_daily_cache` (Task 4), `app.alerts.check_and_fire_alerts` (Task 13)

- [ ] **Step 1: Make the change**

Replace the entire contents of `bot.py`:

```python
#!/usr/bin/env python3
"""Entrypoint: builds the Telegram Application, registers handlers, and
schedules the recurring price-poll and daily-cache-refresh jobs. Single
process, single event loop — no separate scheduler or web server needed
(long-polling, no webhook)."""

from __future__ import annotations

import asyncio
import io
import logging

from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from app import alerts, db, handlers, technicals
from app.config import load_settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DAILY_CACHE_REFRESH_SECONDS = 86400


def _active_tickers(conn) -> list[str]:
    watchlist_tickers = {row["ticker"] for row in db.list_watchlist(conn)}
    holding_tickers = set(db.list_distinct_holding_tickers(conn))
    return sorted(watchlist_tickers | holding_tickers)


async def daily_cache_refresh_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh the in-memory daily-bar cache used by the entry-point
    setups. Runs once at startup and every 24h after — daily bars barely
    change intraday, so there's no benefit to re-fetching a year of
    history on every 15-minute poll."""
    conn = context.bot_data["conn"]
    tickers = _active_tickers(conn)
    if tickers:
        await asyncio.to_thread(technicals.refresh_daily_cache, tickers)


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check every active ticker against the 5 entry-point setups and
    fire (deduped) alerts."""
    conn = context.bot_data["conn"]
    settings = context.bot_data["settings"]

    async def send(text: str, chart_png: bytes) -> None:
        await context.bot.send_photo(
            chat_id=settings.telegram_chat_id,
            photo=io.BytesIO(chart_png),
            caption=text,
        )

    await alerts.check_and_fire_alerts(conn, send)


def main() -> None:
    settings = load_settings()
    conn = db.get_connection(settings.db_path)
    db.init_db(conn)

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.bot_data["conn"] = conn
    application.bot_data["settings"] = settings

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("list", handlers.list_positions))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text))

    application.job_queue.run_repeating(
        daily_cache_refresh_job,
        interval=DAILY_CACHE_REFRESH_SECONDS,
        first=0,
    )
    application.job_queue.run_repeating(
        poll_job,
        interval=settings.poll_interval_minutes * 60,
        first=10,
    )

    logger.info(
        "Starting bot (poll interval: %s min, db: %s)",
        settings.poll_interval_minutes,
        settings.db_path,
    )
    application.run_polling()


if __name__ == "__main__":
    main()
```

Remove `snapshot_active_tickers` from `app/prices.py` — `bot.py` was its only caller, and the rewrite above no longer calls it (daily bars now come from `technicals.refresh_daily_cache`/`get_cached_daily_bars` instead).

- [ ] **Step 2: Smoke-test that the module imports cleanly**

Run: `.venv/bin/python -c "import bot"`
Expected: no output, exit code 0 (confirms no syntax/import errors)

- [ ] **Step 3: Verify the cleanup and run the full test suite**

Run: `grep -rn "snapshot_active_tickers" app/ bot.py tests/`
Expected: no output

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all tests green)

- [ ] **Step 4: Commit**

```bash
git add bot.py app/prices.py
git commit -m "$(cat <<'EOF'
Wire the daily cache-refresh job into bot.py

Adds a second JobQueue job (every 24h, plus once at startup) that
refreshes the in-memory daily-bar cache; the existing 15-minute poll
job now sends alerts as photo+caption via check_and_fire_alerts's new
two-argument send(). Also removes prices.snapshot_active_tickers, its
last caller.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Rewrite the manual smoke-test script

**Files:**
- Modify: `scripts/seed_test_data.py`

**Interfaces:**
- Consumes: `app.alerts.check_and_fire_alerts`, `app.setups.{SetupMatch, ALL_SETUPS}`, `app.technicals.TickerMetrics` (all from earlier tasks)

- [ ] **Step 1: Make the change**

Replace the entire contents of `scripts/seed_test_data.py`:

```python
#!/usr/bin/env python3
"""Manual smoke test for the entry-point alert/episode-dedup logic, no
Telegram or yfinance involved.

Fabricates a ticker that matches Setup 1 (Uptrend Pullback), fakes the
metrics-build step, and prints what check_and_fire_alerts would send —
so the setup/dedup logic can be eyeballed without waiting for a real
matching stock or wiring up Telegram.

Usage: python scripts/seed_test_data.py
"""

import asyncio
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import alerts, db, setups, technicals


async def fake_send(text: str, chart_png: bytes) -> None:
    print(f"  -> WOULD SEND ({len(chart_png)} byte chart):")
    for line in text.splitlines():
        print(f"     {line}")


_FAKE_METRICS = technicals.TickerMetrics(
    ticker="AAPL",
    current_price=124.0,
    today_open=124.5,
    today_intraday_low=123.0,
    daily_closes=[100.0] * technicals.MIN_HISTORY_SESSIONS,
    daily_lows=[99.0] * technicals.MIN_HISTORY_SESSIONS,
    sma20=118.0,
    sma50=110.0,
    sma200=100.0,
    sma50_5d_ago=108.0,
    sma200_20d_ago=99.0,
)


def _fake_match():
    return setups.SetupMatch(
        setup_id="setup1_uptrend_pullback",
        label="Uptrend Pullback",
        is_ideal=True,
        ideal_reasons=["3-day return improving"],
        numbers={"30D return": 0.127, "5D return": -0.046},
    )


async def main() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")

    matching = True

    def fake_check(metrics):
        return _fake_match() if matching else None

    with patch("app.alerts.build_ticker_metrics", return_value=_FAKE_METRICS), \
         patch("app.setups.ALL_SETUPS", [("setup1_uptrend_pullback", fake_check)]), \
         patch("app.charts.render_price_chart", return_value=b"FAKE-PNG-BYTES"):

        print("Scenario 1: AAPL matches Setup 1 -> expect one alert")
        await alerts.check_and_fire_alerts(conn, fake_send)

        print("Scenario 2: same poll again, still matching -> expect silence (episode dedup)")
        await alerts.check_and_fire_alerts(conn, fake_send)

        matching = False
        print("Scenario 3: AAPL no longer matches -> clears silently")
        await alerts.check_and_fire_alerts(conn, fake_send)

        matching = True
        print("Scenario 4: AAPL matches again -> expect a new episode alert")
        await alerts.check_and_fire_alerts(conn, fake_send)

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the script and verify the printed scenarios match expectations**

Run: `.venv/bin/python scripts/seed_test_data.py`
Expected output shape: Scenario 1 prints a "WOULD SEND" block; Scenario 2 prints nothing after its header (silent, deduped); Scenario 3 prints nothing after its header (silent clear); Scenario 4 prints a new "WOULD SEND" block

- [ ] **Step 3: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all tests green)

- [ ] **Step 4: Commit**

```bash
git add scripts/seed_test_data.py
git commit -m "$(cat <<'EOF'
Rewrite seed_test_data.py for the entry-point setup system

Same purpose as before (eyeball the alert/dedup logic without Telegram),
adapted to fake a Setup 1 match instead of a trailing-window breach.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Update README.md and CLAUDE.md

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- None (documentation only)

- [ ] **Step 1: Update README.md's intro and feature bullets**

Replace lines 1-9 of `README.md`:

```markdown
# investment-tracker

A personal Telegram bot that tracks a stock watchlist and your holdings, and
flags a stock reaching a technically attractive **entry point** — a strong or
improving price structure that's temporarily pulled back or reversed.

- **5 entry-point setups** — Uptrend Pullback, Momentum + First Dip, Breakout
  Retest, Oversold Reversal, and Deep Pullback — each with its own trigger
  conditions. See
  [docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md](docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md)
  for the exact rules.
- Scans both your watchlist and your current holdings.
- Alerts fire **once per episode**, not on every check, and each one includes
  a price chart.
```

- [ ] **Step 2: Update the Configuration table**

Replace the Configuration section's table and the paragraph after it:

```markdown
## Configuration

Set in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `POLL_INTERVAL_MINUTES` | `15` | How often prices are checked |

The 5 setups' ~40 numeric thresholds (return bands, pullback depth, moving
average windows, etc.) are hardcoded in `app/setup_thresholds.py` rather than
`.env` — there are too many to expose sanely as environment variables. Tune
them by editing that file and restarting the bot.
```

- [ ] **Step 3: Update the Development section's test count**

Run: `.venv/bin/python -m pytest tests/ -q` and read the passed-test count off its final line (e.g. `212 passed in 0.84s` means the count is 212).

Keep the `## Development` heading as-is; replace only the ```bash fenced block under it with:

```bash
.venv/bin/python -m pytest tests/ -q      # <N> tests, no network required
.venv/bin/python scripts/seed_test_data.py  # prints setup/dedup logic against a fake scenario
```

where `<N>` is literally replaced by the passed-test count you just read (e.g. write `212 tests, no network required`, not the placeholder text `<N>`).

- [ ] **Step 4: Update the Notes section**

Replace the first bullet of the Notes section (the one about "12-hour baseline... intraday history"):

```markdown
- Daily price history (~2 years per ticker) is cached in memory once a day;
  the 15-minute poll re-fetches only the live price and re-evaluates all 5
  setups against the cached bars — see
  [docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md](docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md).
```

- [ ] **Step 5: Update CLAUDE.md's Tech Stack, file map, design decisions, and status**

In the **Tech Stack** section, add a line after the `anthropic` bullet:

```markdown
- **`matplotlib`** renders the price chart attached to every entry-point alert.
```

In the **How the code is organized** table, replace the `app/alerts.py` row and add four new rows after it:

```markdown
| `app/technicals.py` | Daily-bar caching and technical metric math (moving averages, returns, breakout detection) for the alert setups |
| `app/setups.py` | The 5 entry-point setup checks (pure functions: `TickerMetrics` in, a match or `None` out) |
| `app/setup_thresholds.py` | All ~40 numeric thresholds the setups use, hardcoded and tunable by editing the file |
| `app/charts.py` | Renders the price+moving-average chart attached to each alert |
| `app/alerts.py` | Orchestrates: builds metrics per ticker, runs all 5 setup checks, applies once-per-episode dedup |
```

In **Design decisions worth knowing**, add a new bullet (place it right after the "Alert sensitivity is configurable, not hardcoded" bullet, and update that bullet too since it's now only half true):

Replace the "Alert sensitivity is configurable, not hardcoded" bullet with:

```markdown
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
```

In **Where things stand, and what's next**, add a new paragraph right after the header (before the "Built and running" paragraph):

```markdown
**Alert engine redesigned — added 2026-09-05** (see
`docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md` and
`docs/superpowers/plans/2026-09-05-entry-point-alerts.md`). Replaces the old
"±10% in 12h" threshold alerts with 5 technical entry-point setups, scanning
both the watchlist and current holdings. All tests pass. **Not yet
live-verified end-to-end** — needs a real run against real tickers to confirm
a genuine setup match produces a correctly-formatted Telegram photo+caption
alert (the logic is fully covered by offline tests, but nothing has sent a
real chart through the real bot yet).

```

- [ ] **Step 6: Run the full test suite one more time to confirm nothing broke**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all tests green)

- [ ] **Step 7: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
Update README and CLAUDE.md for the entry-point alert redesign

Documents the 5 new setups, the removed .env thresholds, the new files
(technicals/setups/setup_thresholds/charts), the alert_state CHECK
migration as an approved exception to "additive only," and the daily
cache-refresh architecture.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Post-plan note for whoever executes this

This plan intentionally does not add a "live end-to-end verification" task — that requires a real Telegram bot token, a real chat ID, and waiting for (or fabricating a ticker matching) a real setup against live yfinance data, none of which can be scripted into a TDD step. Once all 17 tasks are done and merged, run the bot against a real `.env` and watch for the first real alert before considering this fully proven, and update CLAUDE.md's "Not yet live-verified end-to-end" note once that happens.
