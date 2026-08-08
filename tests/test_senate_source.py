from datetime import date

from app.politician_trades.sources import senate


def test_fetch_recent_filings_is_a_no_op_stub():
    assert senate.fetch_recent_filings(date(2026, 8, 1)) == []
