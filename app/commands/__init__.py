"""Intent -> handler registry.

This is the single seam a new command plugs into: add a pattern in
parser.py, a handler module here, and one line below. handlers.py never
needs a new branch.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from app.commands import purchase, remove, sell, watchlist
from app.parser import ParsedMessage

CommandFn = Callable[[Update, ContextTypes.DEFAULT_TYPE, ParsedMessage], Awaitable[None]]

COMMAND_HANDLERS: dict[str, CommandFn] = {
    "watchlist_add": watchlist.handle_watchlist_add,
    "purchase_record": purchase.handle_purchase_record,
    "sell_record": sell.handle_sell_record,
    "remove_item": remove.handle_remove_item,
}
