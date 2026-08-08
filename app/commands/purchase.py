"""Handler for the "purchase_record" intent.

Two forms:
  "Bought Apple for $150" -> explicit price (useful for backdating a purchase)
  "Bought Apple"          -> price looked up at the current market price
"""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from app import db, prices, ticker_resolver
from app.parser import ParsedMessage


async def handle_purchase_record(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: ParsedMessage
) -> None:
    conn = context.bot_data["conn"]
    company_name = parsed.company_name or ""

    resolved = await asyncio.to_thread(ticker_resolver.resolve_ticker, company_name)
    if resolved is None:
        await update.message.reply_text(
            f'Couldn\'t find a ticker for "{company_name}". '
            'Try the exact ticker symbol instead (e.g. "Bought AAPL").'
        )
        return

    ticker, canonical_name = resolved
    price = parsed.amount
    price_source = "at the price you gave"

    if price is None:
        price = await asyncio.to_thread(prices.get_current_price, ticker)
        if price is None:
            await update.message.reply_text(
                f"Couldn't fetch a current price for {canonical_name} ({ticker}). "
                f'Try again, or set it explicitly: "Bought {ticker} for $150".'
            )
            return
        price_source = "at the current market price"

    db.add_holding(conn, ticker, canonical_name, price)
    await update.message.reply_text(
        f"✅ Recorded: bought {canonical_name} ({ticker}) "
        f"at ${price:,.2f}/share — {price_source}."
    )
