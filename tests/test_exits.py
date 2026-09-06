from app import exits, technicals


def _lerp_path(control_points, length):
    """Straight-line interpolation between (day_index, price) points —
    same helper as tests/test_setups.py and tests/test_technicals.py use,
    kept local so each test file's fixtures stay self-contained."""
    out = [None] * length
    for (i0, p0), (i1, p1) in zip(control_points, control_points[1:]):
        span = i1 - i0
        for d in range(i0, i1 + 1):
            frac = (d - i0) / span if span else 0
            out[d] = p0 + (p1 - p0) * frac
    return out


def _series(tail, flat_days=200, flat_price=100.0):
    return [flat_price] * flat_days + tail


def _build(control_points, current, length=60):
    """Build a TickerMetrics from a flat 200-session baseline plus a
    `length`-session tail defined by control points, ending at `current`."""
    tail = _lerp_path(control_points, length)
    closes = _series(tail)
    lows = [c * 0.99 for c in closes]
    metrics = technicals.build_metrics(
        "TEST", closes, lows, current, current + 0.5, current - 0.5
    )
    assert metrics is not None
    return metrics


_FLAT_METRICS = technicals.build_metrics(
    "FLAT",
    [100.0] * technicals.MIN_HISTORY_SESSIONS,
    [99.0] * technicals.MIN_HISTORY_SESSIONS,
    100.0,
    100.0,
    99.0,
)


# ---------- Take profit / stop loss / trailing stop ----------

def test_take_profit_triggers_above_threshold():
    match = exits.check_take_profit(avg_cost=100.0, current_price=131.0, take_profit_pct=0.30)
    assert match is not None
    assert match.exit_id == "exit_take_profit"
    assert match.label == "Take Profit"


def test_take_profit_does_not_trigger_below_threshold():
    assert exits.check_take_profit(avg_cost=100.0, current_price=129.0, take_profit_pct=0.30) is None


def test_take_profit_triggers_exactly_at_threshold():
    assert exits.check_take_profit(avg_cost=100.0, current_price=130.0, take_profit_pct=0.30) is not None


def test_stop_loss_triggers_below_threshold():
    match = exits.check_stop_loss(avg_cost=100.0, current_price=79.0, stop_loss_pct=0.20)
    assert match is not None
    assert match.exit_id == "exit_stop_loss"


def test_stop_loss_does_not_trigger_above_threshold():
    assert exits.check_stop_loss(avg_cost=100.0, current_price=81.0, stop_loss_pct=0.20) is None


def test_trailing_stop_triggers_after_arming():
    # Peak reached 120 (20% above the 100 cost, past the 10% arming guard),
    # now down 15% off that peak.
    match = exits.check_trailing_stop(avg_cost=100.0, current_price=102.0, peak=120.0, trailing_stop_pct=0.15)
    assert match is not None
    assert match.exit_id == "exit_trailing_stop"


def test_trailing_stop_does_not_arm_below_the_ten_percent_peak_guard():
    # Peak only 5% above cost — never armed, even though price is now well
    # off that peak. The hard stop-loss is the right rule for this shape,
    # not the trailing stop (there were never real gains to protect).
    match = exits.check_trailing_stop(avg_cost=100.0, current_price=90.0, peak=105.0, trailing_stop_pct=0.15)
    assert match is None


def test_trailing_stop_does_not_trigger_while_near_the_peak():
    match = exits.check_trailing_stop(avg_cost=100.0, current_price=118.0, peak=120.0, trailing_stop_pct=0.15)
    assert match is None


# ---------- Exit A: Trend Break ----------

_EXIT_A_SEVERE = [(0, 100), (30, 140), (40, 138), (50, 100), (59, 88)]
_EXIT_A_MILD = [(0, 100), (40, 112), (45, 111), (52, 104), (59, 99)]
_EXIT_A_NEVER_ABOVE = [(0, 100), (20, 98), (40, 94), (59, 88)]
_EXIT_A_SMALL_MARGIN = [(0, 100), (30, 140), (40, 138), (50, 100), (59, 120)]
_EXIT_A_STILL_UP = [(0, 100), (30, 140), (59, 150)]


def test_exit_trend_break_triggers_and_is_confirmed_on_a_severe_break():
    metrics = _build(_EXIT_A_SEVERE, current=85.0)
    match = exits.check_exit_trend_break(metrics)
    assert match is not None
    assert match.exit_id == "exit_trend_break"
    assert match.is_ideal is True
    assert match.soft_tag == "confirmed"


def test_exit_trend_break_triggers_without_confirmation_on_a_mild_break():
    metrics = _build(_EXIT_A_MILD, current=102.5)
    match = exits.check_exit_trend_break(metrics)
    assert match is not None
    assert match.is_ideal is False
    assert match.ideal_reasons == []


def test_exit_trend_break_does_not_trigger_if_never_recently_in_an_uptrend():
    metrics = _build(_EXIT_A_NEVER_ABOVE, current=85.0)
    assert exits.check_exit_trend_break(metrics) is None


def test_exit_trend_break_does_not_trigger_within_the_break_margin():
    metrics = _build(_EXIT_A_SMALL_MARGIN, current=123.0)
    assert exits.check_exit_trend_break(metrics) is None


def test_exit_trend_break_does_not_trigger_while_still_in_an_uptrend():
    metrics = _build(_EXIT_A_STILL_UP, current=151.0)
    assert exits.check_exit_trend_break(metrics) is None


def test_exit_trend_break_does_not_trigger_on_flat_history():
    assert exits.check_exit_trend_break(_FLAT_METRICS) is None


# ---------- Exit B: Momentum Breakdown ----------

_EXIT_B_NEWS = [(0, 100), (50, 120), (56, 119), (57, 110), (59, 108)]
_EXIT_B_SLIDE = [(0, 100), (45, 135), (54, 133), (59, 112)]
_EXIT_B_MILD = [(0, 100), (50, 110), (56, 109), (59, 105)]


def test_exit_momentum_breakdown_triggers_on_the_news_branch():
    metrics = _build(_EXIT_B_NEWS, current=107.0)
    match = exits.check_exit_momentum_breakdown(metrics)
    assert match is not None
    assert match.exit_id == "exit_momentum_breakdown"
    assert "news" in match.label.lower() or "gap" in match.label.lower() or "Momentum" in match.label
    assert match.is_ideal is True  # both softs hold: below SMA50, still falling today


def test_exit_momentum_breakdown_triggers_on_the_slide_branch():
    metrics = _build(_EXIT_B_SLIDE, current=110.0)
    match = exits.check_exit_momentum_breakdown(metrics)
    assert match is not None
    assert match.exit_id == "exit_momentum_breakdown"


def test_exit_momentum_breakdown_does_not_trigger_on_a_mild_decline():
    metrics = _build(_EXIT_B_MILD, current=104.0)
    assert exits.check_exit_momentum_breakdown(metrics) is None


def test_exit_momentum_breakdown_does_not_trigger_on_flat_history():
    assert exits.check_exit_momentum_breakdown(_FLAT_METRICS) is None


# ---------- Orchestration ----------

def test_check_all_exits_returns_every_id_with_none_when_nothing_fires():
    results = dict(
        exits.check_all_exits(
            _FLAT_METRICS,
            avg_cost=100.0,
            take_profit_pct=0.30,
            stop_loss_pct=0.20,
            trailing_stop_pct=0.15,
        )
    )
    assert set(results) == {
        "exit_take_profit",
        "exit_stop_loss",
        "exit_trailing_stop",
        "exit_trend_break",
        "exit_momentum_breakdown",
    }
    assert all(match is None for match in results.values())


def test_check_all_exits_fires_take_profit_from_avg_cost():
    results = dict(
        exits.check_all_exits(
            _FLAT_METRICS,  # current_price == 100.0
            avg_cost=70.0,
            take_profit_pct=0.30,
            stop_loss_pct=0.20,
            trailing_stop_pct=0.15,
        )
    )
    assert results["exit_take_profit"] is not None
