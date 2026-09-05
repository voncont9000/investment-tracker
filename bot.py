#!/usr/bin/env python3
"""Entrypoint: builds the Telegram Application, registers handlers, and
schedules the recurring price-poll job. Single process, single event loop —
no separate scheduler or web server needed (long-polling, no webhook)."""

from __future__ import annotations

import asyncio
import logging

from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from app import alerts, db, handlers
from app.config import load_settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check for threshold breaches and fire alerts."""
    conn = context.bot_data["conn"]
    settings = context.bot_data["settings"]

    async def send(text: str) -> None:
        await context.bot.send_message(chat_id=settings.telegram_chat_id, text=text)

    await alerts.check_and_fire_alerts(conn, settings, send)


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
        poll_job,
        interval=settings.poll_interval_minutes * 60,
        first=10,
    )

    logger.info(
        "Starting bot (poll interval: %s min, db: %s)",
        settings.poll_interval_minutes,
        settings.db_path,
    )
    application.run_polling()


if __name__ == "__main__":
    main()
