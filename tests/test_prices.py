from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from app import prices


def test_fetch_daily_bars_excludes_todays_partial_session():
    now = datetime.now(timezone.utc)
    two_days_ago = now - timedelta(days=2)
    yesterday = now - timedelta(days=1)
    df = pd.DataFrame(
        {"Close": [100.0, 101.0, 102.0], "Low": [99.0, 100.0, 101.0]},
        index=pd.DatetimeIndex([two_days_ago, yesterday, now], tz="UTC"),
    )
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = df

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        result = prices.fetch_daily_bars("AAPL")

    assert result == ([100.0, 101.0], [99.0, 100.0])


def test_fetch_daily_bars_returns_none_when_history_is_empty():
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame()

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_daily_bars("AAPL") is None


def test_fetch_daily_bars_returns_none_on_exception():
    fake_ticker = MagicMock()
    fake_ticker.history.side_effect = RuntimeError("network error")

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_daily_bars("AAPL") is None


def test_fetch_live_price_returns_current_open_and_day_low():
    fake_ticker = MagicMock()
    fake_ticker.fast_info = {"lastPrice": 150.0, "open": 148.0, "dayLow": 147.5}

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        result = prices.fetch_live_price("AAPL")

    assert result == (150.0, 148.0, 147.5)


def test_fetch_live_price_returns_none_when_a_field_is_missing():
    fake_ticker = MagicMock()
    fake_ticker.fast_info = {"lastPrice": 150.0}

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_live_price("AAPL") is None


def test_fetch_latest_headline_time_returns_the_max_pubdate():
    fake_ticker = MagicMock()
    fake_ticker.news = [
        {"content": {"pubDate": "2026-09-01T00:00:00Z"}},
        {"content": {"pubDate": "2026-09-06T12:06:22Z"}},
        {"content": {"pubDate": "2026-09-03T00:00:00Z"}},
    ]

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_latest_headline_time("AAPL") == "2026-09-06T12:06:22Z"


def test_fetch_latest_headline_time_returns_none_when_no_news():
    fake_ticker = MagicMock()
    fake_ticker.news = []

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_latest_headline_time("AAPL") is None


def test_fetch_latest_headline_time_skips_items_without_a_pubdate():
    fake_ticker = MagicMock()
    fake_ticker.news = [{"content": {}}, {"content": {"pubDate": "2026-09-01T00:00:00Z"}}]

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_latest_headline_time("AAPL") == "2026-09-01T00:00:00Z"


def test_fetch_latest_headline_time_returns_none_on_exception():
    fake_ticker = MagicMock()
    type(fake_ticker).news = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    with patch("app.prices.yf.Ticker", return_value=fake_ticker):
        assert prices.fetch_latest_headline_time("AAPL") is None


def test_fetch_live_price_returns_none_on_exception():
    # A plain class (not MagicMock) so the raising property lives only on
    # this one object, instead of leaking onto MagicMock's shared class.
    class ExplodingTicker:
        @property
        def fast_info(self):
            raise RuntimeError("boom")

    with patch("app.prices.yf.Ticker", return_value=ExplodingTicker()):
        assert prices.fetch_live_price("AAPL") is None
