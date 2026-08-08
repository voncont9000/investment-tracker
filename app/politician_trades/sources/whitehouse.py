"""President Trump's Periodic Transaction Reports (OGE Form 278-T), published
at whitehouse.gov/disclosures/ — free, official, no API key.

This is the legitimate free alternative to scraping a paid aggregator's page
for the same underlying data. Quiver Quantitative's "Donald Trump Stock
Trades" tracker is built from these same OGE filings (per their own
methodology write-up), but their site's Terms of Service explicitly forbids
automated access ("Use any robot, spider, or other automatic device... for
any purpose"), and their trade table isn't even server-rendered — it loads
via a robots.txt-disallowed endpoint that requires a paid session. The
President's PTRs are separately published, in full, by the White House
itself, with an open robots.txt and no ToS restricting access.

The President isn't a member of Congress, so these filings don't come
through the House Clerk index — this source is scoped to Trump specifically
(the disclosures page also lists PTRs for many White House staff, which are
filtered out here since they're out of scope for this feature).

Same scanned-PDF situation as the House Clerk source: these PDFs have no
extractable text, so pdf_extract.py's Claude Haiku pipeline is reused
unchanged.
"""

from __future__ import annotations

import re
from datetime import date

import requests

from .base import RawFiling

_DISCLOSURES_URL = "https://www.whitehouse.gov/disclosures/"
_USER_AGENT = "Mozilla/5.0"
_TIMEOUT_SECONDS = 30

_HREF_RE = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)
# Matches the trailing M.D.YY / MM.DD.YYYY date in filenames like
# "...-4.20.26.pdf" or "...-05.08.26-1.pdf" — filenames occasionally carry a
# stray extra number group (e.g. "0.6.25.26-1.pdf"); anchoring to the last
# three dot-separated groups before the extension picks the real date out
# of noise like that rather than failing to parse.
_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})[^.]*\.pdf$", re.IGNORECASE)


def fetch_recent_filings(since: date) -> list[RawFiling]:
    try:
        response = requests.get(
            _DISCLOSURES_URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception:
        return []

    filings: dict[str, RawFiling] = {}
    for url in _HREF_RE.findall(response.text):
        if "trump" not in url.lower():
            continue
        if "periodic-transaction-report" not in url.lower():
            continue
        if "annual" in url.lower():
            continue

        filed_date = _parse_filename_date(url)
        if filed_date is None or filed_date < since:
            continue

        filings[url] = RawFiling(
            doc_id=url.rsplit("/", 1)[-1],
            chamber="Executive",
            politician_name="Donald J. Trump",
            filed_date=filed_date.isoformat(),
            pdf_url=url,
        )

    return list(filings.values())


def _parse_filename_date(url: str) -> date | None:
    match = _DATE_RE.search(url)
    if not match:
        return None
    month, day, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None
