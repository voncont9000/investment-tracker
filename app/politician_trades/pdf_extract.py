"""PDF -> structured trade lines, via Claude Haiku (native PDF document
input + structured outputs) rather than an OCR library.

Tested live against a real House Clerk PTR: the PDFs are scanned images
with no extractable text (decompressing every content stream in a sample
filing found zero text-drawing operators) — so a Python-only OCR pipeline
would mean adding pytesseract plus a system poppler/tesseract install.
Sending the PDF straight to Claude, which reads scanned documents natively,
avoids that dependency entirely and costs a fraction of a cent per filing
at Haiku 4.5 rates ($1/$5 per Mtok).

Same error-swallowing style as app/fundamentals.py — a filing that fails to
download or parse is skipped, never crashes the morning digest.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import anthropic
import requests

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 2048
_TIMEOUT_SECONDS = 30

_SCHEMA = {
    "type": "object",
    "properties": {
        "trades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset_description": {"type": "string"},
                    "transaction_type": {
                        "type": "string",
                        "enum": ["purchase", "sale", "exchange"],
                    },
                    "amount_band": {"type": "string"},
                    "trade_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                },
                "required": ["asset_description", "transaction_type", "amount_band", "trade_date"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["trades"],
    "additionalProperties": False,
}

_PROMPT = (
    "This is a scanned U.S. congressional Periodic Transaction Report (PTR). "
    "Extract every individual asset transaction line from the table. For "
    'transaction_type, use "purchase" for P/Purchase, "sale" for S/Sale '
    '(partial or full), and "exchange" for anything else. Use the exact '
    "dollar-range text from the amount column for amount_band (e.g. "
    '"$1,001 - $15,000"). If a field genuinely is not present, use an empty '
    "string rather than guessing."
)


def _fetch_pdf_base64(pdf_url: str) -> str | None:
    try:
        response = requests.get(
            pdf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=_TIMEOUT_SECONDS
        )
        response.raise_for_status()
    except Exception:
        return None
    return base64.standard_b64encode(response.content).decode("utf-8")


def extract_trades(pdf_url: str, api_key: str) -> list[dict[str, Any]]:
    """Download a PTR PDF and extract its trade lines.

    Never raises — returns [] on any download, API, or parse failure, so
    one bad filing can't take down the whole morning digest.
    """
    pdf_b64 = _fetch_pdf_base64(pdf_url)
    if pdf_b64 is None:
        return []

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": _PROMPT},
                    ],
                }
            ],
        )
    except Exception:
        logger.exception("PDF trade extraction failed for %s", pdf_url)
        return []

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    return data.get("trades", [])
