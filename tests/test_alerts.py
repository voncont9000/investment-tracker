import asyncio
import sqlite3
from unittest.mock import patch

import pytest

from app import alerts, db, setups, technicals


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)
    yield connection
    connection.close()


class FakeSender:
    def __init__(self):
        self.messages: list[tuple[str, bytes]] = []

    async def send(self, text: str, chart_png: bytes) -> None:
        self.messages.append((text, chart_png))


def run(coro):
    return asyncio.run(coro)


_DUMMY_METRICS = technicals.TickerMetrics(
    ticker="AAPL",
    current_price=100.0,
    today_open=100.0,
    today_intraday_low=99.0,
    daily_closes=[100.0] * technicals.MIN_HISTORY_SESSIONS,
    daily_lows=[99.0] * technicals.MIN_HISTORY_SESSIONS,
    sma20=100.0,
    sma50=100.0,
    sma200=100.0,
    sma50_5d_ago=100.0,
    sma200_20d_ago=100.0,
)


def _match():
    return setups.SetupMatch(
        setup_id="setup1_uptrend_pullback",
        label="Fake Setup",
        is_ideal=False,
        ideal_reasons=[],
        numbers={"30D return": 0.1},
    )


def test_setup_match_fires_once_per_episode(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=_DUMMY_METRICS), \
         patch("app.setups.ALL_SETUPS", [("setup1_uptrend_pullback", lambda m: _match())]), \
         patch("app.charts.render_price_chart", return_value=b"PNGDATA"):
        run(alerts.check_and_fire_alerts(conn, sender.send))
        run(alerts.check_and_fire_alerts(conn, sender.send))  # still matching

    assert len(sender.messages) == 1
    text, chart = sender.messages[0]
    assert "AAPL" in text
    assert "Fake Setup" in text
    assert chart == b"PNGDATA"


def test_setup_clears_and_can_refire(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    sender = FakeSender()
    matching = True

    def fake_check(metrics):
        return _match() if matching else None

    with patch("app.alerts.build_ticker_metrics", return_value=_DUMMY_METRICS), \
         patch("app.setups.ALL_SETUPS", [("setup1_uptrend_pullback", fake_check)]), \
         patch("app.charts.render_price_chart", return_value=b"PNGDATA"):
        run(alerts.check_and_fire_alerts(conn, sender.send))
        assert len(sender.messages) == 1

        matching = False
        run(alerts.check_and_fire_alerts(conn, sender.send))
        assert len(sender.messages) == 1  # cleared silently

        matching = True
        run(alerts.check_and_fire_alerts(conn, sender.send))
        assert len(sender.messages) == 2  # new episode


def test_holding_and_watchlist_tickers_are_both_scanned(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=_DUMMY_METRICS), \
         patch("app.setups.ALL_SETUPS", [("setup1_uptrend_pullback", lambda m: _match())]), \
         patch("app.charts.render_price_chart", return_value=b"PNGDATA"):
        run(alerts.check_and_fire_alerts(conn, sender.send))

    tickers_alerted = {text.split()[1] for text, _ in sender.messages}
    assert tickers_alerted == {"AAPL", "TSLA"}


def test_no_alert_when_metrics_unavailable(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=None), \
         patch("app.setups.ALL_SETUPS", [("setup1_uptrend_pullback", lambda m: _match())]):
        run(alerts.check_and_fire_alerts(conn, sender.send))

    assert sender.messages == []


def test_build_ticker_metrics_returns_none_without_a_cached_bars(conn):
    with patch("app.technicals.get_cached_daily_bars", return_value=None), \
         patch("app.technicals.refresh_daily_cache"):
        assert alerts.build_ticker_metrics("AAPL") is None


def test_build_ticker_metrics_returns_none_without_a_live_price():
    bars = ([100.0] * technicals.MIN_HISTORY_SESSIONS, [99.0] * technicals.MIN_HISTORY_SESSIONS)
    with patch("app.technicals.get_cached_daily_bars", return_value=bars), \
         patch("app.prices.fetch_live_price", return_value=None):
        assert alerts.build_ticker_metrics("AAPL") is None


def test_build_ticker_metrics_combines_cache_and_live_price():
    bars = ([100.0] * technicals.MIN_HISTORY_SESSIONS, [99.0] * technicals.MIN_HISTORY_SESSIONS)
    with patch("app.technicals.get_cached_daily_bars", return_value=bars), \
         patch("app.prices.fetch_live_price", return_value=(105.0, 104.0, 103.0)):
        metrics = alerts.build_ticker_metrics("AAPL")
    assert metrics is not None
    assert metrics.current_price == 105.0
    assert metrics.today_open == 104.0
    assert metrics.today_intraday_low == 103.0


def test_format_message_includes_tags_and_numbers():
    match = setups.SetupMatch(
        setup_id="setup4_oversold_reversal",
        label="Oversold Reversal",
        is_ideal=True,
        ideal_reasons=["reclaiming the 20-day moving average"],
        numbers={"30D return": -0.15, "5D return": 0.01},
        risk_label="higher-risk",
    )
    text = alerts._format_message("AAPL", match)
    assert "AAPL" in text
    assert "Oversold Reversal" in text
    assert "ideal signal" in text
    assert "higher-risk" in text
    assert "-15.0%" in text
    assert "reclaiming the 20-day moving average" in text
