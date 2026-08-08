"""Handler for the "select_picks" intent — your reply to the morning
politician-trades digest ("1 3", "all", "skip") saying which picks to run
the full Analyse-style report on.

Reuses app.analysis / app.fundamentals exactly as app/commands/analyze.py
does — no changes to that pipeline, just pointed at the tickers from the
digest. Same "ack now, do the real work in a background task" shape as
analyze.py, for the same reason: the Claude call takes 1-3 minutes and
must not block the price-poll job or other commands.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from app import analysis, db, fundamentals
from app.parser import ParsedMessage

logger = logging.getLogger(__name__)

REPORTS_DIR = os.path.join("reports", "politician-trades")

# Telegram message cap is 4096 characters; leave headroom for the header we
# prepend to the quick take.
_MAX_QUICK_TAKE_CHARS = 3900


async def handle_select_picks(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: ParsedMessage
) -> None:
    conn = context.bot_data["conn"]
    pending = db.get_pending_picks(conn, date.today().isoformat())

    if not pending:
        await update.message.reply_text("No pending picks to select from today.")
        return

    if parsed.selection == "skip":
        for row in pending:
            db.set_pick_status(conn, row["id"], "skipped")
        await update.message.reply_text("Skipped today's picks.")
        return

    selected = _resolve_selection(parsed.selection, pending)
    if not selected:
        await update.message.reply_text(
            f"Those numbers don't match today's picks (1-{len(pending)}). "
            'Reply with the numbers you want (e.g. "1 3"), "all", or "skip".'
        )
        return

    settings = context.bot_data["settings"]
    if not settings.anthropic_api_key:
        await update.message.reply_text(
            "Analysis needs an Anthropic API key. Set ANTHROPIC_API_KEY in .env "
            "and restart the bot to use this."
        )
        return

    for row in selected:
        db.set_pick_status(conn, row["id"], "selected")

    await update.message.reply_text(
        f"🔎 Running full analysis on {len(selected)} pick(s)... this can take a "
        "couple of minutes each."
    )
    for row in selected:
        asyncio.create_task(
            _generate_and_send_report(update, context, row, settings.anthropic_api_key)
        )


def _resolve_selection(selection, pending) -> list:
    if selection == "all":
        return list(pending)
    if isinstance(selection, list):
        by_rank = {row["rank"]: row for row in pending}
        return [by_rank[n] for n in selection if n in by_rank]
    return []


async def _generate_and_send_report(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pick_row, api_key: str
) -> None:
    ticker = pick_row["ticker"]
    canonical_name = pick_row["company_name"]
    conn = context.bot_data["conn"]

    try:
        snapshot = await asyncio.to_thread(fundamentals.build_fundamentals_snapshot, ticker)
        report_text = await asyncio.to_thread(
            analysis.run_analysis, ticker, canonical_name, snapshot, api_key
        )
    except Exception:
        logger.exception("Analysis failed for pick %s (%s)", canonical_name, ticker)
        await update.message.reply_text(
            f"Sorry, the analysis for {canonical_name} ({ticker}) failed. Try again in a moment."
        )
        return

    quick_take, full_report = analysis.split_report(report_text)
    if len(quick_take) > _MAX_QUICK_TAKE_CHARS:
        quick_take = quick_take[:_MAX_QUICK_TAKE_CHARS].rstrip() + "…"

    report_path = _save_report(ticker, full_report)
    db.set_pick_status(conn, pick_row["id"], "selected", report_path=report_path)

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
