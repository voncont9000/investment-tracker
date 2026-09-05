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
