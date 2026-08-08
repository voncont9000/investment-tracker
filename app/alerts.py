"""Trailing-change computation and threshold/episode-dedup alert logic.

`send` (the outbound-message callable) is injected rather than imported, so
this module's logic is fully testable with a stub — no Telegram involved.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app import db, prices

SendFn = Callable[[str], Awaitable[None]]


def compute_trailing_change(ticker: str, hours: int) -> float | None:
    """Return (current - baseline) / baseline, or None if either price is
    unavailable or the baseline is zero (can't divide)."""
    current = prices.get_current_price(ticker)
    baseline = prices.get_price_n_hours_ago(ticker, hours)
    if current is None or baseline is None or baseline == 0:
        return None
    return (current - baseline) / baseline


async def _handle_ticker_alert(
    conn,
    send: SendFn,
    ticker: str,
    alert_type: str,
    breached: bool,
    change: float,
    hours: int,
    verb: str,
) -> None:
    """Apply the "once per episode" dedup rule for a single ticker/direction.

    Fires (and sends) only on the transition into a breach; clears silently
    on the transition back out, re-arming the alert for a future episode.
    """
    state = db.get_alert_state(conn, ticker, alert_type)
    currently_in_alert = bool(state["in_alert"]) if state is not None else False

    if breached and not currently_in_alert:
        await send(
            f"\U0001f514 {ticker} has {verb} {abs(change) * 100:.1f}% "
            f"in the last {hours}h."
        )
        db.set_in_alert(conn, ticker, alert_type, True)
    elif not breached and currently_in_alert:
        db.set_in_alert(conn, ticker, alert_type, False)


async def check_and_fire_alerts(conn, settings, send: SendFn) -> None:
    """Check every active watchlist and holding ticker against its
    threshold and fire (deduped) alerts via `send`.

    - Watchlist tickers: alert on a drop <= watchlist_drop_threshold.
    - Holding tickers (deduped across multiple lots of the same ticker):
      alert on a rise >= holding_gain_threshold.
    """
    hours = settings.trailing_window_hours

    watchlist_tickers = [row["ticker"] for row in db.list_watchlist(conn)]
    for ticker in watchlist_tickers:
        change = await asyncio.to_thread(compute_trailing_change, ticker, hours)
        if change is None:
            continue
        breached = change <= settings.watchlist_drop_threshold
        await _handle_ticker_alert(
            conn, send, ticker, "watchlist_drop", breached, change, hours, "dropped"
        )

    holding_tickers = db.list_distinct_holding_tickers(conn)
    for ticker in holding_tickers:
        change = await asyncio.to_thread(compute_trailing_change, ticker, hours)
        if change is None:
            continue
        breached = change >= settings.holding_gain_threshold
        await _handle_ticker_alert(
            conn, send, ticker, "holding_gain", breached, change, hours, "risen"
        )
