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
