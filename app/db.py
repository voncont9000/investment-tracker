"""SQLite connection handling, schema init, and CRUD helpers.

Single-user tool, raw sqlite3 (stdlib) — no ORM. Callers open a connection
via get_connection() and pass it into these functions; each function commits
its own write so callers don't need to manage transactions explicitly.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

# Single source of truth for valid alert_type values — used both to build
# the CHECK constraint below and to filter old rows during migration, so
# the two can never drift apart.
_CURRENT_ALERT_TYPES = (
    "setup1_uptrend_pullback",
    "setup2_momentum_dip",
    "setup3_breakout_retest",
    "setup4_oversold_reversal",
    "setup5_deep_pullback",
    "exit_take_profit",
    "exit_stop_loss",
    "exit_trailing_stop",
    "exit_trend_break",
    "exit_momentum_breakdown",
    "thesis_break",
)
_ALERT_TYPES_SQL_LIST = ", ".join(f"'{t}'" for t in _CURRENT_ALERT_TYPES)

_ALERT_STATE_COLUMNS_SQL = f"""
    ticker TEXT NOT NULL,
    alert_type TEXT NOT NULL CHECK(alert_type IN ({_ALERT_TYPES_SQL_LIST})),
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

CREATE TABLE IF NOT EXISTS thesis_check_state (
    ticker TEXT PRIMARY KEY,
    last_checked_at TEXT NOT NULL,
    last_headline_at TEXT
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

    # SQLite can't alter a CHECK constraint in place. If alert_state's
    # CHECK doesn't yet list the exit-alert types (either because it's
    # still on the old watchlist_drop/holding_gain values, or on the
    # entry-point-only setup values from before the sell-alerts feature),
    # recreate it with the current column list (_ALERT_STATE_COLUMNS_SQL).
    # Old rows whose alert_type isn't one of the current values are
    # dropped in the rewrite — the watchlist_drop/holding_gain semantics
    # don't mean anything under the new system; setup1-5 rows, which are
    # still valid, are preserved by filtering into the new table instead
    # of just swapping the schema.
    alert_state_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alert_state'"
    ).fetchone()
    if alert_state_row is not None and "exit_take_profit" not in alert_state_row["sql"]:
        conn.execute("ALTER TABLE alert_state RENAME TO alert_state_old")
        conn.execute(f"CREATE TABLE alert_state ({_ALERT_STATE_COLUMNS_SQL})")
        conn.execute(
            f"""INSERT INTO alert_state (ticker, alert_type, in_alert, last_alerted_at)
                SELECT ticker, alert_type, in_alert, last_alerted_at FROM alert_state_old
                WHERE alert_type IN ({",".join("?" * len(_CURRENT_ALERT_TYPES))})""",
            _CURRENT_ALERT_TYPES,
        )
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


def avg_cost_by_ticker(conn: sqlite3.Connection) -> dict[str, float]:
    """Average purchase price per ticker across its active lots — the cost
    basis the sell-alert P&L rules (app/exits.py) compare the current price
    against. Unweighted (there's no share quantity in the schema yet), so
    it's exact only when a ticker's lots are equal-sized."""
    rows = conn.execute(
        "SELECT ticker, AVG(purchase_price) AS avg_cost FROM holdings WHERE active = 1 GROUP BY ticker"
    ).fetchall()
    return {row["ticker"]: row["avg_cost"] for row in rows}


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
    "in alert" for several setup/exit types at once, so this clears all of
    them rather than requiring the caller to enumerate alert ids.
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


# --- Thesis check state (app/thesis.py's weekly sweep) ---

def get_thesis_check_state(conn: sqlite3.Connection, ticker: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM thesis_check_state WHERE ticker = ?", (ticker,)
    ).fetchone()


def set_thesis_check_state(
    conn: sqlite3.Connection, ticker: str, last_checked_at: str, last_headline_at: str | None
) -> None:
    conn.execute(
        """INSERT INTO thesis_check_state (ticker, last_checked_at, last_headline_at)
           VALUES (?, ?, ?)
           ON CONFLICT(ticker) DO UPDATE SET
               last_checked_at = excluded.last_checked_at,
               last_headline_at = excluded.last_headline_at""",
        (ticker, last_checked_at, last_headline_at),
    )
    conn.commit()


def delete_thesis_check_state(conn: sqlite3.Connection, ticker: str) -> None:
    """Called when a ticker is fully sold, alongside clear_all_alert_state —
    buying back in later then starts the weekly check from scratch instead
    of inheriting a stale last-checked date from the earlier holding."""
    conn.execute("DELETE FROM thesis_check_state WHERE ticker = ?", (ticker,))
    conn.commit()
