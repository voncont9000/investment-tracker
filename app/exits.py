"""Sell-side checks: three P&L rules against cost basis, plus two
technical exit setups in the same style as app/setups.py. Pure functions —
TickerMetrics (and for the P&L rules, plain numbers) in, an ExitMatch (or
None) out. No I/O, no DB — fully testable against synthetic metrics.

See docs/superpowers/specs/2026-09-05-sell-alerts-design.md for the design
this implements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app import setup_thresholds as t
from app.technicals import (
    TickerMetrics,
    max_single_day_drop,
    pct_below_high,
    return_n,
)


@dataclass
class ExitMatch:
    exit_id: str
    label: str
    is_ideal: bool
    ideal_reasons: list[str] = field(default_factory=list)
    numbers: dict[str, float] = field(default_factory=dict)
    risk_label: str | None = None
    # Word used for the "(...)" tag when is_ideal is True. The 5 entry
    # setups use "ideal signal" (app/alerts.py's default); a sell alert
    # reads better as "confirmed" — this "the pullback is now a
    # multi-signal break", not "here's an especially good buy".
    soft_tag: str = "confirmed"
    # Read by app/alerts.py's _format_message to label the message BUY vs
    # SELL — entry setups (app/setups.py) don't set this and default to
    # "BUY" there, since every exit exists specifically to say "sell".
    direction: str = "SELL"


# --- P&L rules against cost basis (app/db.avg_cost_by_ticker) ---

def check_take_profit(avg_cost: float, current_price: float, take_profit_pct: float) -> ExitMatch | None:
    if current_price < avg_cost * (1 + take_profit_pct):
        return None
    gain = (current_price - avg_cost) / avg_cost
    return ExitMatch(
        exit_id="exit_take_profit",
        label="Take Profit",
        is_ideal=False,
        numbers={"avg cost": avg_cost, "current price": current_price, "gain": gain},
    )


def check_stop_loss(avg_cost: float, current_price: float, stop_loss_pct: float) -> ExitMatch | None:
    if current_price > avg_cost * (1 - stop_loss_pct):
        return None
    loss = (current_price - avg_cost) / avg_cost
    return ExitMatch(
        exit_id="exit_stop_loss",
        label="Stop Loss",
        is_ideal=False,
        numbers={"avg cost": avg_cost, "current price": current_price, "loss": loss},
    )


# A trailing stop only arms once the peak is meaningfully above cost.
# Without this, a stock that fell steadily from the day it was bought would
# fire a "given back 15% of your gains" alert when there were never any
# gains to give back — the hard stop-loss is the correct rule for that
# shape. Hardcoded: a correctness guard, not a tunable preference.
TRAILING_STOP_ARM_MULTIPLIER = 1.10


def check_trailing_stop(
    avg_cost: float, current_price: float, peak: float, trailing_stop_pct: float
) -> ExitMatch | None:
    if peak < avg_cost * TRAILING_STOP_ARM_MULTIPLIER:
        return None
    if current_price > peak * (1 - trailing_stop_pct):
        return None
    off_peak = (current_price - peak) / peak
    return ExitMatch(
        exit_id="exit_trailing_stop",
        label="Trailing Stop",
        is_ideal=False,
        numbers={
            "avg cost": avg_cost,
            "current price": current_price,
            "peak": peak,
            "off peak": off_peak,
        },
    )


# --- Exit A: Trend Break ---

def check_exit_trend_break(metrics: TickerMetrics) -> ExitMatch | None:
    if metrics.sma50 is None or metrics.sma50_5d_ago is None:
        return None

    recently_in_uptrend = max(metrics.daily_closes[-t.EXIT_A_LOOKBACK_WINDOW:]) > metrics.sma50
    clear_break = metrics.current_price < metrics.sma50 * (1 - t.EXIT_A_BREAK_MARGIN)
    confirmed_by_two_closes = (
        metrics.daily_closes[-1] < metrics.sma50 and metrics.daily_closes[-2] < metrics.sma50
    )
    sma50_rolling_over = metrics.sma50 < metrics.sma50_5d_ago

    if not (recently_in_uptrend and clear_break and confirmed_by_two_closes and sma50_rolling_over):
        return None

    ideal_reasons = []
    if metrics.sma200 is not None and metrics.current_price < metrics.sma200:
        ideal_reasons.append("also below the 200-day moving average")
    if return_n(metrics, t.EXIT_A_SOFT_RETURN_WINDOW) < t.EXIT_A_SOFT_RETURN_MAX:
        ideal_reasons.append("20-day return already sharply negative")

    return ExitMatch(
        exit_id="exit_trend_break",
        label="Trend Break",
        is_ideal=bool(ideal_reasons),
        ideal_reasons=ideal_reasons,
        numbers={"% below SMA50": (metrics.sma50 - metrics.current_price) / metrics.sma50},
    )


# --- Exit B: Momentum Breakdown ---

def check_exit_momentum_breakdown(metrics: TickerMetrics) -> ExitMatch | None:
    if metrics.sma20 is None:
        return None

    news_branch = (
        max_single_day_drop(metrics, t.EXIT_B_NEWS_DROP_WINDOW) > t.EXIT_B_NEWS_DROP_THRESHOLD
        and metrics.current_price < metrics.sma20
    )
    slide_branch = (
        return_n(metrics, 5) <= t.EXIT_B_SLIDE_RETURN_5D_MAX
        and pct_below_high(metrics, t.EXIT_B_SLIDE_HIGH_WINDOW) >= t.EXIT_B_SLIDE_PCT_BELOW_HIGH_MIN
    )
    if not (news_branch or slide_branch):
        return None

    branch_label = "sudden drop" if news_branch else "sliding decline"
    ideal_reasons = []
    if metrics.sma50 is not None and metrics.current_price < metrics.sma50:
        ideal_reasons.append("also below the 50-day moving average")
    if return_n(metrics, 1) < 0:
        ideal_reasons.append("still falling today")

    return ExitMatch(
        exit_id="exit_momentum_breakdown",
        label=f"Momentum Breakdown ({branch_label})",
        is_ideal=bool(ideal_reasons),
        ideal_reasons=ideal_reasons,
        numbers={
            "5D return": return_n(metrics, 5),
            "% below 30D high": pct_below_high(metrics, t.EXIT_B_SLIDE_HIGH_WINDOW),
        },
    )


def check_all_exits(
    metrics: TickerMetrics,
    avg_cost: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    trailing_stop_pct: float,
) -> list[tuple[str, ExitMatch | None]]:
    """Run every exit check for one held ticker. The peak used by the
    trailing stop is the highest close in the entire cached daily-bar
    history (up to ~2 years — app.prices.fetch_daily_bars) or the current
    price, whichever is higher.

    Simplification vs. the design doc: rather than tracking the peak since
    each lot's exact purchase date (which would need a per-bar date, not
    just a list of closes), this uses all cached history as an
    approximation. Since the cache already covers ~2 years and holdings
    bought further back than that are the rare case for a personal
    tracker, the difference only matters for a handful of old positions —
    and even then it only makes the trailing stop arm *later*, never
    fire on a peak that didn't happen.
    """
    peak = max(metrics.daily_closes + [metrics.current_price])
    return [
        ("exit_take_profit", check_take_profit(avg_cost, metrics.current_price, take_profit_pct)),
        ("exit_stop_loss", check_stop_loss(avg_cost, metrics.current_price, stop_loss_pct)),
        ("exit_trailing_stop", check_trailing_stop(avg_cost, metrics.current_price, peak, trailing_stop_pct)),
        ("exit_trend_break", check_exit_trend_break(metrics)),
        ("exit_momentum_breakdown", check_exit_momentum_breakdown(metrics)),
    ]
