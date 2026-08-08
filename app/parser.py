"""Free-text -> ParsedMessage. No strict command syntax required.

Pattern order matters and is deliberate: the explicit-price purchase form
("bought X for $Y") is checked before the bare form ("bought X"), because
the bare pattern would otherwise swallow the price into the company name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PURCHASE_WITH_PRICE_RE = re.compile(
    r"bought\s+(?P<company>.+?)\s+for\s+\$?(?P<amount>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PURCHASE_RE = re.compile(
    r"^(?:bought|buy|purchased)\s+(?P<company>.+)$",
    re.IGNORECASE,
)
_SELL_RE = re.compile(
    r"^(?:sold|sell)\s+(?P<company>.+)$",
    re.IGNORECASE,
)
_REMOVE_RE = re.compile(
    r"^(?:remove|delete|unwatch|drop|stop\s+watching|stop\s+tracking)\s+(?P<company>.+)$",
    re.IGNORECASE,
)
_WATCHLIST_RE = re.compile(
    r"^(?:watch|add|track|follow)\s+(?P<company>.+)$",
    re.IGNORECASE,
)

# Trailing noise people naturally append; stripped from the captured company
# name so "Remove Apple from my watchlist" resolves the same as "Remove Apple".
_SUFFIX_RE = re.compile(
    r"\s+(?:from\s+(?:my\s+|the\s+)?(?:watchlist|list|portfolio)"
    r"|to\s+(?:my\s+|the\s+)?(?:watchlist|list|portfolio)"
    r"|stock|shares)\s*$",
    re.IGNORECASE,
)


@dataclass
class ParsedMessage:
    intent: str  # "watchlist_add" | "purchase_record" | "sell_record" | "remove_item" | "unknown"
    company_name: str | None
    amount: float | None


def _clean(company: str) -> str:
    return _SUFFIX_RE.sub("", company.strip()).strip()


def parse_message(text: str) -> ParsedMessage:
    text = text.strip()

    # 1. Purchase with an explicit price — most specific, must win over the
    #    bare purchase pattern below.
    match = _PURCHASE_WITH_PRICE_RE.search(text)
    if match:
        company = _clean(match.group("company"))
        amount_str = match.group("amount").replace(",", "")
        try:
            amount = float(amount_str)
        except ValueError:
            return ParsedMessage(intent="unknown", company_name=None, amount=None)
        if company:
            return ParsedMessage(intent="purchase_record", company_name=company, amount=amount)

    # 2. Purchase without a price — the handler fills in the market price.
    match = _PURCHASE_RE.match(text)
    if match:
        company = _clean(match.group("company"))
        if company:
            return ParsedMessage(intent="purchase_record", company_name=company, amount=None)

    # 3. Sale.
    match = _SELL_RE.match(text)
    if match:
        company = _clean(match.group("company"))
        if company:
            return ParsedMessage(intent="sell_record", company_name=company, amount=None)

    # 4. Removal from the watchlist.
    match = _REMOVE_RE.match(text)
    if match:
        company = _clean(match.group("company"))
        if company:
            return ParsedMessage(intent="remove_item", company_name=company, amount=None)

    # 5. Watchlist add.
    match = _WATCHLIST_RE.match(text)
    if match:
        company = _clean(match.group("company"))
        if company:
            return ParsedMessage(intent="watchlist_add", company_name=company, amount=None)

    return ParsedMessage(intent="unknown", company_name=None, amount=None)
