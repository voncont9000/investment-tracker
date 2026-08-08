"""Trade-source adapter interface.

A TradeSource only fetches the raw list of recently-filed disclosures —
turning a filing's PDF into structured trade lines (ticker, buy/sell,
amount) is a separate step (pdf_extract.py), since that costs an Anthropic
call and should only run for filings that pass the date-window/dedup filter.

New sources (e.g. a paid Senate provider) just implement this interface;
nothing else in digest.py needs to change. See sources/senate.py for why
there isn't a free Senate source today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class RawFiling:
    doc_id: str
    chamber: str  # "House" | "Senate" | "Executive"
    politician_name: str
    filed_date: str  # ISO date (YYYY-MM-DD)
    pdf_url: str


class TradeSource(Protocol):
    def fetch_recent_filings(self, since: date) -> list[RawFiling]: ...
