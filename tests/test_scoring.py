"""Deterministic scoring/ranking of politician trades. Pure Python, no
network — mirrors the style of tests/test_alerts.py."""

from app.politician_trades import scoring


def _trade(**overrides) -> dict:
    base = {
        "ticker": "AAPL",
        "transaction_type": "purchase",
        "amount_band": "$15,001 - $50,000",
        "trade_date": "2026-08-01",
        "filed_date": "2026-08-08",
        "politician_name": "Jane Smith",
        "chamber": "House",
    }
    base.update(overrides)
    return base


# --- Amount band scoring ---

def test_amount_band_score_increases_with_band_size():
    small = scoring.amount_band_score("$1,001 - $15,000")
    large = scoring.amount_band_score("$500,001 - $1,000,000")
    assert large > small


def test_amount_band_score_unknown_band_returns_zero():
    assert scoring.amount_band_score("not a real band") == 0.0


def test_amount_band_score_none_returns_zero():
    assert scoring.amount_band_score(None) == 0.0


# --- Freshness scoring ---

def test_freshness_score_higher_for_faster_filing():
    fast = scoring.freshness_score(trade_date="2026-08-07", filed_date="2026-08-08")
    slow = scoring.freshness_score(trade_date="2026-06-01", filed_date="2026-08-08")
    assert fast > slow


def test_freshness_score_missing_dates_returns_zero():
    assert scoring.freshness_score(trade_date=None, filed_date="2026-08-08") == 0.0
    assert scoring.freshness_score(trade_date="2026-08-07", filed_date=None) == 0.0


# --- Ranking purchases ---

def test_rank_purchases_excludes_sales():
    trades = [_trade(ticker="AAPL"), _trade(ticker="TSLA", transaction_type="sale")]
    ranked = scoring.rank_purchases(trades)
    tickers = [r["ticker"] for r in ranked]
    assert "AAPL" in tickers
    assert "TSLA" not in tickers


def test_rank_purchases_orders_by_score_descending():
    trades = [
        _trade(ticker="SMALL", amount_band="$1,001 - $15,000"),
        _trade(ticker="BIG", amount_band="$500,001 - $1,000,000"),
    ]
    ranked = scoring.rank_purchases(trades)
    assert [r["ticker"] for r in ranked] == ["BIG", "SMALL"]


def test_rank_purchases_cluster_bonus_for_shared_ticker():
    trades = [
        _trade(ticker="AAPL", politician_name="Jane Smith"),
        _trade(ticker="AAPL", politician_name="John Doe"),
        _trade(ticker="TSLA", politician_name="Jane Smith"),
    ]
    ranked = scoring.rank_purchases(trades)
    aapl = next(r for r in ranked if r["ticker"] == "AAPL")
    tsla = next(r for r in ranked if r["ticker"] == "TSLA")
    # Same amount band and dates otherwise — AAPL wins purely on the
    # multi-politician cluster bonus.
    assert aapl["score"] > tsla["score"]
    assert aapl["politician_count"] == 2


def test_rank_purchases_returns_empty_for_no_trades():
    assert scoring.rank_purchases([]) == []


def test_rank_purchases_dedupes_multiple_lots_of_same_politician_same_ticker():
    # Same politician buying the same ticker twice in the batch shouldn't
    # count as two "different people" for the cluster bonus.
    trades = [
        _trade(ticker="AAPL", politician_name="Jane Smith"),
        _trade(ticker="AAPL", politician_name="Jane Smith"),
    ]
    ranked = scoring.rank_purchases(trades)
    assert len(ranked) == 1
    assert ranked[0]["politician_count"] == 1


def test_top_n_picks_limits_to_three_by_default():
    trades = [_trade(ticker=t) for t in ("A", "B", "C", "D")]
    ranked = scoring.rank_purchases(trades)
    top = scoring.top_n_picks(ranked, n=3)
    assert len(top) == 3


def test_top_n_picks_returns_fewer_when_fewer_available():
    trades = [_trade(ticker="A")]
    ranked = scoring.rank_purchases(trades)
    top = scoring.top_n_picks(ranked, n=3)
    assert len(top) == 1
