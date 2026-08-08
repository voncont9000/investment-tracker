"""Senate trade-disclosure source.

The Senate's own efdsearch.senate.gov site actively blocks automated
access (tested live: 403 from an Akamai bot-block, even with a normal
browser User-Agent), and every free third-party mirror that used to cover
the Senate is dead (Senate Stock Watcher's GitHub data hasn't been updated
since 2021; Capitol Trades' internal API returned 503 in testing). Paid
providers exist (Quiver Quantitative, Financial Modeling Prep, ~$75/mo) but
aren't wired up by default — this stays a documented no-op until you decide
it's worth paying for.

To add a real provider: implement fetch_recent_filings() against it,
returning app.politician_trades.sources.base.RawFiling objects, gated
behind a SENATE_SOURCE_PROVIDER setting. Nothing else in the digest
pipeline needs to change — digest.py only depends on the TradeSource
interface in base.py, not on this module specifically.
"""

from __future__ import annotations

from datetime import date

from .base import RawFiling


def fetch_recent_filings(since: date) -> list[RawFiling]:
    return []
