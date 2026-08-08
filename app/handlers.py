"""Telegram command + text-message handlers. Dispatch only — the actual
work for each intent lives in app/commands/.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app import db
from app.commands import COMMAND_HANDLERS
from app.parser import parse_message

HELP_TEXT = (
    "I track a watchlist and your holdings, and alert you on big moves.\n\n"
    "Watchlist:\n"
    "  Watch Apple\n"
    "  Remove Apple\n\n"
    "Holdings:\n"
    "  Bought Apple            (uses the current market price)\n"
    "  Bought Apple for $150   (sets the price yourself)\n"
    "  Sold Apple              (records the sale + profit/loss)\n\n"
    "Commands: /list"
)


def _is_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = context.bot_data["settings"]
    return (
        update.effective_chat is not None
        and update.effective_chat.id == settings.telegram_chat_id
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        return
    await update.message.reply_text(HELP_TEXT)


async def list_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        return
    conn = context.bot_data["conn"]
    watchlist_items = db.list_watchlist(conn)
    holdings = db.list_holdings(conn)

    lines = ["Watchlist:"]
    if watchlist_items:
        lines += [f"  {row['ticker']} — {row['company_name']}" for row in watchlist_items]
    else:
        lines.append("  (empty)")

    lines.append("")
    lines.append("Holdings:")
    if holdings:
        lines += [
            f"  {row['ticker']} — {row['company_name']} @ ${row['purchase_price']:.2f}"
            for row in holdings
        ]
    else:
        lines.append("  (empty)")

    await update.message.reply_text("\n".join(lines))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        return

    text = update.message.text or ""
    parsed = parse_message(text)

    handler = COMMAND_HANDLERS.get(parsed.intent)
    if handler is None:
        await update.message.reply_text("Sorry, I didn't understand that.\n\n" + HELP_TEXT)
        return

    await handler(update, context, parsed)
