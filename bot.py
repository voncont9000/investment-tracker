#!/usr/bin/env python3
"""Entrypoint: builds the Telegram Application, registers handlers, and
schedules the recurring price-poll and daily-cache-refresh jobs. Single
process, single event loop — no separate scheduler or web server needed
(long-polling, no webhook)."""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging

from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from app import alerts, db, handlers, technicals, thesis
from app.config import load_settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DAILY_CACHE_REFRESH_SECONDS = 86400

# PTB's run_daily numbers days 0-6 as Sunday-Saturday (changed from
# Monday-Sunday in PTB 20.0) — confirmed against the installed version
# before relying on it, since getting this wrong would silently schedule
# the sweep on the wrong day with no error.
_SUNDAY = 0
THESIS_SWEEP_TIME = dt.time(hour=18, minute=0, tzinfo=dt.timezone.utc)


def _active_tickers(conn) -> list[str]:
    watchlist_tickers = {row["ticker"] for row in db.list_watchlist(conn)}
    holding_tickers = set(db.list_distinct_holding_tickers(conn))
    return sorted(watchlist_tickers | holding_tickers)


async def daily_cache_refresh_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh the in-memory daily-bar cache used by the entry-point
    setups. Runs once at startup and every 24h after — daily bars barely
    change intraday, so there's no benefit to re-fetching a year of
    history on every 15-minute poll."""
    conn = context.bot_data["conn"]
    tickers = _active_tickers(conn)
    if tickers:
        await asyncio.to_thread(technicals.refresh_daily_cache, tickers)


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check every active ticker against the 5 entry-point setups and
    fire (deduped) alerts."""
    conn = context.bot_data["conn"]
    settings = context.bot_data["settings"]

    async def send(text: str, chart_png: bytes) -> None:
        await context.bot.send_photo(
            chat_id=settings.telegram_chat_id,
            photo=io.BytesIO(chart_png),
            caption=text,
        )

    await alerts.check_and_fire_alerts(conn, settings, send)


async def thesis_sweep_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Weekly sell-thesis check on every holding (app/thesis.py). Text-only
    messages — unlike the price-poll alerts, a news verdict has no chart to
    attach."""
    conn = context.bot_data["conn"]
    settings = context.bot_data["settings"]

    async def send(text: str) -> None:
        await context.bot.send_message(chat_id=settings.telegram_chat_id, text=text)

    await thesis.run_weekly_sweep(conn, settings, send)


def main() -> None:
    settings = load_settings()
    conn = db.get_connection(settings.db_path)
    db.init_db(conn)

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.bot_data["conn"] = conn
    application.bot_data["settings"] = settings

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("list", handlers.list_positions))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text))

    application.job_queue.run_repeating(
        daily_cache_refresh_job,
        interval=DAILY_CACHE_REFRESH_SECONDS,
        first=0,
    )
    application.job_queue.run_repeating(
        poll_job,
        interval=settings.poll_interval_minutes * 60,
        first=10,
    )

    if settings.anthropic_api_key and settings.exa_api_key:
        application.job_queue.run_daily(
            thesis_sweep_job,
            time=THESIS_SWEEP_TIME,
            days=(_SUNDAY,),
        )
    else:
        logger.info(
            "Weekly sell-thesis sweep disabled — set ANTHROPIC_API_KEY and "
            "EXA_API_KEY to enable it."
        )

    logger.info(
        "Starting bot (poll interval: %s min, db: %s)",
        settings.poll_interval_minutes,
        settings.db_path,
    )
    application.run_polling()


if __name__ == "__main__":
    main()
