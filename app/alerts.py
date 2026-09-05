"""Entry-point alert orchestration: builds technical metrics for every
watchlist/holding ticker, runs the 5 setup checks (app/setups.py), and
fires (deduped) alerts via `send`.

`send` (the outbound-message callable) is injected rather than imported,
so this module's logic is fully testable with a stub — no Telegram
involved.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app import charts, db, prices, setups, technicals

SendFn = Callable[[str, bytes], Awaitable[None]]

_DOLLAR_METRICS = {"resistance", "current price"}


def build_ticker_metrics(ticker: str) -> technicals.TickerMetrics | None:
    """Combine the cached daily bars with a fresh live price into a
    TickerMetrics, or None if either is unavailable."""
    bars = technicals.get_cached_daily_bars(ticker)
    if bars is None:
        # Not yet covered by the daily refresh job (e.g. just added) —
        # fetch it now rather than silently going alert-less for up to 24h.
        technicals.refresh_daily_cache([ticker])
        bars = technicals.get_cached_daily_bars(ticker)
    if bars is None:
        return None
    live = prices.fetch_live_price(ticker)
    if live is None:
        return None
    daily_closes, daily_lows = bars
    current_price, today_open, today_intraday_low = live
    return technicals.build_metrics(
        ticker, daily_closes, daily_lows, current_price, today_open, today_intraday_low
    )


def _format_message(ticker: str, match: setups.SetupMatch) -> str:
    tags = []
    if match.is_ideal:
        tags.append("ideal signal")
    if match.risk_label:
        tags.append(match.risk_label)
    tag_suffix = f" ({', '.join(tags)})" if tags else ""

    lines = [f"\U0001f514 {ticker} — {match.label}{tag_suffix}"]
    for name, value in match.numbers.items():
        if name in _DOLLAR_METRICS:
            lines.append(f"{name}: {value:.2f}")
        else:
            lines.append(f"{name}: {value:.1%}")
    if match.ideal_reasons:
        lines.append("Also: " + "; ".join(match.ideal_reasons))
    return "\n".join(lines)


async def _handle_setup_match(
    conn,
    send: SendFn,
    ticker: str,
    metrics: technicals.TickerMetrics,
    match: setups.SetupMatch | None,
    setup_id: str,
) -> None:
    """Apply the "once per episode" dedup rule for one (ticker, setup)."""
    state = db.get_alert_state(conn, ticker, setup_id)
    currently_in_alert = bool(state["in_alert"]) if state is not None else False

    if match is not None and not currently_in_alert:
        text = _format_message(ticker, match)
        chart_png = await asyncio.to_thread(charts.render_price_chart, metrics)
        await send(text, chart_png)
        db.set_in_alert(conn, ticker, setup_id, True)
    elif match is None and currently_in_alert:
        db.set_in_alert(conn, ticker, setup_id, False)


async def check_and_fire_alerts(conn, send: SendFn) -> None:
    """Check every active watchlist/holding ticker against all 5 entry-
    point setups and fire (deduped) alerts via `send`."""
    watchlist_tickers = {row["ticker"] for row in db.list_watchlist(conn)}
    holding_tickers = set(db.list_distinct_holding_tickers(conn))
    tickers = sorted(watchlist_tickers | holding_tickers)

    for ticker in tickers:
        metrics = await asyncio.to_thread(build_ticker_metrics, ticker)
        if metrics is None:
            continue
        for setup_id, check in setups.ALL_SETUPS:
            match = check(metrics)
            await _handle_setup_match(conn, send, ticker, metrics, match, setup_id)
