"""SQLite connection handling, schema init, and CRUD helpers.

Single-user tool, raw sqlite3 (stdlib) — no ORM. Callers open a connection
via get_connection() and pass it into these functions; each function commits
its own write so callers don't need to manage transactions explicitly.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

_ALERT_STATE_COLUMNS_SQL = """
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
"""

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    company_name TEXT NOT NULL,
    date_added TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    purchase_price REAL NOT NULL,
    purchase_date TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS alert_state (
{_ALERT_STATE_COLUMNS_SQL}
);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a connection with row access by column name.

    check_same_thread=False because python-telegram-bot's JobQueue and
    asyncio.to_thread() calls may hand this connection between threads;
    all our own access is still effectively serialized on the event loop.
    """
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    """Apply schema changes to an existing database. Each change checks
    the current schema first, making this idempotent and safe to run on
    every startup."""
    holdings_columns = {row["name"] for row in conn.execute("PRAGMA table_info(holdings)")}

    if "sell_price" not in holdings_columns:
        conn.execute("ALTER TABLE holdings ADD COLUMN sell_price REAL")
    if "sell_date" not in holdings_columns:
        conn.execute("ALTER TABLE holdings ADD COLUMN sell_date TEXT")

    # price_history existed only to serve the old trailing-window alert
    # design (see git history) and is unused by anything else.
    conn.execute("DROP TABLE IF EXISTS price_history")

    # SQLite can't alter a CHECK constraint in place. If alert_state is
    # still on the old (watchlist_drop/holding_gain) values, recreate it
    # with the new setup-based ones (same _ALERT_STATE_COLUMNS_SQL as
    # SCHEMA_SQL) — dropping existing rows, since the old alert semantics
    # don't mean anything under the new system.
    alert_state_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alert_state'"
    ).fetchone()
    if alert_state_row is not None and "watchlist_drop" in alert_state_row["sql"]:
        conn.execute("ALTER TABLE alert_state RENAME TO alert_state_old")
        conn.execute(f"CREATE TABLE alert_state ({_ALERT_STATE_COLUMNS_SQL})")
        conn.execute("DROP TABLE alert_state_old")

    conn.commit()


# --- Watchlist ---

def add_watchlist_item(conn: sqlite3.Connection, ticker: str, company_name: str) -> None:
    conn.execute(
        """INSERT INTO watchlist (ticker, company_name, date_added, active)
           VALUES (?, ?, ?, 1)
           ON CONFLICT(ticker) DO UPDATE SET active = 1, company_name = excluded.company_name""",
        (ticker, company_name, utcnow_iso()),
    )
    conn.commit()


def list_watchlist(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    query = "SELECT * FROM watchlist"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY date_added"
    return conn.execute(query).fetchall()


def is_in_watchlist(conn: sqlite3.Connection, ticker: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM watchlist WHERE ticker = ? AND active = 1", (ticker,)
    ).fetchone()
    return row is not None


def remove_watchlist_item(conn: sqlite3.Connection, ticker: str) -> bool:
    """Soft-delete a watchlist entry. Returns True if a row was actually
    removed, so the caller can distinguish "removed" from "wasn't there"."""
    cursor = conn.execute(
        "UPDATE watchlist SET active = 0 WHERE ticker = ? AND active = 1", (ticker,)
    )
    conn.commit()
    return cursor.rowcount > 0


# --- Holdings ---

def add_holding(conn: sqlite3.Connection, ticker: str, company_name: str, purchase_price: float) -> None:
    conn.execute(
        """INSERT INTO holdings (ticker, company_name, purchase_price, purchase_date, active)
           VALUES (?, ?, ?, ?, 1)""",
        (ticker, company_name, purchase_price, utcnow_iso()),
    )
    conn.commit()


def list_holdings(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    query = "SELECT * FROM holdings"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY purchase_date"
    return conn.execute(query).fetchall()


def list_distinct_holding_tickers(conn: sqlite3.Connection) -> list[str]:
    """Distinct tickers across all holding lots, so buying the same stock
    twice doesn't produce two separate alert checks."""
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM holdings WHERE active = 1"
    ).fetchall()
    return [row["ticker"] for row in rows]


def get_active_holdings_for_ticker(conn: sqlite3.Connection, ticker: str) -> list[sqlite3.Row]:
    """The open lots a sale would close, oldest first."""
    return conn.execute(
        "SELECT * FROM holdings WHERE ticker = ? AND active = 1 ORDER BY purchase_date",
        (ticker,),
    ).fetchall()


def sell_holdings(
    conn: sqlite3.Connection, ticker: str, sell_price: float
) -> list[sqlite3.Row]:
    """Close every open lot of `ticker`, recording the sale price and date.

    Returns the lots as they were *before* closing, so the caller can compute
    the cost basis for a profit/loss reply.
    """
    lots = get_active_holdings_for_ticker(conn, ticker)
    if not lots:
        return []

    conn.execute(
        """UPDATE holdings
           SET active = 0, sell_price = ?, sell_date = ?
           WHERE ticker = ? AND active = 1""",
        (sell_price, utcnow_iso(), ticker),
    )
    conn.commit()
    return lots


# --- Alert state ---

def get_alert_state(conn: sqlite3.Connection, ticker: str, alert_type: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM alert_state WHERE ticker = ? AND alert_type = ?",
        (ticker, alert_type),
    ).fetchone()


def clear_all_alert_state(conn: sqlite3.Connection, ticker: str) -> None:
    """Delete every alert_state row for a ticker, regardless of alert_type.

    Called when a stock leaves the watchlist or is sold. A ticker can be
    "in alert" for any of the 5 setup types at once, so this clears all of
    them rather than requiring the caller to enumerate setup ids.
    """
    conn.execute("DELETE FROM alert_state WHERE ticker = ?", (ticker,))
    conn.commit()


def set_in_alert(conn: sqlite3.Connection, ticker: str, alert_type: str, in_alert: bool) -> None:
    if in_alert:
        conn.execute(
            """INSERT INTO alert_state (ticker, alert_type, in_alert, last_alerted_at)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(ticker, alert_type) DO UPDATE SET in_alert = 1, last_alerted_at = excluded.last_alerted_at""",
            (ticker, alert_type, utcnow_iso()),
        )
    else:
        conn.execute(
            """INSERT INTO alert_state (ticker, alert_type, in_alert, last_alerted_at)
               VALUES (?, ?, 0, NULL)
               ON CONFLICT(ticker, alert_type) DO UPDATE SET in_alert = 0""",
            (ticker, alert_type),
        )
    conn.commit()
