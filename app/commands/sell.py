"""Handler for the "sell_record" intent — e.g. "Sold Apple".

Closes every open lot of the ticker at the current market price and reports
profit/loss against the average cost basis.

Note on units: the schema tracks a purchase price per lot but no share
quantity, so profit/loss here is strictly *per share* — a percentage and a
per-share dollar delta, never an absolute portfolio total. The reply wording
says so explicitly rather than implying a total gain.
"""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from app import db, prices, ticker_resolver
from app.parser import ParsedMessage


async def handle_sell_record(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: ParsedMessage
) -> None:
    conn = context.bot_data["conn"]
    company_name = parsed.company_name or ""

    resolved = await asyncio.to_thread(ticker_resolver.resolve_ticker, company_name)
    if resolved is None:
        await update.message.reply_text(
            f'Couldn\'t find a ticker for "{company_name}". '
            'Try the exact ticker symbol instead (e.g. "Sold AAPL").'
        )
        return

    ticker, canonical_name = resolved

    lots = db.get_active_holdings_for_ticker(conn, ticker)
    if not lots:
        await update.message.reply_text(
            f"You don't currently hold {canonical_name} ({ticker}), so there's nothing to sell."
        )
        return

    sell_price = await asyncio.to_thread(prices.get_current_price, ticker)
    if sell_price is None:
        await update.message.reply_text(
            f"Couldn't fetch a current price for {canonical_name} ({ticker}). "
            "Try again in a moment."
        )
        return

    avg_cost = sum(lot["purchase_price"] for lot in lots) / len(lots)
    db.sell_holdings(conn, ticker, sell_price)
    db.clear_alert_state(conn, ticker, "holding_gain")

    delta = sell_price - avg_cost
    pct = (delta / avg_cost * 100) if avg_cost else 0.0
    emoji = "📈" if delta >= 0 else "📉"
    lot_note = f" across {len(lots)} lots" if len(lots) > 1 else ""

    await update.message.reply_text(
        f"✅ Sold {canonical_name} ({ticker}){lot_note}.\n\n"
        f"{emoji} Cost basis: ${avg_cost:,.2f}/share\n"
        f"    Sold at:    ${sell_price:,.2f}/share\n"
        f"    Change:     {delta:+,.2f}/share ({pct:+.1f}%)"
    )
