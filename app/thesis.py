"""Weekly sell-thesis sweep.

A free gate (yfinance headlines + the cached daily-bar move + a 28-day
floor) decides which holdings are worth spending anything on this week.
For each gated-in ticker, app.news does one deterministic Exa search for
recent coverage, and one Claude call (no tools) turns that into a
HOLD/CONCERN verdict via structured output. Only CONCERN sends a message,
and only on the transition into it — the same episode dedup as every
other alert (app.db.alert_state, type "thesis_break").

See docs/superpowers/specs/2026-09-05-sell-alerts-design.md for the full
design and the reasoning behind each choice below.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Literal

import anthropic
from pydantic import BaseModel

from app import alerts, db, news, prices, technicals

logger = logging.getLogger(__name__)

SendFn = Callable[[str], Awaitable[None]]

# Sonnet 5 at low effort, no tools: the retrieval (app.news) is already
# done deterministically in Python, so the model's only job is judging a
# fixed set of snippets — a couple of sentences of reasoning, not agentic
# search. See the design doc's "Why Exa" section for the cost comparison
# against Claude's own web_search.
MODEL = "claude-sonnet-5"
MAX_TOKENS = 2000
EFFORT = "low"

# The free gate: check a holding this week if any of these hold, so a
# quiet holding costs nothing.
GATE_MOVE_THRESHOLD = 0.05   # a >=5% move over the trailing 5 sessions
GATE_FLOOR_DAYS = 28         # ...or it's simply been this long since the last check


class ThesisVerdict(BaseModel):
    verdict: Literal["HOLD", "CONCERN"]
    reason: str
    sources: list[str]


PROMPT_TEMPLATE = """\
You are screening a stock I already hold, checking whether recent news has \
weakened the reason to keep holding it. This is a quick weekly screen, not \
a full buy/sell analysis.

Ticker: {ticker} ({company_name})
Purchased: {purchase_date}
Average cost: ${avg_cost:,.2f}/share
Current price: ${current_price:,.2f}/share ({change_pct:+.1f}% since purchase)

Recent news:
{news_block}

Based only on the news above, has anything happened that materially \
weakens the reason to hold this stock — e.g. a guidance cut, a missed \
earnings report, a lost contract, a leadership departure, or a \
regulatory/legal problem? Ordinary market noise, routine coverage, and \
analyst price-target tweaks are not concerns. If nothing above rises to \
that bar, or there's no relevant news, say HOLD. Give a 2-3 sentence \
reason either way, and list the source URLs you relied on (an empty list \
if none).
"""


def _format_news_block(items: list[news.NewsItem]) -> str:
    if not items:
        return "(none found)"
    return "\n\n".join(
        f"- {item.title} ({item.published_date or 'date unknown'})\n  {item.url}\n  {item.text}"
        for item in items
    )


def _get_verdict(
    api_key: str,
    ticker: str,
    company_name: str,
    purchase_date: str,
    avg_cost: float,
    current_price: float,
    news_items: list[news.NewsItem],
) -> ThesisVerdict:
    """Blocking (synchronous Anthropic call) — callers on an asyncio event
    loop must wrap this in asyncio.to_thread(), the same as app.analysis's
    run_analysis."""
    client = anthropic.Anthropic(api_key=api_key)
    change_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost else 0.0
    prompt = PROMPT_TEMPLATE.format(
        ticker=ticker,
        company_name=company_name,
        purchase_date=purchase_date,
        avg_cost=avg_cost,
        current_price=current_price,
        change_pct=change_pct,
        news_block=_format_news_block(news_items),
    )
    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": EFFORT},
        output_format=ThesisVerdict,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].parsed_output


def _should_check(state, metrics: technicals.TickerMetrics, latest_headline_at: str | None) -> bool:
    """The free gate. `state` is a thesis_check_state row (or None if this
    ticker has never been checked) — a mapping with last_checked_at /
    last_headline_at, so both a sqlite3.Row and a plain dict work here."""
    if state is None:
        return True

    last_checked_at = datetime.fromisoformat(state["last_checked_at"])
    if datetime.now(timezone.utc) - last_checked_at >= timedelta(days=GATE_FLOOR_DAYS):
        return True

    if abs(technicals.return_n(metrics, 5)) >= GATE_MOVE_THRESHOLD:
        return True

    last_headline_at = state["last_headline_at"]
    if latest_headline_at is not None and (last_headline_at is None or latest_headline_at > last_headline_at):
        return True

    return False


async def _handle_verdict(conn, send: SendFn, ticker: str, verdict: ThesisVerdict) -> None:
    """Same "once per episode" dedup as every other alert (alert_type
    "thesis_break") — a CONCERN sends only on the transition into it, and
    clears silently (no message) the week it returns to HOLD."""
    state = db.get_alert_state(conn, ticker, "thesis_break")
    currently_in_alert = bool(state["in_alert"]) if state is not None else False
    concern = verdict.verdict == "CONCERN"

    if concern and not currently_in_alert:
        sources = "\n".join(f"- {s}" for s in verdict.sources) if verdict.sources else "(no sources cited)"
        await send(f"⚠️ {ticker} — thesis check\n\n{verdict.reason}\n\n{sources}")
        db.set_in_alert(conn, ticker, "thesis_break", True)
    elif not concern and currently_in_alert:
        db.set_in_alert(conn, ticker, "thesis_break", False)


async def run_weekly_sweep(conn, settings, send: SendFn) -> None:
    """Entry point for the weekly job (bot.py). Does nothing if either
    required key is missing — every other command, including the other
    sell alerts, works with no key at all."""
    if not settings.anthropic_api_key or not settings.exa_api_key:
        return

    avg_costs = db.avg_cost_by_ticker(conn)
    for ticker in db.list_distinct_holding_tickers(conn):
        metrics = await asyncio.to_thread(alerts.build_ticker_metrics, ticker)
        if metrics is None:
            continue

        latest_headline_at = await asyncio.to_thread(prices.fetch_latest_headline_time, ticker)
        state = db.get_thesis_check_state(conn, ticker)
        if not _should_check(state, metrics, latest_headline_at):
            continue

        lots = db.get_active_holdings_for_ticker(conn, ticker)
        company_name = lots[0]["company_name"]
        purchase_date = lots[0]["purchase_date"]
        since_iso = state["last_checked_at"] if state is not None else db.utcnow_iso()

        news_items = await asyncio.to_thread(
            news.fetch_recent_news, settings.exa_api_key, ticker, company_name, since_iso
        )

        try:
            verdict = await asyncio.to_thread(
                _get_verdict,
                settings.anthropic_api_key,
                ticker,
                company_name,
                purchase_date,
                avg_costs[ticker],
                metrics.current_price,
                news_items,
            )
        except Exception:
            # Leave thesis_check_state untouched so this ticker is retried
            # rather than silently marked "checked" for a week — a failed
            # verdict is not a HOLD.
            logger.exception("Thesis check failed for %s", ticker)
            continue

        new_headline_at = latest_headline_at if latest_headline_at is not None else (
            state["last_headline_at"] if state is not None else None
        )
        db.set_thesis_check_state(conn, ticker, db.utcnow_iso(), new_headline_at)
        await _handle_verdict(conn, send, ticker, verdict)
