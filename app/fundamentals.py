"""yfinance wrappers for the fundamentals fed into an "Analyse X" report.

Same error-handling style as app/prices.py: each getter swallows exceptions
and returns None (or drops the affected period) rather than raising, since a
ticker with thin data shouldn't crash the report — it should just say so.
Every number here is computed in Python, not left for the model to derive
from raw figures, because multi-year percentage math is exactly the kind of
arithmetic a prompt shouldn't ask an LLM to do by hand.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

_MAX_ANNUAL_PERIODS = 5


def _row(df: pd.DataFrame, label: str) -> pd.Series | None:
    if df is None or df.empty or label not in df.index:
        return None
    return df.loc[label]


def _pct(numerator: float, denominator: float) -> float | None:
    if denominator in (0, None) or pd.isna(denominator) or pd.isna(numerator):
        return None
    return numerator / denominator * 100


def _sum_last_n_quarters(df: pd.DataFrame, label: str, n: int = 4) -> float | None:
    row = _row(df, label)
    if row is None:
        return None
    values = row.iloc[:n].dropna()
    if values.empty:
        return None
    return float(values.sum())


def get_income_statement_summary(ticker: str) -> dict[str, Any] | None:
    """Annual revenue/margin figures (oldest to newest) plus a trailing-twelve-month row.

    Returns None only if the annual statement itself couldn't be fetched;
    individual missing fields inside a period are represented as None.
    """
    try:
        t = yf.Ticker(ticker)
        annual = t.financials
        quarterly = t.quarterly_financials
    except Exception:
        return None

    if annual is None or annual.empty:
        return None

    revenue = _row(annual, "Total Revenue")
    gross_profit = _row(annual, "Gross Profit")
    operating_income = _row(annual, "Operating Income")
    net_income = _row(annual, "Net Income")
    if revenue is None:
        return None

    periods = []
    columns = list(annual.columns)[:_MAX_ANNUAL_PERIODS]
    for i, col in enumerate(columns):
        rev = revenue.get(col)
        if pd.isna(rev):
            continue
        prior_rev = revenue.get(columns[i + 1]) if i + 1 < len(columns) else None
        gp = gross_profit.get(col) if gross_profit is not None else None
        oi = operating_income.get(col) if operating_income is not None else None
        ni = net_income.get(col) if net_income is not None else None
        periods.append(
            {
                "fiscal_year_end": col.date().isoformat(),
                "revenue": float(rev),
                "revenue_growth_pct": _pct(rev - prior_rev, prior_rev) if prior_rev is not None else None,
                "gross_margin_pct": _pct(gp, rev) if gp is not None else None,
                "operating_margin_pct": _pct(oi, rev) if oi is not None else None,
                "net_margin_pct": _pct(ni, rev) if ni is not None else None,
            }
        )
    periods.reverse()  # yfinance columns are newest-first; display oldest-first

    ttm_revenue = _sum_last_n_quarters(quarterly, "Total Revenue")
    ttm_gross_profit = _sum_last_n_quarters(quarterly, "Gross Profit")
    ttm_operating_income = _sum_last_n_quarters(quarterly, "Operating Income")
    ttm_net_income = _sum_last_n_quarters(quarterly, "Net Income")
    ttm = None
    if ttm_revenue is not None:
        ttm = {
            "revenue": ttm_revenue,
            "gross_margin_pct": _pct(ttm_gross_profit, ttm_revenue) if ttm_gross_profit is not None else None,
            "operating_margin_pct": _pct(ttm_operating_income, ttm_revenue)
            if ttm_operating_income is not None
            else None,
            "net_margin_pct": _pct(ttm_net_income, ttm_revenue) if ttm_net_income is not None else None,
        }

    return {"annual_periods": periods, "ttm": ttm}


def get_balance_sheet_summary(ticker: str) -> dict[str, Any] | None:
    """Latest available balance-sheet snapshot (most recent quarter, else annual)."""
    try:
        t = yf.Ticker(ticker)
        bs = t.quarterly_balance_sheet
        if bs is None or bs.empty:
            bs = t.balance_sheet
    except Exception:
        return None

    if bs is None or bs.empty:
        return None

    col = bs.columns[0]

    def get(label: str) -> float | None:
        row = _row(bs, label)
        if row is None:
            return None
        value = row.get(col)
        return None if pd.isna(value) else float(value)

    total_debt = get("Total Debt")
    equity = get("Stockholders Equity")
    current_assets = get("Current Assets")
    current_liabilities = get("Current Liabilities")
    cash = get("Cash And Cash Equivalents") or get("Cash Cash Equivalents And Short Term Investments")

    debt_to_equity = None
    if total_debt is not None and equity:
        debt_to_equity = total_debt / equity

    current_ratio = None
    if current_assets is not None and current_liabilities:
        current_ratio = current_assets / current_liabilities

    return {
        "as_of": col.date().isoformat(),
        "total_debt": total_debt,
        "stockholders_equity": equity,
        "cash": cash,
        "debt_to_equity": debt_to_equity,
        "current_ratio": current_ratio,
    }


def get_cash_flow_summary(ticker: str) -> dict[str, Any] | None:
    """Annual operating cash flow / capex / FCF (oldest to newest) plus a TTM row."""
    try:
        t = yf.Ticker(ticker)
        annual = t.cashflow
        quarterly = t.quarterly_cashflow
    except Exception:
        return None

    if annual is None or annual.empty:
        return None

    ocf = _row(annual, "Operating Cash Flow")
    capex = _row(annual, "Capital Expenditure")
    if ocf is None:
        return None

    periods = []
    for col in list(annual.columns)[:_MAX_ANNUAL_PERIODS]:
        ocf_val = ocf.get(col)
        if pd.isna(ocf_val):
            continue
        capex_val = capex.get(col) if capex is not None else None
        fcf = (ocf_val + capex_val) if capex_val is not None and not pd.isna(capex_val) else None
        periods.append(
            {
                "fiscal_year_end": col.date().isoformat(),
                "operating_cash_flow": float(ocf_val),
                "capital_expenditure": float(capex_val) if capex_val is not None and not pd.isna(capex_val) else None,
                "free_cash_flow": fcf,
            }
        )
    periods.reverse()  # yfinance columns are newest-first; display oldest-first

    ttm_ocf = _sum_last_n_quarters(quarterly, "Operating Cash Flow")
    ttm_capex = _sum_last_n_quarters(quarterly, "Capital Expenditure")
    ttm = None
    if ttm_ocf is not None:
        ttm = {
            "operating_cash_flow": ttm_ocf,
            "capital_expenditure": ttm_capex,
            "free_cash_flow": (ttm_ocf + ttm_capex) if ttm_capex is not None else None,
        }

    return {"annual_periods": periods, "ttm": ttm}


def get_valuation_snapshot(ticker: str) -> dict[str, Any] | None:
    """Current multiples and company profile from yfinance's `.info`."""
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return None

    if not info:
        return None

    return {
        "current_price": info.get("currentPrice"),
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "price_to_sales": info.get("priceToSalesTrailing12Months"),
        "price_to_book": info.get("priceToBook"),
        "ev_to_ebitda": info.get("enterpriseToEbitda"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "employees": info.get("fullTimeEmployees"),
        "insider_ownership_pct": (info.get("heldPercentInsiders") or 0) * 100
        if info.get("heldPercentInsiders") is not None
        else None,
        "institutional_ownership_pct": (info.get("heldPercentInstitutions") or 0) * 100
        if info.get("heldPercentInstitutions") is not None
        else None,
        "business_summary": info.get("longBusinessSummary"),
    }


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "not available"
    abs_value = abs(value)
    if abs_value >= 1e9:
        return f"${value / 1e9:,.2f}B"
    if abs_value >= 1e6:
        return f"${value / 1e6:,.2f}M"
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    return "not available" if value is None else f"{value:.1f}%"


def _fmt_ratio(value: float | None) -> str:
    return "not available" if value is None else f"{value:.2f}"


def build_fundamentals_snapshot(ticker: str) -> str:
    """Render every fetchable fundamental into one markdown block for the prompt.

    Missing data is stated explicitly ("not available") rather than omitted,
    so the model never has to guess whether a gap means zero.
    """
    income = get_income_statement_summary(ticker)
    balance_sheet = get_balance_sheet_summary(ticker)
    cash_flow = get_cash_flow_summary(ticker)
    valuation = get_valuation_snapshot(ticker)

    lines: list[str] = []

    lines.append(f"### {ticker} — Valuation Snapshot")
    if valuation:
        lines.append(f"- Current price: {_fmt_money(valuation['current_price'])}")
        lines.append(f"- Market cap: {_fmt_money(valuation['market_cap'])}")
        lines.append(f"- Trailing P/E: {_fmt_ratio(valuation['trailing_pe'])}")
        lines.append(f"- Forward P/E: {_fmt_ratio(valuation['forward_pe'])}")
        lines.append(f"- P/S (TTM): {_fmt_ratio(valuation['price_to_sales'])}")
        lines.append(f"- P/B: {_fmt_ratio(valuation['price_to_book'])}")
        lines.append(f"- EV/EBITDA: {_fmt_ratio(valuation['ev_to_ebitda'])}")
        lines.append(f"- Sector / Industry: {valuation['sector'] or 'not available'} / {valuation['industry'] or 'not available'}")
        lines.append(f"- Employees: {valuation['employees'] or 'not available'}")
        lines.append(f"- Insider ownership: {_fmt_pct(valuation['insider_ownership_pct'])}")
        lines.append(f"- Institutional ownership: {_fmt_pct(valuation['institutional_ownership_pct'])}")
    else:
        lines.append("Not available.")

    lines.append("\n### Income Statement (annual, oldest to newest)")
    if income and income["annual_periods"]:
        for period in income["annual_periods"]:
            lines.append(
                f"- FY ending {period['fiscal_year_end']}: revenue {_fmt_money(period['revenue'])} "
                f"(growth {_fmt_pct(period['revenue_growth_pct'])}), "
                f"gross margin {_fmt_pct(period['gross_margin_pct'])}, "
                f"operating margin {_fmt_pct(period['operating_margin_pct'])}, "
                f"net margin {_fmt_pct(period['net_margin_pct'])}"
            )
        if income["ttm"]:
            ttm = income["ttm"]
            lines.append(
                f"- TTM: revenue {_fmt_money(ttm['revenue'])}, "
                f"gross margin {_fmt_pct(ttm['gross_margin_pct'])}, "
                f"operating margin {_fmt_pct(ttm['operating_margin_pct'])}, "
                f"net margin {_fmt_pct(ttm['net_margin_pct'])}"
            )
        if len(income["annual_periods"]) < 5:
            lines.append(
                f"(Only {len(income['annual_periods'])} fiscal year(s) of annual data available from this source.)"
            )
    else:
        lines.append("Not available.")

    lines.append("\n### Balance Sheet")
    if balance_sheet:
        lines.append(f"As of {balance_sheet['as_of']}:")
        lines.append(f"- Total debt: {_fmt_money(balance_sheet['total_debt'])}")
        lines.append(f"- Stockholders' equity: {_fmt_money(balance_sheet['stockholders_equity'])}")
        lines.append(f"- Cash and equivalents: {_fmt_money(balance_sheet['cash'])}")
        lines.append(f"- Debt-to-equity: {_fmt_ratio(balance_sheet['debt_to_equity'])}")
        lines.append(f"- Current ratio: {_fmt_ratio(balance_sheet['current_ratio'])}")
    else:
        lines.append("Not available.")

    lines.append("\n### Cash Flow (annual, oldest to newest)")
    if cash_flow and cash_flow["annual_periods"]:
        for period in cash_flow["annual_periods"]:
            lines.append(
                f"- FY ending {period['fiscal_year_end']}: operating cash flow "
                f"{_fmt_money(period['operating_cash_flow'])}, capex {_fmt_money(period['capital_expenditure'])}, "
                f"free cash flow {_fmt_money(period['free_cash_flow'])}"
            )
        if cash_flow["ttm"]:
            ttm = cash_flow["ttm"]
            lines.append(
                f"- TTM: operating cash flow {_fmt_money(ttm['operating_cash_flow'])}, "
                f"capex {_fmt_money(ttm['capital_expenditure'])}, free cash flow {_fmt_money(ttm['free_cash_flow'])}"
            )
    else:
        lines.append("Not available.")

    if valuation and valuation.get("business_summary"):
        lines.append("\n### Business Summary (from data provider)")
        lines.append(valuation["business_summary"])

    return "\n".join(lines)
