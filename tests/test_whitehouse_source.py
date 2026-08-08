"""President Trump's Periodic Transaction Reports (OGE Form 278-T),
published at whitehouse.gov/disclosures/ — free, official, no API key.
Mocked against requests so no network is used."""

from datetime import date
from unittest.mock import MagicMock, patch

from app.politician_trades.sources import whitehouse

_SAMPLE_HTML = """
<html><body>
<a href="https://www.whitehouse.gov/wp-content/uploads/2026/04/President-Donald-J.-Trump-Periodic-Transaction-Report-4.20.26.pdf">President Donald J. Trump Periodic Transaction Report 4.20.26</a>
<a href="https://www.whitehouse.gov/wp-content/uploads/2025/11/Kenny-Stephen-Periodic-Transaction-Report-04.03.25.pdf">Kenny, Stephen Periodic Transaction Report</a>
<a href="https://www.whitehouse.gov/wp-content/uploads/2026/01/Trump-Donald-J-2025-Annual-278.pdf">Trump, Donald J. 2025 Annual Report</a>
</body></html>
"""


def _mock_response(html: str) -> MagicMock:
    response = MagicMock()
    response.text = html
    response.raise_for_status = MagicMock()
    return response


def test_fetch_recent_filings_returns_only_trump_ptrs():
    with patch("app.politician_trades.sources.whitehouse.requests.get", return_value=_mock_response(_SAMPLE_HTML)):
        filings = whitehouse.fetch_recent_filings(date(2026, 1, 1))

    assert len(filings) == 1
    assert "Trump" in filings[0].politician_name
    assert filings[0].chamber == "Executive"
    assert filings[0].pdf_url.endswith("4.20.26.pdf")


def test_fetch_recent_filings_excludes_annual_reports_and_other_officials():
    with patch("app.politician_trades.sources.whitehouse.requests.get", return_value=_mock_response(_SAMPLE_HTML)):
        filings = whitehouse.fetch_recent_filings(date(2026, 1, 1))

    urls = [f.pdf_url for f in filings]
    assert not any("Annual" in u for u in urls)
    assert not any("Kenny" in u for u in urls)


def test_fetch_recent_filings_excludes_old_filings():
    with patch("app.politician_trades.sources.whitehouse.requests.get", return_value=_mock_response(_SAMPLE_HTML)):
        filings = whitehouse.fetch_recent_filings(date(2026, 5, 1))

    assert filings == []


def test_fetch_recent_filings_network_failure_returns_empty():
    with patch("app.politician_trades.sources.whitehouse.requests.get", side_effect=Exception("down")):
        filings = whitehouse.fetch_recent_filings(date(2026, 1, 1))
    assert filings == []


def test_fetch_recent_filings_parses_dotted_date_in_filename():
    html = (
        '<a href="https://www.whitehouse.gov/wp-content/uploads/2026/05/'
        'President-Donald-J.-Trump-Periodic-Transaction-Report-05.08.26-1.pdf">'
        "President Donald J. Trump Periodic Transaction Report 05.08.26 (1)</a>"
    )
    with patch("app.politician_trades.sources.whitehouse.requests.get", return_value=_mock_response(html)):
        filings = whitehouse.fetch_recent_filings(date(2026, 1, 1))

    assert len(filings) == 1
    assert filings[0].filed_date == "2026-05-08"


def test_fetch_recent_filings_deduplicates_by_url():
    html = _SAMPLE_HTML + (
        '<a href="https://www.whitehouse.gov/wp-content/uploads/2026/04/'
        'President-Donald-J.-Trump-Periodic-Transaction-Report-4.20.26.pdf">duplicate link text</a>'
    )
    with patch("app.politician_trades.sources.whitehouse.requests.get", return_value=_mock_response(html)):
        filings = whitehouse.fetch_recent_filings(date(2026, 1, 1))

    assert len(filings) == 1
