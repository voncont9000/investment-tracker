"""House Clerk trade-disclosure source, mocked against requests so no
network is used. Mirrors the mocking style in tests/test_ticker_resolver.py."""

import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from unittest.mock import MagicMock, patch

from app.politician_trades.sources import house_clerk


def _member_xml(last, first, filing_type, filing_date, doc_id) -> str:
    return (
        "<Member>"
        f"<Prefix /><Last>{last}</Last><First>{first}</First><Suffix />"
        f"<FilingType>{filing_type}</FilingType><StateDst>TX01</StateDst>"
        f"<Year>2026</Year><FilingDate>{filing_date}</FilingDate><DocID>{doc_id}</DocID>"
        "</Member>"
    )


def _index_zip(year: int, members_xml: str) -> bytes:
    xml = f'<?xml version="1.0"?><FinancialDisclosure>{members_xml}</FinancialDisclosure>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{year}FD.xml", xml)
    return buf.getvalue()


def _mock_response(content: bytes) -> MagicMock:
    response = MagicMock()
    response.content = content
    response.raise_for_status = MagicMock()
    return response


def test_fetch_recent_filings_returns_only_ptr_type():
    members = _member_xml("Smith", "Jane", "P", "8/8/2026", "111") + _member_xml(
        "Doe", "John", "W", "8/8/2026", "222"
    )
    zip_bytes = _index_zip(2026, members)

    with patch("app.politician_trades.sources.house_clerk.requests.get", return_value=_mock_response(zip_bytes)):
        filings = house_clerk.fetch_recent_filings(date(2026, 8, 1))

    assert len(filings) == 1
    assert filings[0].doc_id == "111"
    assert filings[0].politician_name == "Jane Smith"
    assert filings[0].chamber == "House"


def test_fetch_recent_filings_excludes_old_filings():
    members = _member_xml("Smith", "Jane", "P", "1/1/2026", "111") + _member_xml(
        "Doe", "John", "P", "8/8/2026", "222"
    )
    zip_bytes = _index_zip(2026, members)

    with patch("app.politician_trades.sources.house_clerk.requests.get", return_value=_mock_response(zip_bytes)):
        filings = house_clerk.fetch_recent_filings(date(2026, 8, 1))

    assert [f.doc_id for f in filings] == ["222"]


def test_fetch_recent_filings_builds_pdf_url():
    zip_bytes = _index_zip(2026, _member_xml("Smith", "Jane", "P", "8/8/2026", "111"))

    with patch("app.politician_trades.sources.house_clerk.requests.get", return_value=_mock_response(zip_bytes)):
        filings = house_clerk.fetch_recent_filings(date(2026, 8, 1))

    assert filings[0].pdf_url == "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/111.pdf"


def test_fetch_recent_filings_network_failure_returns_empty():
    with patch(
        "app.politician_trades.sources.house_clerk.requests.get",
        side_effect=Exception("network down"),
    ):
        filings = house_clerk.fetch_recent_filings(date(2026, 8, 1))
    assert filings == []


def test_fetch_recent_filings_malformed_zip_returns_empty():
    with patch(
        "app.politician_trades.sources.house_clerk.requests.get",
        return_value=_mock_response(b"not a zip file"),
    ):
        filings = house_clerk.fetch_recent_filings(date(2026, 8, 1))
    assert filings == []
