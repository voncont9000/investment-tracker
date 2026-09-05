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


def test_migrate_removes_price_history_table(conn):
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='price_history'"
    ).fetchone() is None
    db.migrate(conn)  # must not raise even though the table is already gone


def test_alert_state_set_and_clear(conn):
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback") is None

    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    state = db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback")
    assert state["in_alert"] == 1
    assert state["last_alerted_at"] is not None

    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", False)
    state = db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback")
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
    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    db.clear_alert_state(conn, "AAPL", "setup1_uptrend_pullback")
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback") is None


def test_clear_alert_state_is_scoped_to_alert_type(conn):
    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    db.set_in_alert(conn, "AAPL", "setup2_momentum_dip", True)
    db.clear_alert_state(conn, "AAPL", "setup1_uptrend_pullback")
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback") is None
    assert db.get_alert_state(conn, "AAPL", "setup2_momentum_dip") is not None


# --- Migration ---

def test_migrate_is_idempotent(conn):
    # init_db already ran migrate once via the fixture; running again must
    # not raise (guards the "column already exists" ALTER TABLE error).
    db.migrate(conn)
    db.migrate(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(holdings)")}
    assert {"sell_price", "sell_date"} <= columns


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


def test_migrate_recreates_alert_state_with_new_alert_types(conn):
    # Simulate a live DB still on the old alert_state schema/values.
    conn.execute("DROP TABLE alert_state")
    conn.execute(
        """CREATE TABLE alert_state (
               ticker TEXT NOT NULL,
               alert_type TEXT NOT NULL CHECK(alert_type IN ('watchlist_drop','holding_gain')),
               in_alert INTEGER NOT NULL DEFAULT 0,
               last_alerted_at TEXT,
               PRIMARY KEY (ticker, alert_type)
           )"""
    )
    conn.execute(
        "INSERT INTO alert_state (ticker, alert_type, in_alert) VALUES ('AAPL', 'watchlist_drop', 1)"
    )
    conn.commit()

    db.migrate(conn)

    # The old row's semantics no longer apply under the new system.
    assert conn.execute("SELECT * FROM alert_state").fetchall() == []

    # New alert types are accepted...
    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback")["in_alert"] == 1

    # ...and the old ones are now rejected by the CHECK constraint.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alert_state (ticker, alert_type, in_alert) VALUES ('TSLA', 'watchlist_drop', 1)"
        )
