"""Handler for the "watchlist_add" intent — e.g. "Watch Apple"."""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from app import db, ticker_resolver
from app.parser import ParsedMessage


async def handle_watchlist_add(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: ParsedMessage
) -> None:
    conn = context.bot_data["conn"]
    company_name = parsed.company_name or ""

    resolved = await asyncio.to_thread(ticker_resolver.resolve_ticker, company_name)
    if resolved is None:
        await update.message.reply_text(
            f'Couldn\'t find a ticker for "{company_name}". '
            "Try sending the exact ticker symbol instead (e.g. \"Watch AAPL\")."
        )
        return

    ticker, canonical_name = resolved
    db.add_watchlist_item(conn, ticker, canonical_name)
    await update.message.reply_text(f"✅ Added {canonical_name} ({ticker}) to your watchlist.")
