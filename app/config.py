"""Loads configuration from the environment (.env) into a Settings object."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: int
    db_path: str
    poll_interval_minutes: int
    anthropic_api_key: str | None
    take_profit_pct: float
    stop_loss_pct: float
    trailing_stop_pct: float
    exa_api_key: str | None


def load_settings() -> Settings:
    """Load settings from environment variables (via .env if present).

    Raises ValueError if a required variable is missing, so misconfiguration
    fails loudly at startup rather than silently later.
    """
    load_dotenv()

    def require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise ValueError(f"Missing required environment variable: {name}")
        return value

    return Settings(
        telegram_bot_token=require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=int(require("TELEGRAM_CHAT_ID")),
        db_path=os.environ.get("DB_PATH", "data/tracker.db"),
        poll_interval_minutes=int(os.environ.get("POLL_INTERVAL_MINUTES", "15")),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        # Sell-alert P&L rules (app/exits.py) — three numbers, so .env-
        # configurable unlike the ~40 hardcoded entry-setup thresholds.
        take_profit_pct=float(os.environ.get("TAKE_PROFIT_PCT", "0.30")),
        stop_loss_pct=float(os.environ.get("STOP_LOSS_PCT", "0.20")),
        trailing_stop_pct=float(os.environ.get("TRAILING_STOP_PCT", "0.15")),
        # Needed for the weekly thesis-check sweep (app/thesis.py). Leave
        # unset and the sweep simply doesn't run — every other command,
        # including the other sell alerts, works with no key.
        exa_api_key=os.environ.get("EXA_API_KEY") or None,
    )
