"""Company name -> (ticker, canonical_name) resolution.

Tries the Yahoo Finance public search endpoint first (handles "Apple" ->
AAPL for essentially any listed company), then falls back to a small
bundled CSV + fuzzy matching if the network call fails, is rate-limited, or
finds nothing. Returns None if neither source can resolve the name, so
callers can ask the user to clarify or send the exact ticker.
"""

from __future__ import annotations

import csv
import os

import requests
from rapidfuzz import fuzz, process

_STATIC_TICKERS_PATH = os.path.join(os.path.dirname(__file__), "static_tickers.csv")
_FUZZY_SCORE_CUTOFF = 80

_static_tickers_cache: dict[str, str] | None = None


def _load_static_tickers() -> dict[str, str]:
    mapping: dict[str, str] = {}
    if os.path.exists(_STATIC_TICKERS_PATH):
        with open(_STATIC_TICKERS_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = row.get("name")
                ticker = row.get("ticker")
                if name and ticker:
                    mapping[name] = ticker
    return mapping


def _static_tickers() -> dict[str, str]:
    global _static_tickers_cache
    if _static_tickers_cache is None:
        _static_tickers_cache = _load_static_tickers()
    return _static_tickers_cache


def _search_yahoo(company_name: str) -> tuple[str, str] | None:
    try:
        response = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": company_name, "quotesCount": 5, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None

    for quote in data.get("quotes", []):
        if quote.get("quoteType") == "EQUITY":
            ticker = quote.get("symbol")
            name = quote.get("shortname") or quote.get("longname") or ticker
            if ticker:
                return ticker, name
    return None


def _search_static(company_name: str) -> tuple[str, str] | None:
    tickers = _static_tickers()
    if not tickers:
        return None
    match = process.extractOne(
        company_name, tickers.keys(), scorer=fuzz.WRatio, score_cutoff=_FUZZY_SCORE_CUTOFF
    )
    if match is None:
        return None
    matched_name = match[0]
    return tickers[matched_name], matched_name


def resolve_ticker(company_name: str) -> tuple[str, str] | None:
    company_name = company_name.strip()
    if not company_name:
        return None

    result = _search_yahoo(company_name)
    if result is not None:
        return result

    return _search_static(company_name)
