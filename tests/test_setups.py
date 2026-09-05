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
