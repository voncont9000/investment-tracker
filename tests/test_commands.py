"""End-to-end (minus the network) test of the dispatch path: free text ->
parser -> COMMAND_HANDLERS registry -> command handler -> DB write + reply.

Telegram's Update/Context objects are stubbed rather than constructed for
real, since that requires no live bot token or network access.
"""

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import db, handlers


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)
    yield connection
    connection.close()


def make_update_and_context(conn, text: str, chat_id: int = 1):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.text = text
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {
        "conn": conn,
        "settings": MagicMock(telegram_chat_id=chat_id),
    }
    return update, context


def run(coro):
    return asyncio.run(coro)


def test_watch_message_adds_to_watchlist_and_confirms(conn):
    update, context = make_update_and_context(conn, "Watch Apple")

    with patch("app.commands.watchlist.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")):
        run(handlers.handle_text(update, context))

    rows = db.list_watchlist(conn)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    update.message.reply_text.assert_awaited_once()
    assert "AAPL" in update.message.reply_text.call_args[0][0]


def test_purchase_message_adds_holding_and_confirms(conn):
    update, context = make_update_and_context(conn, "Bought Tesla for $200")

    with patch("app.commands.purchase.ticker_resolver.resolve_ticker", return_value=("TSLA", "Tesla Inc.")):
        run(handlers.handle_text(update, context))

    rows = db.list_holdings(conn)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "TSLA"
    assert rows[0]["purchase_price"] == 200.0
    update.message.reply_text.assert_awaited_once()
    assert "TSLA" in update.message.reply_text.call_args[0][0]


def test_unresolvable_ticker_replies_without_writing(conn):
    update, context = make_update_and_context(conn, "Watch Zzyzxcorp")

    with patch("app.commands.watchlist.ticker_resolver.resolve_ticker", return_value=None):
        run(handlers.handle_text(update, context))

    assert db.list_watchlist(conn) == []
    update.message.reply_text.assert_awaited_once()
    assert "Couldn't find" in update.message.reply_text.call_args[0][0]


def test_unknown_message_replies_with_help(conn):
    update, context = make_update_and_context(conn, "what's the weather")

    run(handlers.handle_text(update, context))

    assert db.list_watchlist(conn) == []
    assert db.list_holdings(conn) == []
    update.message.reply_text.assert_awaited_once()
    assert "didn't understand" in update.message.reply_text.call_args[0][0]


def test_non_owner_chat_is_ignored(conn):
    update, context = make_update_and_context(conn, "Watch Apple", chat_id=999)
    context.bot_data["settings"].telegram_chat_id = 1  # different from update's chat_id

    run(handlers.handle_text(update, context))

    assert db.list_watchlist(conn) == []
    update.message.reply_text.assert_not_awaited()


def test_list_positions_shows_watchlist_and_holdings(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    update, context = make_update_and_context(conn, "/list")

    run(handlers.list_positions(update, context))

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "AAPL" in text
    assert "TSLA" in text


# --- Buying at the current market price ---

def test_bought_without_price_uses_market_price(conn):
    update, context = make_update_and_context(conn, "Bought Apple")

    with patch("app.commands.purchase.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")), \
         patch("app.commands.purchase.prices.get_current_price", return_value=313.33):
        run(handlers.handle_text(update, context))

    rows = db.list_holdings(conn)
    assert len(rows) == 1
    assert rows[0]["purchase_price"] == 313.33
    reply = update.message.reply_text.call_args[0][0]
    assert "313.33" in reply
    assert "current market price" in reply


def test_bought_with_explicit_price_does_not_fetch(conn):
    update, context = make_update_and_context(conn, "Bought Apple for $150")

    with patch("app.commands.purchase.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")), \
         patch("app.commands.purchase.prices.get_current_price") as mock_price:
        run(handlers.handle_text(update, context))

    mock_price.assert_not_called()
    assert db.list_holdings(conn)[0]["purchase_price"] == 150.0


def test_bought_without_price_handles_price_lookup_failure(conn):
    update, context = make_update_and_context(conn, "Bought Apple")

    with patch("app.commands.purchase.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")), \
         patch("app.commands.purchase.prices.get_current_price", return_value=None):
        run(handlers.handle_text(update, context))

    # Must not record a bogus 0.0 purchase.
    assert db.list_holdings(conn) == []
    assert "Couldn't fetch" in update.message.reply_text.call_args[0][0]


# --- Selling ---

def test_sold_closes_holding_and_reports_gain(conn):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    update, context = make_update_and_context(conn, "Sold Apple")

    with patch("app.commands.sell.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")), \
         patch("app.commands.sell.prices.get_current_price", return_value=120.0):
        run(handlers.handle_text(update, context))

    assert db.get_active_holdings_for_ticker(conn, "AAPL") == []
    reply = update.message.reply_text.call_args[0][0]
    assert "+20.0%" in reply
    assert "100.00" in reply and "120.00" in reply


def test_sold_reports_loss(conn):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    update, context = make_update_and_context(conn, "Sold Apple")

    with patch("app.commands.sell.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")), \
         patch("app.commands.sell.prices.get_current_price", return_value=75.0):
        run(handlers.handle_text(update, context))

    reply = update.message.reply_text.call_args[0][0]
    assert "-25.0%" in reply


def test_sold_averages_cost_across_multiple_lots(conn):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    db.add_holding(conn, "AAPL", "Apple Inc.", 200.0)  # avg cost = 150
    update, context = make_update_and_context(conn, "Sold Apple")

    with patch("app.commands.sell.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")), \
         patch("app.commands.sell.prices.get_current_price", return_value=180.0):
        run(handlers.handle_text(update, context))

    reply = update.message.reply_text.call_args[0][0]
    assert "150.00" in reply       # averaged cost basis
    assert "+20.0%" in reply       # (180 - 150) / 150
    assert "2 lots" in reply
    assert db.get_active_holdings_for_ticker(conn, "AAPL") == []


def test_sold_clears_holding_gain_alert_state(conn):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    db.set_in_alert(conn, "AAPL", "holding_gain", True)
    update, context = make_update_and_context(conn, "Sold Apple")

    with patch("app.commands.sell.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")), \
         patch("app.commands.sell.prices.get_current_price", return_value=120.0):
        run(handlers.handle_text(update, context))

    assert db.get_alert_state(conn, "AAPL", "holding_gain") is None


def test_sold_something_not_held(conn):
    update, context = make_update_and_context(conn, "Sold Apple")

    with patch("app.commands.sell.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")), \
         patch("app.commands.sell.prices.get_current_price", return_value=120.0):
        run(handlers.handle_text(update, context))

    assert "don't currently hold" in update.message.reply_text.call_args[0][0]


# --- Removing ---

def test_remove_drops_from_watchlist(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    update, context = make_update_and_context(conn, "Remove Apple")

    with patch("app.commands.remove.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")):
        run(handlers.handle_text(update, context))

    assert db.list_watchlist(conn) == []
    assert "Removed" in update.message.reply_text.call_args[0][0]


def test_remove_clears_watchlist_alert_state(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    db.set_in_alert(conn, "AAPL", "watchlist_drop", True)
    update, context = make_update_and_context(conn, "Remove Apple")

    with patch("app.commands.remove.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")):
        run(handlers.handle_text(update, context))

    # Stale state would otherwise suppress the first alert after re-adding.
    assert db.get_alert_state(conn, "AAPL", "watchlist_drop") is None


def test_remove_points_at_sell_when_only_held(conn):
    db.add_holding(conn, "AAPL", "Apple Inc.", 150.0)
    update, context = make_update_and_context(conn, "Remove Apple")

    with patch("app.commands.remove.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")):
        run(handlers.handle_text(update, context))

    reply = update.message.reply_text.call_args[0][0]
    assert "Sold AAPL" in reply
    # The holding must be left intact — selling is a separate, explicit action.
    assert len(db.get_active_holdings_for_ticker(conn, "AAPL")) == 1


def test_remove_when_not_tracked_at_all(conn):
    update, context = make_update_and_context(conn, "Remove Apple")

    with patch("app.commands.remove.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")):
        run(handlers.handle_text(update, context))

    assert "isn't on your watchlist" in update.message.reply_text.call_args[0][0]
