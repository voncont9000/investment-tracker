from unittest.mock import patch

from app import ticker_resolver


def test_resolve_via_yahoo_search():
    with patch(
        "app.ticker_resolver._search_yahoo",
        return_value=("AAPL", "Apple Inc."),
    ):
        result = ticker_resolver.resolve_ticker("Apple")
    assert result == ("AAPL", "Apple Inc.")


def test_falls_back_to_static_table_when_yahoo_unavailable():
    with patch("app.ticker_resolver._search_yahoo", return_value=None):
        result = ticker_resolver.resolve_ticker("Tesla")
    assert result is not None
    ticker, _ = result
    assert ticker == "TSLA"


def test_fuzzy_match_handles_typo_in_fallback():
    with patch("app.ticker_resolver._search_yahoo", return_value=None):
        result = ticker_resolver.resolve_ticker("Microsft")  # typo
    assert result is not None
    ticker, _ = result
    assert ticker == "MSFT"


def test_returns_none_when_nothing_matches():
    with patch("app.ticker_resolver._search_yahoo", return_value=None):
        result = ticker_resolver.resolve_ticker("Totally Fictional Company Zzyzx")
    assert result is None


def test_empty_input_returns_none():
    result = ticker_resolver.resolve_ticker("   ")
    assert result is None
