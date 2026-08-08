"""Digest orchestration: sources -> extraction -> scoring -> persistence ->
Telegram message. Mirrors the FakeSender/mocking style of tests/test_alerts.py."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import db
from app.politician_trades import digest
from app.politician_trades.sources.base import RawFiling


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def settings():
    return SimpleNamespace(anthropic_api_key="fake-key")


class FakeSender:
    def __init__(self):
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        self.messages.append(text)


def run(coro):
    return asyncio.run(coro)


def _filing(doc_id="111") -> RawFiling:
    return RawFiling(
        doc_id=doc_id,
        chamber="House",
        politician_name="Jane Smith",
        filed_date="2026-08-08",
        pdf_url="https://example.com/111.pdf",
    )


def test_sends_quiet_day_message_when_no_filings(conn, settings):
    sender = FakeSender()
    with patch("app.politician_trades.digest.house_clerk.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.senate.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.whitehouse.fetch_recent_filings", return_value=[]):
        run(digest.run_daily_digest(conn, settings, sender.send))

    assert len(sender.messages) == 1
    assert "No qualifying" in sender.messages[0]


def test_sends_ranked_digest_for_qualifying_purchase(conn, settings):
    sender = FakeSender()
    trade = {
        "asset_description": "Apple Inc.",
        "transaction_type": "purchase",
        "amount_band": "$50,001 - $100,000",
        "trade_date": "2026-07-28",
    }
    with patch("app.politician_trades.digest.house_clerk.fetch_recent_filings", return_value=[_filing()]), \
         patch("app.politician_trades.digest.senate.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.whitehouse.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.pdf_extract.extract_trades", return_value=[trade]), \
         patch("app.politician_trades.digest.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")):
        run(digest.run_daily_digest(conn, settings, sender.send))

    assert len(sender.messages) == 1
    message = sender.messages[0]
    assert "AAPL" in message
    assert "Jane Smith" in message
    assert '"1 3"' in message or "1 3" in message  # reply-format hint present

    pending = db.get_pending_picks(conn, __import__("datetime").date.today().isoformat())
    assert len(pending) == 1
    assert pending[0]["ticker"] == "AAPL"
    assert pending[0]["status"] == "pending"


def test_marks_filings_as_processed(conn, settings):
    sender = FakeSender()
    with patch("app.politician_trades.digest.house_clerk.fetch_recent_filings", return_value=[_filing("111")]), \
         patch("app.politician_trades.digest.senate.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.whitehouse.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.pdf_extract.extract_trades", return_value=[]):
        run(digest.run_daily_digest(conn, settings, sender.send))

    assert db.is_filing_processed(conn, "111") is True


def test_does_not_reprocess_already_processed_filings(conn, settings):
    db.mark_filing_processed(conn, "111", "House")
    sender = FakeSender()

    with patch("app.politician_trades.digest.house_clerk.fetch_recent_filings", return_value=[_filing("111")]), \
         patch("app.politician_trades.digest.senate.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.whitehouse.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.pdf_extract.extract_trades") as mock_extract:
        run(digest.run_daily_digest(conn, settings, sender.send))

    mock_extract.assert_not_called()


def test_only_purchases_qualify_for_the_digest(conn, settings):
    sender = FakeSender()
    trade = {
        "asset_description": "Apple Inc.",
        "transaction_type": "sale",
        "amount_band": "$50,001 - $100,000",
        "trade_date": "2026-07-28",
    }
    with patch("app.politician_trades.digest.house_clerk.fetch_recent_filings", return_value=[_filing()]), \
         patch("app.politician_trades.digest.senate.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.whitehouse.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.pdf_extract.extract_trades", return_value=[trade]), \
         patch("app.politician_trades.digest.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")):
        run(digest.run_daily_digest(conn, settings, sender.send))

    assert "No qualifying" in sender.messages[0]


def test_unresolvable_ticker_is_skipped_without_crashing(conn, settings):
    sender = FakeSender()
    trade = {
        "asset_description": "Some Fictional Corp",
        "transaction_type": "purchase",
        "amount_band": "$1,001 - $15,000",
        "trade_date": "2026-07-28",
    }
    with patch("app.politician_trades.digest.house_clerk.fetch_recent_filings", return_value=[_filing()]), \
         patch("app.politician_trades.digest.senate.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.whitehouse.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.pdf_extract.extract_trades", return_value=[trade]), \
         patch("app.politician_trades.digest.ticker_resolver.resolve_ticker", return_value=None):
        run(digest.run_daily_digest(conn, settings, sender.send))

    assert "No qualifying" in sender.messages[0]


def test_includes_filings_from_the_whitehouse_source(conn, settings):
    sender = FakeSender()
    trade = {
        "asset_description": "Apple Inc.",
        "transaction_type": "purchase",
        "amount_band": "$1,000,001 - $5,000,000",
        "trade_date": "2026-07-28",
    }
    trump_filing = RawFiling(
        doc_id="trump-1",
        chamber="Executive",
        politician_name="Donald J. Trump",
        filed_date="2026-08-08",
        pdf_url="https://www.whitehouse.gov/wp-content/uploads/trump-ptr.pdf",
    )
    with patch("app.politician_trades.digest.house_clerk.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.senate.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.whitehouse.fetch_recent_filings", return_value=[trump_filing]), \
         patch("app.politician_trades.digest.pdf_extract.extract_trades", return_value=[trade]), \
         patch("app.politician_trades.digest.ticker_resolver.resolve_ticker", return_value=("AAPL", "Apple Inc.")):
        run(digest.run_daily_digest(conn, settings, sender.send))

    message = sender.messages[0]
    assert "AAPL" in message
    assert "Donald J. Trump" in message
    assert db.is_filing_processed(conn, "trump-1") is True


def test_no_api_key_skips_extraction_without_crashing(conn):
    settings = SimpleNamespace(anthropic_api_key=None)
    sender = FakeSender()
    with patch("app.politician_trades.digest.house_clerk.fetch_recent_filings", return_value=[_filing()]), \
         patch("app.politician_trades.digest.senate.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.whitehouse.fetch_recent_filings", return_value=[]), \
         patch("app.politician_trades.digest.pdf_extract.extract_trades") as mock_extract:
        run(digest.run_daily_digest(conn, settings, sender.send))

    mock_extract.assert_not_called()
    assert "No qualifying" in sender.messages[0]
