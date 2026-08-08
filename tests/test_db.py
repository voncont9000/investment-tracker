import sqlite3

import pytest

from app import db


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)
    yield connection
    connection.close()


def test_add_and_list_watchlist(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    items = db.list_watchlist(conn)
    assert len(items) == 1
    assert items[0]["ticker"] == "AAPL"
    assert items[0]["active"] == 1


def test_add_watchlist_item_upserts_on_duplicate_ticker(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    items = db.list_watchlist(conn)
    assert len(items) == 1


def test_add_and_list_holdings(conn):
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    db.add_holding(conn, "TSLA", "Tesla Inc.", 210.0)
    holdings = db.list_holdings(conn)
    assert len(holdings) == 2  # two separate lots


def test_list_distinct_holding_tickers_dedupes(conn):
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    db.add_holding(conn, "TSLA", "Tesla Inc.", 210.0)
    db.add_holding(conn, "AAPL", "Apple Inc.", 150.0)
    tickers = db.list_distinct_holding_tickers(conn)
    assert sorted(tickers) == ["AAPL", "TSLA"]


def test_price_history_insert_and_prune(conn):
    db.insert_price_snapshot(conn, "AAPL", 150.0)
    rows = conn.execute("SELECT * FROM price_history").fetchall()
    assert len(rows) == 1

    # Manually backdate the row past the prune cutoff.
    conn.execute(
        "UPDATE price_history SET fetched_at = '2000-01-01T00:00:00+00:00'"
    )
    conn.commit()
    deleted = db.prune_price_history(conn, older_than_hours=36)
    assert deleted == 1
    assert conn.execute("SELECT * FROM price_history").fetchall() == []


def test_alert_state_set_and_clear(conn):
    assert db.get_alert_state(conn, "AAPL", "watchlist_drop") is None

    db.set_in_alert(conn, "AAPL", "watchlist_drop", True)
    state = db.get_alert_state(conn, "AAPL", "watchlist_drop")
    assert state["in_alert"] == 1
    assert state["last_alerted_at"] is not None

    db.set_in_alert(conn, "AAPL", "watchlist_drop", False)
    state = db.get_alert_state(conn, "AAPL", "watchlist_drop")
    assert state["in_alert"] == 0


# --- Watchlist removal ---

def test_is_in_watchlist(conn):
    assert db.is_in_watchlist(conn, "AAPL") is False
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    assert db.is_in_watchlist(conn, "AAPL") is True


def test_remove_watchlist_item(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    assert db.remove_watchlist_item(conn, "AAPL") is True
    assert db.list_watchlist(conn) == []
    assert db.is_in_watchlist(conn, "AAPL") is False


def test_remove_watchlist_item_reports_when_absent(conn):
    # Nothing to remove — must return False so the caller can say so.
    assert db.remove_watchlist_item(conn, "AAPL") is False


def test_removed_ticker_can_be_re_added(conn):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    db.remove_watchlist_item(conn, "AAPL")
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    assert db.is_in_watchlist(conn, "AAPL") is True


# --- Selling ---

def test_get_active_holdings_for_ticker(conn):
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    db.add_holding(conn, "AAPL", "Apple Inc.", 150.0)
    lots = db.get_active_holdings_for_ticker(conn, "TSLA")
    assert len(lots) == 1
    assert lots[0]["ticker"] == "TSLA"


def test_sell_holdings_closes_lots_and_records_sale(conn):
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    lots = db.sell_holdings(conn, "TSLA", 250.0)

    assert len(lots) == 1
    assert lots[0]["purchase_price"] == 200.0  # pre-sale state, for P/L math

    assert db.get_active_holdings_for_ticker(conn, "TSLA") == []
    assert db.list_distinct_holding_tickers(conn) == []

    sold = conn.execute("SELECT * FROM holdings WHERE ticker = 'TSLA'").fetchone()
    assert sold["active"] == 0
    assert sold["sell_price"] == 250.0
    assert sold["sell_date"] is not None


def test_sell_holdings_closes_all_lots_of_a_ticker(conn):
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    db.add_holding(conn, "TSLA", "Tesla Inc.", 220.0)
    lots = db.sell_holdings(conn, "TSLA", 250.0)

    assert len(lots) == 2
    assert db.get_active_holdings_for_ticker(conn, "TSLA") == []


def test_sell_holdings_leaves_other_tickers_untouched(conn):
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    db.add_holding(conn, "AAPL", "Apple Inc.", 150.0)
    db.sell_holdings(conn, "TSLA", 250.0)
    assert db.list_distinct_holding_tickers(conn) == ["AAPL"]


def test_sell_holdings_when_nothing_held(conn):
    assert db.sell_holdings(conn, "TSLA", 250.0) == []


# --- Alert state cleanup ---

def test_clear_alert_state_removes_the_row(conn):
    db.set_in_alert(conn, "AAPL", "watchlist_drop", True)
    db.clear_alert_state(conn, "AAPL", "watchlist_drop")
    # Fully gone, not just flipped to 0 — so a re-added stock starts clean
    # and its first real alert isn't suppressed.
    assert db.get_alert_state(conn, "AAPL", "watchlist_drop") is None


def test_clear_alert_state_is_scoped_to_alert_type(conn):
    db.set_in_alert(conn, "AAPL", "watchlist_drop", True)
    db.set_in_alert(conn, "AAPL", "holding_gain", True)
    db.clear_alert_state(conn, "AAPL", "watchlist_drop")
    assert db.get_alert_state(conn, "AAPL", "watchlist_drop") is None
    assert db.get_alert_state(conn, "AAPL", "holding_gain") is not None


# --- Migration ---

def test_migrate_is_idempotent(conn):
    # init_db already ran migrate once via the fixture; running again must
    # not raise (guards the "column already exists" ALTER TABLE error).
    db.migrate(conn)
    db.migrate(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(holdings)")}
    assert {"sell_price", "sell_date"} <= columns


# --- Politician trades: processed_filings idempotency guard ---

def test_filing_not_processed_by_default(conn):
    assert db.is_filing_processed(conn, "12345") is False


def test_mark_filing_processed_and_check(conn):
    db.mark_filing_processed(conn, "12345", "house_clerk")
    assert db.is_filing_processed(conn, "12345") is True


def test_mark_filing_processed_twice_does_not_raise(conn):
    # The digest job may re-run after a crash; re-marking the same doc_id
    # must be a safe no-op, not a UNIQUE constraint error.
    db.mark_filing_processed(conn, "12345", "house_clerk")
    db.mark_filing_processed(conn, "12345", "house_clerk")
    assert db.is_filing_processed(conn, "12345") is True


# --- Politician trades: daily_picks ---

def test_save_daily_picks_and_list_pending(conn):
    picks = [
        {
            "rank": 1,
            "ticker": "NVDA",
            "company_name": "Nvidia Corp",
            "politician_name": "Jane Smith",
            "chamber": "House",
            "transaction_type": "purchase",
            "amount_band": "$50,001-$100,000",
            "trade_date": "2026-07-28",
            "filed_date": "2026-08-08",
            "score": 9.2,
        },
        {
            "rank": 2,
            "ticker": "PLTR",
            "company_name": "Palantir Technologies",
            "politician_name": "John Doe",
            "chamber": "Senate",
            "transaction_type": "purchase",
            "amount_band": "$15,001-$50,000",
            "trade_date": "2026-07-20",
            "filed_date": "2026-08-07",
            "score": 7.8,
        },
    ]
    db.save_daily_picks(conn, "2026-08-08", picks)

    pending = db.get_pending_picks(conn, "2026-08-08")
    assert len(pending) == 2
    assert pending[0]["rank"] == 1
    assert pending[0]["ticker"] == "NVDA"
    assert pending[0]["status"] == "pending"
    assert pending[1]["rank"] == 2


def test_get_pending_picks_excludes_resolved(conn):
    db.save_daily_picks(
        conn,
        "2026-08-08",
        [
            {
                "rank": 1, "ticker": "NVDA", "company_name": "Nvidia Corp",
                "politician_name": "Jane Smith", "chamber": "House",
                "transaction_type": "purchase", "amount_band": "$50,001-$100,000",
                "trade_date": "2026-07-28", "filed_date": "2026-08-08", "score": 9.2,
            }
        ],
    )
    pick_id = db.get_pending_picks(conn, "2026-08-08")[0]["id"]
    db.set_pick_status(conn, pick_id, "selected")
    assert db.get_pending_picks(conn, "2026-08-08") == []


def test_get_pending_picks_for_a_different_date_is_empty(conn):
    db.save_daily_picks(
        conn,
        "2026-08-08",
        [
            {
                "rank": 1, "ticker": "NVDA", "company_name": "Nvidia Corp",
                "politician_name": "Jane Smith", "chamber": "House",
                "transaction_type": "purchase", "amount_band": "$50,001-$100,000",
                "trade_date": "2026-07-28", "filed_date": "2026-08-08", "score": 9.2,
            }
        ],
    )
    assert db.get_pending_picks(conn, "2026-08-09") == []


def test_set_pick_status_and_report_path(conn):
    db.save_daily_picks(
        conn,
        "2026-08-08",
        [
            {
                "rank": 1, "ticker": "NVDA", "company_name": "Nvidia Corp",
                "politician_name": "Jane Smith", "chamber": "House",
                "transaction_type": "purchase", "amount_band": "$50,001-$100,000",
                "trade_date": "2026-07-28", "filed_date": "2026-08-08", "score": 9.2,
            }
        ],
    )
    pick_id = db.get_pending_picks(conn, "2026-08-08")[0]["id"]
    db.set_pick_status(conn, pick_id, "selected", report_path="reports/politician-trades/NVDA-2026-08-08.md")

    row = conn.execute("SELECT * FROM daily_picks WHERE id = ?", (pick_id,)).fetchone()
    assert row["status"] == "selected"
    assert row["report_path"] == "reports/politician-trades/NVDA-2026-08-08.md"


def test_migrate_adds_columns_to_a_preexisting_table(conn):
    """Simulate the live DB: a holdings table created before the sell
    columns existed."""
    conn.execute("DROP TABLE holdings")
    conn.execute(
        """CREATE TABLE holdings (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ticker TEXT NOT NULL,
               company_name TEXT NOT NULL,
               purchase_price REAL NOT NULL,
               purchase_date TEXT NOT NULL,
               active INTEGER NOT NULL DEFAULT 1
           )"""
    )
    conn.execute(
        "INSERT INTO holdings (ticker, company_name, purchase_price, purchase_date) "
        "VALUES ('AAPL', 'Apple Inc.', 150.0, '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()

    db.migrate(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(holdings)")}
    assert {"sell_price", "sell_date"} <= columns
    # Pre-existing row survives, with NULL sell fields.
    row = conn.execute("SELECT * FROM holdings WHERE ticker = 'AAPL'").fetchone()
    assert row["purchase_price"] == 150.0
    assert row["sell_price"] is None
