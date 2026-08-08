#!/usr/bin/env python3
"""Print your Telegram chat ID.

Reads TELEGRAM_BOT_TOKEN from .env, asks Telegram for recent messages sent
to your bot, and prints the chat ID(s) it finds — so you can paste it into
TELEGRAM_CHAT_ID in .env.

Run this BEFORE starting bot.py: a running bot consumes these updates via
long-polling, which leaves this endpoint empty.

Usage: .venv/bin/python scripts/get_chat_id.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set in .env")
        print()
        print("Fix: open .env and paste the token BotFather gave you, e.g.")
        print("  TELEGRAM_BOT_TOKEN=8123456789:AAHf...")
        return 1

    try:
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates", timeout=10
        )
        data = response.json()
    except Exception as exc:
        print(f"ERROR: couldn't reach Telegram: {exc}")
        return 1

    if not data.get("ok"):
        print(f"ERROR: Telegram rejected the token: {data.get('description')}")
        print()
        print("Fix: double-check TELEGRAM_BOT_TOKEN in .env matches what BotFather sent.")
        return 1

    chats = {}
    for update in data.get("result", []):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat")
        if chat and chat.get("id"):
            name = chat.get("first_name") or chat.get("title") or chat.get("username") or "?"
            chats[chat["id"]] = name

    if not chats:
        print("No messages found yet.")
        print()
        print("Fix: open Telegram, find your bot, send it any message (e.g. 'hi'),")
        print("then run this script again.")
        print()
        print("If you already did that and still see this: bot.py may be running and")
        print("consuming the updates — stop it (Ctrl+C) and try again.")
        return 1

    print("Found your chat ID:")
    print()
    for chat_id, name in chats.items():
        print(f"  TELEGRAM_CHAT_ID={chat_id}      (from messages by: {name})")
    print()
    print("Paste that line into .env, replacing the empty TELEGRAM_CHAT_ID= line.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
