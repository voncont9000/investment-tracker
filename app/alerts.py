"""Entry-point and exit alert orchestration: builds technical metrics for
every watchlist/holding ticker, runs the 5 entry setup checks
(app/setups.py) plus — for held tickers only — the 3 P&L rules and 2
technical exit checks (app/exits.py), and fires (deduped) alerts via
`send`.

`send` (the outbound-message callable) is injected rather than imported,
so this module's logic is fully testable with a stub — no Telegram
involved.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app import charts, db, exits, prices, setups, technicals

SendFn = Callable[[str, bytes], Awaitable[None]]

_DOLLAR_METRICS = {"resistance", "current price", "avg cost", "peak"}


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


_DIRECTION_EMOJI = {"BUY": "\U0001f7e2", "SELL": "\U0001f534"}  # 🟢 🔴


def _format_message(ticker: str, match: setups.SetupMatch) -> str:
    # Entry setups (app/setups.py) don't have a direction/soft_tag field
    # and default to "BUY"/"ideal signal"; exit matches (app/exits.py) set
    # their own — "SELL"/"confirmed" reads better for a sell alert.
    direction = getattr(match, "direction", "BUY")
    emoji = _DIRECTION_EMOJI.get(direction, "\U0001f514")

    tags = []
    if match.is_ideal:
        tags.append(getattr(match, "soft_tag", "ideal signal"))
    if match.risk_label:
        tags.append(match.risk_label)
    tag_suffix = f" ({', '.join(tags)})" if tags else ""

    lines = [f"{emoji} {direction} — {ticker} — {match.label}{tag_suffix}"]
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
    match: setups.SetupMatch | exits.ExitMatch | None,
    setup_id: str,
    send_message: bool = True,
) -> None:
    """Apply the "once per episode" dedup rule for one (ticker, alert).

    `send_message=False` still updates alert_state on a match but skips
    the actual send — used to suppress entry alerts on a ticker whose
    exit rules are currently firing (see check_and_fire_alerts) without
    losing the dedup transition, so a suppressed entry doesn't queue up
    and fire the moment the exit condition clears.
    """
    state = db.get_alert_state(conn, ticker, setup_id)
    currently_in_alert = bool(state["in_alert"]) if state is not None else False

    if match is not None and not currently_in_alert:
        if send_message:
            text = _format_message(ticker, match)
            chart_png = await asyncio.to_thread(charts.render_price_chart, metrics)
            await send(text, chart_png)
        db.set_in_alert(conn, ticker, setup_id, True)
    elif match is None and currently_in_alert:
        db.set_in_alert(conn, ticker, setup_id, False)


async def check_and_fire_alerts(conn, settings, send: SendFn) -> None:
    """Check every active watchlist/holding ticker against the 5 entry-
    point setups, plus — for held tickers — the P&L rules and 2 technical
    exit setups (app/exits.py), and fire (deduped) alerts via `send`.

    A held ticker whose tape currently satisfies any exit rule suppresses
    that poll's entry messages for the same ticker: the tape reason is
    often literally the same (entry Setup 1, "uptrend now pulling back",
    is also the start of a trend break), and one coherent message beats
    two contradictory ones arriving together.
    """
    watchlist_tickers = {row["ticker"] for row in db.list_watchlist(conn)}
    holding_tickers = set(db.list_distinct_holding_tickers(conn))
    tickers = sorted(watchlist_tickers | holding_tickers)
    avg_costs = db.avg_cost_by_ticker(conn)

    for ticker in tickers:
        metrics = await asyncio.to_thread(build_ticker_metrics, ticker)
        if metrics is None:
            continue

        exit_results: list[tuple[str, exits.ExitMatch | None]] = []
        if ticker in holding_tickers:
            exit_results = exits.check_all_exits(
                metrics,
                avg_costs[ticker],
                settings.take_profit_pct,
                settings.stop_loss_pct,
                settings.trailing_stop_pct,
            )
            for exit_id, match in exit_results:
                await _handle_setup_match(conn, send, ticker, metrics, match, exit_id)

        suppress_entries = any(match is not None for _, match in exit_results)
        for setup_id, check in setups.ALL_SETUPS:
            match = check(metrics)
            await _handle_setup_match(
                conn, send, ticker, metrics, match, setup_id, send_message=not suppress_entries
            )
