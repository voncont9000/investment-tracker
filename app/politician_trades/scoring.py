"""Deterministic scoring and ranking of disclosed politician trades.

No LLM call anywhere in this module — the whole point of screening in plain
Python is that it's free to run every morning. The one Anthropic call in this
feature is pdf_extract.py's PDF-to-structured-data step; everything after
that is arithmetic.

Only purchases are ranked (see the "purchases only" assumption in the design
doc) — a sale isn't a "recommended trade" the way this feature is framed.
Sales still flow through unscored so nothing is silently dropped by the
caller that fetches trades.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# STOCK Act periodic-transaction-report disclosures use these fixed dollar
# bands (the PTR form is literally a checkbox list of them) — mapping the
# band string to a fixed score is simpler and more reliable than trying to
# parse and midpoint a "$X - $Y" string ourselves.
_AMOUNT_BAND_SCORES: dict[str, float] = {
    "$1,001 - $15,000": 1.0,
    "$15,001 - $50,000": 2.0,
    "$50,001 - $100,000": 3.0,
    "$100,001 - $250,000": 4.0,
    "$250,001 - $500,000": 5.0,
    "$500,001 - $1,000,000": 6.0,
    "$1,000,001 - $5,000,000": 7.0,
    "$5,000,001 - $25,000,000": 8.0,
    "$25,000,001 - $50,000,000": 9.0,
    "Over $50,000,000": 10.0,
}

_CLUSTER_BONUS_PER_EXTRA_POLITICIAN = 2.0
_MAX_FRESHNESS_SCORE = 5.0
_FRESHNESS_HALF_LIFE_DAYS = 14  # score halves every this many days of lag


def amount_band_score(amount_band: str | None) -> float:
    if amount_band is None:
        return 0.0
    return _AMOUNT_BAND_SCORES.get(amount_band.strip(), 0.0)


def freshness_score(trade_date: str | None, filed_date: str | None) -> float:
    """Higher when the gap between the trade and its disclosure is small."""
    if not trade_date or not filed_date:
        return 0.0
    try:
        traded = date.fromisoformat(trade_date)
        filed = date.fromisoformat(filed_date)
    except ValueError:
        return 0.0
    lag_days = max((filed - traded).days, 0)
    return _MAX_FRESHNESS_SCORE * (0.5 ** (lag_days / _FRESHNESS_HALF_LIFE_DAYS))


def rank_purchases(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score and rank purchase trades, clustering multiple politicians'
    buys of the same ticker into one candidate.

    Each returned dict carries the winning (highest-scoring) trade's fields
    plus `score` and `politician_count`.
    """
    purchases = [t for t in trades if t.get("transaction_type") == "purchase"]
    if not purchases:
        return []

    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for trade in purchases:
        by_ticker.setdefault(trade["ticker"], []).append(trade)

    ranked: list[dict[str, Any]] = []
    for ticker, group in by_ticker.items():
        politicians = {t["politician_name"] for t in group}
        # Best individual trade in the cluster sets the displayed details.
        best = max(
            group,
            key=lambda t: amount_band_score(t.get("amount_band"))
            + freshness_score(t.get("trade_date"), t.get("filed_date")),
        )
        base_score = amount_band_score(best.get("amount_band")) + freshness_score(
            best.get("trade_date"), best.get("filed_date")
        )
        cluster_bonus = (len(politicians) - 1) * _CLUSTER_BONUS_PER_EXTRA_POLITICIAN
        candidate = dict(best)
        candidate["score"] = base_score + cluster_bonus
        candidate["politician_count"] = len(politicians)
        ranked.append(candidate)

    ranked.sort(key=lambda c: c["score"], reverse=True)
    return ranked


def top_n_picks(ranked: list[dict[str, Any]], n: int = 3) -> list[dict[str, Any]]:
    return ranked[:n]
