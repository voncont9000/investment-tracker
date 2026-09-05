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
    max_single_day_drop,
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
