"""Fundamentals fetching, mocked against yfinance so no network is used.

Mirrors the mocking style in tests/test_ticker_resolver.py: patch the
network-touching call, assert on the shape our own code produces.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app import fundamentals


def _annual_df(rows: dict[str, list[float]], years: list[str]) -> pd.DataFrame:
    """Build a DataFrame shaped like yfinance's own: row labels as the index,
    period end-dates as columns."""
    dates = pd.to_datetime(years)
    return pd.DataFrame(rows, index=dates).T


@pytest.fixture
def mock_ticker():
    with patch("app.fundamentals.yf.Ticker") as mock_ticker_cls:
        yield mock_ticker_cls


def test_income_statement_summary_computes_growth_and_margins(mock_ticker):
    annual = _annual_df(
        {
            "Total Revenue": [110.0, 100.0],
            "Gross Profit": [55.0, 40.0],
            "Operating Income": [22.0, 20.0],
            "Net Income": [11.0, 10.0],
        },
        ["2025-12-31", "2024-12-31"],
    )
    quarterly = _annual_df(
        {
            "Total Revenue": [30.0, 28.0, 27.0, 26.0],
            "Gross Profit": [15.0, 14.0, 13.5, 13.0],
            "Operating Income": [6.0, 5.5, 5.0, 5.0],
            "Net Income": [3.0, 2.8, 2.6, 2.5],
        },
        ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"],
    )
    mock_ticker.return_value.financials = annual
    mock_ticker.return_value.quarterly_financials = quarterly

    result = fundamentals.get_income_statement_summary("TEST")

    assert result is not None
    periods = result["annual_periods"]
    assert len(periods) == 2

    # Rendered oldest-to-newest, even though yfinance's own columns are newest-first.
    oldest, newest = periods
    assert newest["revenue"] == 110.0
    assert newest["revenue_growth_pct"] == pytest.approx(10.0)
    assert newest["gross_margin_pct"] == pytest.approx(50.0)
    assert newest["operating_margin_pct"] == pytest.approx(20.0)
    assert newest["net_margin_pct"] == pytest.approx(10.0)

    assert oldest["revenue_growth_pct"] is None  # no prior period to compare against

    ttm = result["ttm"]
    assert ttm["revenue"] == pytest.approx(111.0)


def test_income_statement_summary_returns_none_when_unavailable(mock_ticker):
    mock_ticker.return_value.financials = pd.DataFrame()
    mock_ticker.return_value.quarterly_financials = pd.DataFrame()

    assert fundamentals.get_income_statement_summary("TEST") is None


def test_income_statement_summary_returns_none_on_exception(mock_ticker):
    mock_ticker.side_effect = Exception("network down")

    assert fundamentals.get_income_statement_summary("TEST") is None


def test_balance_sheet_summary_computes_ratios(mock_ticker):
    quarterly_bs = _annual_df(
        {
            "Total Debt": [50.0],
            "Stockholders Equity": [100.0],
            "Current Assets": [80.0],
            "Current Liabilities": [40.0],
            "Cash And Cash Equivalents": [20.0],
        },
        ["2026-06-30"],
    )
    mock_ticker.return_value.quarterly_balance_sheet = quarterly_bs

    result = fundamentals.get_balance_sheet_summary("TEST")

    assert result is not None
    assert result["debt_to_equity"] == pytest.approx(0.5)
    assert result["current_ratio"] == pytest.approx(2.0)
    assert result["cash"] == pytest.approx(20.0)


def test_balance_sheet_summary_falls_back_to_annual_when_quarterly_empty(mock_ticker):
    mock_ticker.return_value.quarterly_balance_sheet = pd.DataFrame()
    annual_bs = _annual_df({"Total Debt": [10.0], "Stockholders Equity": [40.0]}, ["2025-12-31"])
    mock_ticker.return_value.balance_sheet = annual_bs

    result = fundamentals.get_balance_sheet_summary("TEST")

    assert result is not None
    assert result["total_debt"] == 10.0
    assert result["current_ratio"] is None  # not present in this frame


def test_cash_flow_summary_computes_free_cash_flow(mock_ticker):
    annual_cf = _annual_df(
        {
            "Operating Cash Flow": [100.0],
            "Capital Expenditure": [-20.0],  # yfinance reports capex as negative
        },
        ["2025-12-31"],
    )
    mock_ticker.return_value.cashflow = annual_cf
    mock_ticker.return_value.quarterly_cashflow = pd.DataFrame()

    result = fundamentals.get_cash_flow_summary("TEST")

    assert result is not None
    assert result["annual_periods"][0]["free_cash_flow"] == pytest.approx(80.0)


def test_valuation_snapshot_converts_ownership_to_percent(mock_ticker):
    mock_ticker.return_value.info = {
        "currentPrice": 150.0,
        "trailingPE": 30.0,
        "heldPercentInsiders": 0.05,
        "heldPercentInstitutions": 0.6,
    }

    result = fundamentals.get_valuation_snapshot("TEST")

    assert result is not None
    assert result["insider_ownership_pct"] == pytest.approx(5.0)
    assert result["institutional_ownership_pct"] == pytest.approx(60.0)


def test_valuation_snapshot_returns_none_when_info_empty(mock_ticker):
    mock_ticker.return_value.info = {}

    assert fundamentals.get_valuation_snapshot("TEST") is None


def test_build_fundamentals_snapshot_notes_missing_data_without_crashing(mock_ticker):
    mock_ticker.return_value.financials = pd.DataFrame()
    mock_ticker.return_value.quarterly_financials = pd.DataFrame()
    mock_ticker.return_value.quarterly_balance_sheet = pd.DataFrame()
    mock_ticker.return_value.balance_sheet = pd.DataFrame()
    mock_ticker.return_value.cashflow = pd.DataFrame()
    mock_ticker.return_value.quarterly_cashflow = pd.DataFrame()
    mock_ticker.return_value.info = {}

    snapshot = fundamentals.build_fundamentals_snapshot("TEST")

    assert "Not available." in snapshot
    assert "TEST" in snapshot
