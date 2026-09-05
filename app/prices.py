"""yfinance wrappers for current price and trailing-N-hours-ago price.

Both functions are synchronous (yfinance/requests are blocking) — callers in
async code (Telegram handlers, the JobQueue poll job) must wrap calls in
asyncio.to_thread() so the event loop isn't stalled while a ticker list is
polled one-by-one.

The trailing-hours lookup goes straight to Yahoo's own intraday history
rather than our locally-collected price_history snapshots. That local table
only starts accumulating once a ticker is added, so a purely
snapshot-based lookup would leave a newly-added ticker with no usable
baseline for its first ~12 hours. Fetching directly from Yahoo means a
ticker added five minutes ago can still alert immediately.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yfinance as yf


def get_current_price(ticker: str) -> float | None:
    try:
        price = yf.Ticker(ticker).fast_info.get("lastPrice")
        if price is None:
            return None
        return float(price)
    except Exception:
        return None


def get_price_n_hours_ago(ticker: str, hours: int) -> float | None:
    """Return the close price of the intraday candle closest to now - hours.

    Uses 15m-interval history over the trailing 2 days, which comfortably
    covers a 12h lookback window (and any weekend/holiday gap where the
    market was simply closed — the closest prior candle is still valid).
    """
    try:
        history = yf.Ticker(ticker).history(period="2d", interval="15m")
        if history.empty:
            return None

        target = datetime.now(timezone.utc) - timedelta(hours=hours)
        index_utc = history.index.tz_convert("UTC")

        # Find the candle whose timestamp is closest to the target time.
        deltas = abs(index_utc - target)
        closest_pos = deltas.argmin()
        close_price = history.iloc[closest_pos]["Close"]
        if close_price is None:
            return None
        return float(close_price)
    except Exception:
        return None


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


def snapshot_active_tickers(conn, tickers: list[str]) -> None:
    """Fetch and store a current-price snapshot for each ticker.

    Not on the critical path for alerting (see module docstring) — this is
    purely for observability/debugging and as a fallback data source.
    Failures for individual tickers are swallowed so one bad ticker doesn't
    block the rest of the poll.
    """
    from app import db  # local import to avoid a circular import at module load

    for ticker in tickers:
        price = get_current_price(ticker)
        if price is not None:
            db.insert_price_snapshot(conn, ticker, price)
