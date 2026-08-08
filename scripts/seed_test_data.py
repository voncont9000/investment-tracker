#!/usr/bin/env python3
"""Manual smoke test for the alert/episode-dedup logic, no Telegram involved.

Seeds a watchlist stock and a held stock, fabricates trailing-change
scenarios via mocking, and prints what check_and_fire_alerts would send —
so the core alert math can be eyeballed without waiting 12 real hours or
wiring up Telegram.

Usage: python scripts/seed_test_data.py
"""

import asyncio
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import alerts, db


async def fake_send(text: str) -> None:
    print(f"  -> WOULD SEND: {text}")


async def main() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)

    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)

    settings = SimpleNamespace(
        trailing_window_hours=12,
        watchlist_drop_threshold=-0.10,
        holding_gain_threshold=0.10,
    )

    print("Scenario 1: AAPL down 15%, TSLA flat -> expect one alert (AAPL)")
    with patch("app.alerts.compute_trailing_change", side_effect=lambda t, h: -0.15 if t == "AAPL" else 0.01):
        await alerts.check_and_fire_alerts(conn, settings, fake_send)

    print("Scenario 2: same poll again, still down 15% -> expect silence (episode dedup)")
    with patch("app.alerts.compute_trailing_change", side_effect=lambda t, h: -0.15 if t == "AAPL" else 0.01):
        await alerts.check_and_fire_alerts(conn, settings, fake_send)

    print("Scenario 3: AAPL recovers to -2%, TSLA up 12% -> expect one alert (TSLA), AAPL clears silently")
    with patch("app.alerts.compute_trailing_change", side_effect=lambda t, h: -0.02 if t == "AAPL" else 0.12):
        await alerts.check_and_fire_alerts(conn, settings, fake_send)

    print("Scenario 4: AAPL drops 20% again -> expect a new episode alert (AAPL)")
    with patch("app.alerts.compute_trailing_change", side_effect=lambda t, h: -0.20 if t == "AAPL" else 0.12):
        await alerts.check_and_fire_alerts(conn, settings, fake_send)

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
