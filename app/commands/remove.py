"""Handler for the "remove_item" intent — e.g. "Remove Apple".

Removal targets the watchlist. If the stock isn't watched but *is* held,
the reply points at "Sold X" rather than silently doing nothing — removing a
holding is a sale, and should go through the path that records the price and
reports profit/loss.
"""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from app import db, ticker_resolver
from app.parser import ParsedMessage


async def handle_remove_item(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: ParsedMessage
) -> None:
    conn = context.bot_data["conn"]
    company_name = parsed.company_name or ""

    resolved = await asyncio.to_thread(ticker_resolver.resolve_ticker, company_name)
    if resolved is None:
        await update.message.reply_text(
            f'Couldn\'t find a ticker for "{company_name}". '
            'Try the exact ticker symbol instead (e.g. "Remove AAPL").'
        )
        return

    ticker, canonical_name = resolved

    if db.remove_watchlist_item(conn, ticker):
        db.clear_alert_state(conn, ticker, "watchlist_drop")
        await update.message.reply_text(
            f"✅ Removed {canonical_name} ({ticker}) from your watchlist."
        )
        return

    if db.get_active_holdings_for_ticker(conn, ticker):
        await update.message.reply_text(
            f"{canonical_name} ({ticker}) isn't on your watchlist, but you do own it.\n\n"
            f'To record a sale (with profit/loss), send: "Sold {ticker}"'
        )
        return

    await update.message.reply_text(
        f"{canonical_name} ({ticker}) isn't on your watchlist."
    )
