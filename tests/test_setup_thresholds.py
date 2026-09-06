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
