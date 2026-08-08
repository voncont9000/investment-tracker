"""Orchestrates the morning politician-trades digest: fetch filings from
every configured source, extract structured trades from the new ones, score
and rank purchases, persist the shortlist, and message it to Telegram.

No Opus call happens here — this is the free/cheap screening stage. The
Analyse-style deep dive only runs once you reply with a selection (see
app/commands/picks.py). `send` is injected the same way app/alerts.py does
it, so this is fully testable with a stub — no Telegram involved.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Awaitable, Callable

from app import db, ticker_resolver
from app.politician_trades import pdf_extract, scoring
from app.politician_trades.sources import house_clerk, senate, whitehouse
from app.politician_trades.sources.base import RawFiling

logger = logging.getLogger(__name__)

SendFn = Callable[[str], Awaitable[None]]

# Generous lookback so a late job run or a filing that lands right around
# midnight isn't missed — processed_filings dedupes, so nothing already
# shown is ever double-counted or re-sent.
_LOOKBACK_DAYS = 2
_TOP_N = 3


async def run_daily_digest(conn, settings, send: SendFn) -> None:
    since = date.today() - timedelta(days=_LOOKBACK_DAYS)

    filings = await asyncio.to_thread(_fetch_all_filings, since)
    new_filings = [f for f in filings if not db.is_filing_processed(conn, f.doc_id)]

    trades: list[dict] = []
    for filing in new_filings:
        filing_trades = await asyncio.to_thread(
            _extract_and_resolve, filing, settings.anthropic_api_key
        )
        trades.extend(filing_trades)
        db.mark_filing_processed(conn, filing.doc_id, filing.chamber)

    ranked = scoring.rank_purchases(trades)
    top = scoring.top_n_picks(ranked, n=_TOP_N)

    pick_date = date.today().isoformat()
    if not top:
        await send(_quiet_day_message(len(new_filings)))
        return

    picks = [
        {
            "rank": i + 1,
            "ticker": t["ticker"],
            "company_name": t.get("company_name", t["ticker"]),
            "politician_name": t["politician_name"],
            "chamber": t["chamber"],
            "transaction_type": t["transaction_type"],
            "amount_band": t.get("amount_band"),
            "trade_date": t.get("trade_date"),
            "filed_date": t.get("filed_date"),
            "score": t["score"],
            "politician_count": t.get("politician_count", 1),
        }
        for i, t in enumerate(top)
    ]

    db.save_daily_picks(conn, pick_date, picks)
    await send(_format_digest_message(picks, len(new_filings)))


def _fetch_all_filings(since: date) -> list[RawFiling]:
    filings: list[RawFiling] = []
    for source in (house_clerk, senate, whitehouse):
        try:
            filings.extend(source.fetch_recent_filings(since))
        except Exception:
            logger.exception("Trade source %s failed", source.__name__)
    return filings


def _extract_and_resolve(filing: RawFiling, api_key: str | None) -> list[dict]:
    if not api_key:
        return []
    raw_trades = pdf_extract.extract_trades(filing.pdf_url, api_key)
    resolved = []
    for raw in raw_trades:
        result = ticker_resolver.resolve_ticker(raw.get("asset_description", ""))
        if result is None:
            continue
        ticker, company_name = result
        resolved.append(
            {
                "ticker": ticker,
                "company_name": company_name,
                "politician_name": filing.politician_name,
                "chamber": filing.chamber,
                "transaction_type": raw.get("transaction_type"),
                "amount_band": raw.get("amount_band"),
                "trade_date": raw.get("trade_date"),
                "filed_date": filing.filed_date,
            }
        )
    return resolved


def _quiet_day_message(filings_checked: int) -> str:
    return (
        "🏛 No qualifying politician purchases filed in the last 24h "
        f"(checked {filings_checked} new filing(s))."
    )


def _format_digest_message(picks: list[dict], filings_checked: int) -> str:
    lines = [f"📋 Politician Trades — {date.today().isoformat()}", ""]
    lines.append(
        f"Filed recently: {len(picks)} qualifying purchase(s) "
        f"(of {filings_checked} new filing(s))"
    )
    lines.append("")
    for pick in picks:
        cluster_note = (
            f" · {pick['politician_count']} members bought this"
            if pick["politician_count"] > 1
            else ""
        )
        lines.append(f"{pick['rank']}. {pick['ticker']} — {pick['company_name']}")
        lines.append(
            f"   {pick['politician_name']} ({pick['chamber']}) · bought "
            f"{pick['amount_band'] or 'unknown amount'} · filed {pick['filed_date']} "
            f"(traded {pick['trade_date']}){cluster_note}"
        )
        lines.append(f"   Score: {pick['score']:.1f}")
        lines.append("")
    lines.append('Reply with the numbers you want full analysis on (e.g. "1 3"), "all", or "skip".')
    return "\n".join(lines)
