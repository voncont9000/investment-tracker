"""SQLite connection handling, schema init, and CRUD helpers.

Single-user tool, raw sqlite3 (stdlib) — no ORM. Callers open a connection
via get_connection() and pass it into these functions; each function commits
its own write so callers don't need to manage transactions explicitly.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

SCHEMA_SQL = """
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

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    price REAL NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_ticker_time ON price_history(ticker, fetched_at);

CREATE TABLE IF NOT EXISTS alert_state (
    ticker TEXT NOT NULL,
    alert_type TEXT NOT NULL CHECK(alert_type IN ('watchlist_drop','holding_gain')),
    in_alert INTEGER NOT NULL DEFAULT 0,
    last_alerted_at TEXT,
    PRIMARY KEY (ticker, alert_type)
);

CREATE TABLE IF NOT EXISTS processed_filings (
    doc_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_date TEXT NOT NULL,
    rank INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    politician_name TEXT NOT NULL,
    chamber TEXT NOT NULL,
    transaction_type TEXT NOT NULL,
    amount_band TEXT,
    trade_date TEXT,
    filed_date TEXT,
    score REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','selected','skipped')),
    report_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_daily_picks_date ON daily_picks(pick_date);
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
    """Apply additive schema changes to an existing database.

    The bot runs against a live DB with real rows, so new columns are added
    via ALTER TABLE rather than a schema rewrite. Each change checks
    PRAGMA table_info first, making this idempotent and safe to run on
    every startup.
    """
    holdings_columns = {row["name"] for row in conn.execute("PRAGMA table_info(holdings)")}

    if "sell_price" not in holdings_columns:
        conn.execute("ALTER TABLE holdings ADD COLUMN sell_price REAL")
    if "sell_date" not in holdings_columns:
        conn.execute("ALTER TABLE holdings ADD COLUMN sell_date TEXT")

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


# --- Price history ---

def insert_price_snapshot(conn: sqlite3.Connection, ticker: str, price: float) -> None:
    conn.execute(
        "INSERT INTO price_history (ticker, price, fetched_at) VALUES (?, ?, ?)",
        (ticker, price, utcnow_iso()),
    )
    conn.commit()


def prune_price_history(conn: sqlite3.Connection, older_than_hours: int = 36) -> int:
    cutoff = datetime.now(timezone.utc).timestamp() - older_than_hours * 3600
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    cursor = conn.execute("DELETE FROM price_history WHERE fetched_at < ?", (cutoff_iso,))
    conn.commit()
    return cursor.rowcount


# --- Alert state ---

def get_alert_state(conn: sqlite3.Connection, ticker: str, alert_type: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM alert_state WHERE ticker = ? AND alert_type = ?",
        (ticker, alert_type),
    ).fetchone()


def clear_alert_state(conn: sqlite3.Connection, ticker: str, alert_type: str) -> None:
    """Delete a ticker's alert state entirely.

    Called when a stock leaves the watchlist or is sold. Without this, a row
    left at in_alert = 1 would persist, and re-adding that stock later would
    start it already "in alert" — silently suppressing the first real alert.
    """
    conn.execute(
        "DELETE FROM alert_state WHERE ticker = ? AND alert_type = ?",
        (ticker, alert_type),
    )
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


# --- Politician trades: processed filings (idempotency guard) ---

def is_filing_processed(conn: sqlite3.Connection, doc_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed_filings WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    return row is not None


def mark_filing_processed(conn: sqlite3.Connection, doc_id: str, source: str) -> None:
    """Record a filing as processed. Safe to call twice for the same doc_id —
    the digest job may re-run after a crash and must not choke on it."""
    conn.execute(
        """INSERT INTO processed_filings (doc_id, source, processed_at)
           VALUES (?, ?, ?)
           ON CONFLICT(doc_id) DO NOTHING""",
        (doc_id, source, utcnow_iso()),
    )
    conn.commit()


# --- Politician trades: daily picks ---

def save_daily_picks(conn: sqlite3.Connection, pick_date: str, picks: list[dict]) -> None:
    """Persist a day's ranked shortlist, each starting at status='pending'."""
    for pick in picks:
        conn.execute(
            """INSERT INTO daily_picks
               (pick_date, rank, ticker, company_name, politician_name, chamber,
                transaction_type, amount_band, trade_date, filed_date, score, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                pick_date,
                pick["rank"],
                pick["ticker"],
                pick["company_name"],
                pick["politician_name"],
                pick["chamber"],
                pick["transaction_type"],
                pick.get("amount_band"),
                pick.get("trade_date"),
                pick.get("filed_date"),
                pick["score"],
            ),
        )
    conn.commit()


def get_pending_picks(conn: sqlite3.Connection, pick_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM daily_picks WHERE pick_date = ? AND status = 'pending' ORDER BY rank",
        (pick_date,),
    ).fetchall()


def set_pick_status(
    conn: sqlite3.Connection, pick_id: int, status: str, report_path: str | None = None
) -> None:
    conn.execute(
        "UPDATE daily_picks SET status = ?, report_path = COALESCE(?, report_path) WHERE id = ?",
        (status, report_path, pick_id),
    )
    conn.commit()
