"""House Clerk periodic-transaction-report (PTR) index — free, official,
no API key. Confirmed live: the House Clerk's own site (200, daily-refreshed
ZIP) works; House Stock Watcher's mirror (403) and every other free
aggregator we tried do not (see the design doc for what was tested).

The index is one ZIP per year containing every financial-disclosure filing
(not just PTRs) — filter to FilingType == "P" for stock trades.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime

import requests

from .base import RawFiling

_INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
_USER_AGENT = "Mozilla/5.0"
_TIMEOUT_SECONDS = 30


def fetch_recent_filings(since: date) -> list[RawFiling]:
    """Every Periodic Transaction Report filed on or after `since`.

    Checks both `since.year` and the current year's index — the only case
    they differ is a job run in the first couple of days of January, where
    `since` (today minus a couple of days) can fall into the prior year.
    """
    filings: dict[str, RawFiling] = {}
    for year in sorted({since.year, date.today().year}):
        for filing in _fetch_year_index(year, since):
            filings[filing.doc_id] = filing
    return list(filings.values())


def _fetch_year_index(year: int, since: date) -> list[RawFiling]:
    try:
        response = requests.get(
            _INDEX_URL.format(year=year),
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception:
        return []

    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            xml_bytes = zf.read(f"{year}FD.xml")
    except Exception:
        return []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    filings: list[RawFiling] = []
    for member in root.findall("Member"):
        if (member.findtext("FilingType") or "") != "P":
            continue
        filed_date = _parse_filing_date(member.findtext("FilingDate"))
        if filed_date is None or filed_date < since:
            continue
        doc_id = member.findtext("DocID") or ""
        if not doc_id:
            continue
        last = member.findtext("Last") or ""
        first = member.findtext("First") or ""
        filings.append(
            RawFiling(
                doc_id=doc_id,
                chamber="House",
                politician_name=f"{first} {last}".strip(),
                filed_date=filed_date.isoformat(),
                pdf_url=_PDF_URL.format(year=year, doc_id=doc_id),
            )
        )
    return filings


def _parse_filing_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError:
        return None
