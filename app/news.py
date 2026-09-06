"""Exa-based news retrieval for the weekly sell-thesis sweep (app/thesis.py).

Deterministic search-by-ticker-and-date, done in plain Python rather than
as an agentic Claude tool loop (the "Analyse" command's web_search) — the
per-search price is nearly the same either way, but an agentic loop resends
the growing conversation on every turn, which is the real cost driver (see
docs/superpowers/specs/2026-09-05-sell-alerts-design.md, "Why Exa"). One
search here, one Claude call in app/thesis.py, nothing accumulates.
"""

from __future__ import annotations

from dataclasses import dataclass

from exa_py import Exa

NUM_RESULTS = 5

# Caps a single article's text, same spirit as app/analysis.py's
# WEB_FETCH_MAX_CONTENT_TOKENS — one long page shouldn't dominate the
# prompt sent to Claude.
MAX_TEXT_CHARS = 2000


@dataclass
class NewsItem:
    title: str
    url: str
    published_date: str | None
    text: str


def fetch_recent_news(api_key: str, ticker: str, company_name: str, since_iso: str) -> list[NewsItem]:
    """One search for recent news about `company_name` (ticker), published
    on or after `since_iso` (ISO 8601). Returns [] on any error — a thesis
    check that can't retrieve news should be skipped for this week, not
    crash the sweep for every other holding."""
    try:
        client = Exa(api_key=api_key)
        response = client.search_and_contents(
            f"{company_name} ({ticker}) stock news",
            num_results=NUM_RESULTS,
            start_published_date=since_iso,
            text=True,
        )
        return [
            NewsItem(
                title=result.title or "",
                url=result.url,
                published_date=result.published_date,
                text=(result.text or "")[:MAX_TEXT_CHARS],
            )
            for result in response.results
        ]
    except Exception:
        return []
