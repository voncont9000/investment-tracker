"""app/commands/picks.py — the "select_picks" reply handler. Dispatched the
same way as tests/test_commands.py: free text -> parser -> handlers.handle_text.

The report generation itself (fundamentals + Claude call) is covered by
test_analysis.py and test_fundamentals.py already, same rationale as
test_commands.py's analyze tests — here we replace the background task's
target with a mock so no real network call happens.
"""

import asyncio
import sqlite3
from datetime import date
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


def make_update_and_context(conn, text: str, chat_id: int = 1, anthropic_api_key="fake-key"):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.text = text
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {
        "conn": conn,
        "settings": MagicMock(telegram_chat_id=chat_id, anthropic_api_key=anthropic_api_key),
    }
    return update, context


def run(coro):
    return asyncio.run(coro)


def _seed_pick(conn, rank=1, ticker="AAPL") -> None:
    db.save_daily_picks(
        conn,
        date.today().isoformat(),
        [
            {
                "rank": rank,
                "ticker": ticker,
                "company_name": "Apple Inc.",
                "politician_name": "Jane Smith",
                "chamber": "House",
                "transaction_type": "purchase",
                "amount_band": "$50,001 - $100,000",
                "trade_date": "2026-07-28",
                "filed_date": "2026-08-08",
                "score": 9.2,
            }
        ],
    )


def test_no_pending_picks_replies_accordingly(conn):
    update, context = make_update_and_context(conn, "1")

    run(handlers.handle_text(update, context))

    assert "No pending picks" in update.message.reply_text.call_args[0][0]


def test_selecting_a_pick_marks_selected_and_schedules_report(conn):
    _seed_pick(conn)
    update, context = make_update_and_context(conn, "1")

    with patch("app.commands.picks._generate_and_send_report", new=AsyncMock()) as mock_generate:
        run(handlers.handle_text(update, context))

    pick = conn.execute("SELECT * FROM daily_picks").fetchone()
    assert pick["status"] == "selected"
    mock_generate.assert_called_once()
    update.message.reply_text.assert_awaited()


def test_selecting_all_schedules_every_pending_pick(conn):
    _seed_pick(conn, rank=1, ticker="AAPL")
    db.save_daily_picks(
        conn,
        date.today().isoformat(),
        [
            {
                "rank": 2, "ticker": "TSLA", "company_name": "Tesla Inc.",
                "politician_name": "John Doe", "chamber": "Senate",
                "transaction_type": "purchase", "amount_band": "$15,001 - $50,000",
                "trade_date": "2026-08-01", "filed_date": "2026-08-08", "score": 6.0,
            }
        ],
    )
    update, context = make_update_and_context(conn, "all")

    with patch("app.commands.picks._generate_and_send_report", new=AsyncMock()) as mock_generate:
        run(handlers.handle_text(update, context))

    assert mock_generate.call_count == 2


def test_skip_marks_all_pending_as_skipped_without_scheduling(conn):
    _seed_pick(conn)
    update, context = make_update_and_context(conn, "skip")

    with patch("app.commands.picks._generate_and_send_report", new=AsyncMock()) as mock_generate:
        run(handlers.handle_text(update, context))

    mock_generate.assert_not_called()
    pick = conn.execute("SELECT * FROM daily_picks").fetchone()
    assert pick["status"] == "skipped"
    assert "Skipped" in update.message.reply_text.call_args[0][0]


def test_out_of_range_selection_leaves_state_untouched(conn):
    _seed_pick(conn)
    update, context = make_update_and_context(conn, "5")

    with patch("app.commands.picks._generate_and_send_report", new=AsyncMock()) as mock_generate:
        run(handlers.handle_text(update, context))

    mock_generate.assert_not_called()
    pick = conn.execute("SELECT * FROM daily_picks").fetchone()
    assert pick["status"] == "pending"
    assert "don't match" in update.message.reply_text.call_args[0][0]


def test_no_api_key_replies_with_setup_message(conn):
    _seed_pick(conn)
    update, context = make_update_and_context(conn, "1", anthropic_api_key=None)

    with patch("app.commands.picks._generate_and_send_report", new=AsyncMock()) as mock_generate:
        run(handlers.handle_text(update, context))

    mock_generate.assert_not_called()
    assert "ANTHROPIC_API_KEY" in update.message.reply_text.call_args[0][0]
    pick = conn.execute("SELECT * FROM daily_picks").fetchone()
    assert pick["status"] == "pending"
