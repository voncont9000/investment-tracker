"""Handler for the "analyze_company" intent — e.g. "Analyse Apple".

Runs a full equity research report via app.analysis (financials fetched
deterministically, competitor/moat/analyst research done by the model via
web search + web fetch). This takes 1-3 minutes, so the handler sends an
immediate acknowledgement and does the real work in a background task —
without that, this command would block the price-poll job and every other
command for as long as the report takes to generate.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from app import analysis, fundamentals, ticker_resolver
from app.parser import ParsedMessage

logger = logging.getLogger(__name__)

REPORTS_DIR = "reports"

# Telegram message cap is 4096 characters; leave headroom for the header we
# prepend to the quick take.
_MAX_QUICK_TAKE_CHARS = 3900


async def handle_analyze_company(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: ParsedMessage
) -> None:
    company_name = parsed.company_name or ""

    resolved = await asyncio.to_thread(ticker_resolver.resolve_ticker, company_name)
    if resolved is None:
        await update.message.reply_text(
            f'Couldn\'t find a ticker for "{company_name}". '
            'Try the exact ticker symbol instead (e.g. "Analyse AAPL").'
        )
        return

    ticker, canonical_name = resolved
    settings = context.bot_data["settings"]

    if not settings.anthropic_api_key:
        await update.message.reply_text(
            "Analysis needs an Anthropic API key. Set ANTHROPIC_API_KEY in .env "
            "and restart the bot to use this command."
        )
        return

    await update.message.reply_text(
        f"🔎 Analyzing {canonical_name} ({ticker})... this can take a couple of "
        "minutes — pulling financials and reading analyst coverage."
    )

    asyncio.create_task(
        _generate_and_send_report(update, context, ticker, canonical_name, settings.anthropic_api_key)
    )


async def _generate_and_send_report(
    update: Update, context: ContextTypes.DEFAULT_TYPE, ticker: str, canonical_name: str, api_key: str
) -> None:
    try:
        snapshot = await asyncio.to_thread(fundamentals.build_fundamentals_snapshot, ticker)
        report_text = await asyncio.to_thread(analysis.run_analysis, ticker, canonical_name, snapshot, api_key)
    except Exception:
        logger.exception("Analysis failed for %s (%s)", canonical_name, ticker)
        await update.message.reply_text(
            f"Sorry, the analysis for {canonical_name} ({ticker}) failed. Try again in a moment."
        )
        return

    quick_take, full_report = analysis.split_report(report_text)
    if len(quick_take) > _MAX_QUICK_TAKE_CHARS:
        quick_take = quick_take[:_MAX_QUICK_TAKE_CHARS].rstrip() + "…"

    report_path = _save_report(ticker, full_report)

    await update.message.reply_text(f"📊 {canonical_name} ({ticker})\n\n{quick_take}")

    with open(report_path, "rb") as report_file:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=report_file,
            filename=os.path.basename(report_path),
        )


def _save_report(ticker: str, full_report: str) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"{ticker}-{date.today().isoformat()}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(full_report)
    return path
