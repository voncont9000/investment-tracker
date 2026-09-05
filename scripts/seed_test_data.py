#!/usr/bin/env python3
"""Manual smoke test for the entry-point alert/episode-dedup logic, no
Telegram or yfinance involved.

Fabricates a ticker that matches Setup 1 (Uptrend Pullback), fakes the
metrics-build step, and prints what check_and_fire_alerts would send —
so the setup/dedup logic can be eyeballed without waiting for a real
matching stock or wiring up Telegram.

Usage: python scripts/seed_test_data.py
"""

import asyncio
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import alerts, db, setups, technicals


async def fake_send(text: str, chart_png: bytes) -> None:
    print(f"  -> WOULD SEND ({len(chart_png)} byte chart):")
    for line in text.splitlines():
        print(f"     {line}")


_FAKE_METRICS = technicals.TickerMetrics(
    ticker="AAPL",
    current_price=124.0,
    today_open=124.5,
    today_intraday_low=123.0,
    daily_closes=[100.0] * technicals.MIN_HISTORY_SESSIONS,
    daily_lows=[99.0] * technicals.MIN_HISTORY_SESSIONS,
    sma20=118.0,
    sma50=110.0,
    sma200=100.0,
    sma50_5d_ago=108.0,
    sma200_20d_ago=99.0,
)


def _fake_match():
    return setups.SetupMatch(
        setup_id="setup1_uptrend_pullback",
        label="Uptrend Pullback",
        is_ideal=True,
        ideal_reasons=["3-day return improving"],
        numbers={"30D return": 0.127, "5D return": -0.046},
    )


async def main() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")

    matching = True

    def fake_check(metrics):
        return _fake_match() if matching else None

    with patch("app.alerts.build_ticker_metrics", return_value=_FAKE_METRICS), \
         patch("app.setups.ALL_SETUPS", [("setup1_uptrend_pullback", fake_check)]), \
         patch("app.charts.render_price_chart", return_value=b"FAKE-PNG-BYTES"):

        print("Scenario 1: AAPL matches Setup 1 -> expect one alert")
        await alerts.check_and_fire_alerts(conn, fake_send)

        print("Scenario 2: same poll again, still matching -> expect silence (episode dedup)")
        await alerts.check_and_fire_alerts(conn, fake_send)

        matching = False
        print("Scenario 3: AAPL no longer matches -> clears silently")
        await alerts.check_and_fire_alerts(conn, fake_send)

        matching = True
        print("Scenario 4: AAPL matches again -> expect a new episode alert")
        await alerts.check_and_fire_alerts(conn, fake_send)

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
