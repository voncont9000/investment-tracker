"""yfinance wrappers for current price, daily bars, and the live intraday
snapshot used by the entry-point alert checks.

All functions are synchronous (yfinance/requests are blocking) — callers in
async code (Telegram handlers, the JobQueue poll job) must wrap calls in
asyncio.to_thread() so the event loop isn't stalled while a ticker list is
polled one-by-one.
"""

from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf


def get_current_price(ticker: str) -> float | None:
    try:
        price = yf.Ticker(ticker).fast_info.get("lastPrice")
        if price is None:
            return None
        return float(price)
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
