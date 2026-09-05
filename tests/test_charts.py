from app import charts, technicals


def _metrics(n=220):
    closes = [100.0 + i * 0.1 for i in range(n)]
    lows = [c * 0.99 for c in closes]
    metrics = technicals.build_metrics(
        "AAPL", closes, lows,
        current_price=closes[-1] + 1, today_open=closes[-1], today_intraday_low=closes[-1] - 1,
    )
    assert metrics is not None
    return metrics


def test_render_price_chart_returns_valid_png_bytes():
    png_bytes = charts.render_price_chart(_metrics())
    assert isinstance(png_bytes, bytes)
    assert len(png_bytes) > 0
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_price_chart_works_with_minimum_history():
    # Exactly MIN_HISTORY_SESSIONS closes — the shortest history any
    # triggered alert could ever have.
    png_bytes = charts.render_price_chart(_metrics(n=technicals.MIN_HISTORY_SESSIONS))
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_sma200_has_values_over_full_history_even_though_chart_window_is_shorter():
    # render_price_chart only shows the trailing CHART_WINDOW_SESSIONS
    # (126) sessions, but a real TickerMetrics always carries at least
    # MIN_HISTORY_SESSIONS (220) closes — enough for rolling_sma(..., 200)
    # to produce real values, which must survive into the plotted window.
    # PNG pixel content isn't practical to assert on, so this checks the
    # underlying data feeding the SMA200 line instead.
    metrics = _metrics(n=technicals.MIN_HISTORY_SESSIONS)
    full_sma200 = technicals.rolling_sma(metrics.daily_closes, 200)
    windowed = full_sma200[-charts.CHART_WINDOW_SESSIONS:]
    assert any(v is not None for v in windowed)
