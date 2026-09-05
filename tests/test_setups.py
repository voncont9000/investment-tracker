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
