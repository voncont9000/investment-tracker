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

def test_clear_all_alert_state_removes_all_types_for_ticker(conn):
    db.set_in_alert(conn, "AAPL", "setup1_uptrend_pullback", True)
    db.set_in_alert(conn, "AAPL", "setup2_momentum_dip", True)
    db.set_in_alert(conn, "AAPL", "setup3_breakout_retest", True)
    db.clear_all_alert_state(conn, "AAPL")
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback") is None
    assert db.get_alert_state(conn, "AAPL", "setup2_momentum_dip") is None
    assert db.get_alert_state(conn, "AAPL", "setup3_breakout_retest") is None


def test_exit_alert_types_are_accepted(conn):
    for alert_type in (
        "exit_take_profit",
        "exit_stop_loss",
        "exit_trailing_stop",
        "exit_trend_break",
        "exit_momentum_breakdown",
        "thesis_break",
    ):
        db.set_in_alert(conn, "AAPL", alert_type, True)
        assert db.get_alert_state(conn, "AAPL", alert_type)["in_alert"] == 1


# --- Average cost ---

def test_avg_cost_by_ticker_averages_multiple_lots(conn):
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    db.add_holding(conn, "TSLA", "Tesla Inc.", 220.0)
    db.add_holding(conn, "AAPL", "Apple Inc.", 150.0)
    costs = db.avg_cost_by_ticker(conn)
    assert costs == {"TSLA": 210.0, "AAPL": 150.0}


def test_avg_cost_by_ticker_ignores_sold_lots(conn):
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    db.sell_holdings(conn, "TSLA", 250.0)
    assert db.avg_cost_by_ticker(conn) == {}


# --- Thesis check state ---

def test_thesis_check_state_starts_unset(conn):
    assert db.get_thesis_check_state(conn, "AAPL") is None


def test_set_and_get_thesis_check_state(conn):
    db.set_thesis_check_state(conn, "AAPL", "2026-09-01T00:00:00+00:00", "2026-08-30T00:00:00+00:00")
    state = db.get_thesis_check_state(conn, "AAPL")
    assert state["last_checked_at"] == "2026-09-01T00:00:00+00:00"
    assert state["last_headline_at"] == "2026-08-30T00:00:00+00:00"


def test_set_thesis_check_state_upserts(conn):
    db.set_thesis_check_state(conn, "AAPL", "2026-09-01T00:00:00+00:00", None)
    db.set_thesis_check_state(conn, "AAPL", "2026-09-08T00:00:00+00:00", "2026-09-07T00:00:00+00:00")
    state = db.get_thesis_check_state(conn, "AAPL")
    assert state["last_checked_at"] == "2026-09-08T00:00:00+00:00"
    assert state["last_headline_at"] == "2026-09-07T00:00:00+00:00"


def test_delete_thesis_check_state(conn):
    db.set_thesis_check_state(conn, "AAPL", "2026-09-01T00:00:00+00:00", None)
    db.delete_thesis_check_state(conn, "AAPL")
    assert db.get_thesis_check_state(conn, "AAPL") is None


def test_delete_thesis_check_state_when_absent_does_not_raise(conn):
    db.delete_thesis_check_state(conn, "AAPL")  # nothing to delete


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


def test_migrate_widens_alert_state_for_exit_types(conn):
    # Simulate a live DB on the entry-point-only schema (5 setup types, no
    # exit types yet) — the state this migration itself needs to widen.
    conn.execute("DROP TABLE alert_state")
    conn.execute(
        """CREATE TABLE alert_state (
               ticker TEXT NOT NULL,
               alert_type TEXT NOT NULL CHECK(alert_type IN (
                   'setup1_uptrend_pullback',
                   'setup2_momentum_dip',
                   'setup3_breakout_retest',
                   'setup4_oversold_reversal',
                   'setup5_deep_pullback'
               )),
               in_alert INTEGER NOT NULL DEFAULT 0,
               last_alerted_at TEXT,
               PRIMARY KEY (ticker, alert_type)
           )"""
    )
    conn.execute(
        "INSERT INTO alert_state (ticker, alert_type, in_alert) VALUES ('AAPL', 'setup1_uptrend_pullback', 1)"
    )
    conn.commit()

    db.migrate(conn)

    # Existing entry-point alert state survives — only the CHECK constraint
    # needed widening, not the data.
    assert db.get_alert_state(conn, "AAPL", "setup1_uptrend_pullback")["in_alert"] == 1

    # The new exit types are now accepted.
    db.set_in_alert(conn, "AAPL", "exit_take_profit", True)
    assert db.get_alert_state(conn, "AAPL", "exit_take_profit")["in_alert"] == 1


def test_migrate_creates_thesis_check_state_table(conn):
    conn.execute("DROP TABLE IF EXISTS thesis_check_state")
    conn.commit()
    db.init_db(conn)  # re-run schema + migrate, as main() does on every startup
    db.set_thesis_check_state(conn, "AAPL", "2026-09-01T00:00:00+00:00", None)
    assert db.get_thesis_check_state(conn, "AAPL") is not None
