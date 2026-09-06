import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import db, news, technicals, thesis
from app.config import Settings


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def settings():
    return Settings(
        telegram_bot_token="dummy",
        telegram_chat_id=1,
        db_path=":memory:",
        poll_interval_minutes=15,
        anthropic_api_key="anthropic-key",
        take_profit_pct=0.30,
        stop_loss_pct=0.20,
        trailing_stop_pct=0.15,
        exa_api_key="exa-key",
    )


class FakeSender:
    def __init__(self):
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        self.messages.append(text)


def run(coro):
    return asyncio.run(coro)


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


_FLAT_METRICS = technicals.build_metrics(
    "AAPL",
    [100.0] * technicals.MIN_HISTORY_SESSIONS,
    [99.0] * technicals.MIN_HISTORY_SESSIONS,
    100.0,
    100.0,
    99.0,
)


def _moved_metrics(pct: float):
    closes = [100.0] * (technicals.MIN_HISTORY_SESSIONS - 1) + [100.0]
    current = 100.0 * (1 + pct)
    return technicals.build_metrics("AAPL", closes, [99.0] * len(closes), current, current, current - 1)


def _verdict(kind="HOLD", reason="Nothing notable.", sources=None):
    return thesis.ThesisVerdict(verdict=kind, reason=reason, sources=sources or [])


# ---------- The free gate ----------

def test_gate_checks_when_never_checked_before():
    assert thesis._should_check(None, _FLAT_METRICS, latest_headline_at=None) is True


def test_gate_checks_when_past_the_floor():
    state = {"last_checked_at": _iso(thesis.GATE_FLOOR_DAYS + 1), "last_headline_at": None}
    assert thesis._should_check(state, _FLAT_METRICS, latest_headline_at=None) is True


def test_gate_checks_on_a_large_price_move():
    state = {"last_checked_at": _iso(1), "last_headline_at": None}
    moved = _moved_metrics(0.06)
    assert thesis._should_check(state, moved, latest_headline_at=None) is True


def test_gate_checks_on_a_new_headline():
    state = {"last_checked_at": _iso(1), "last_headline_at": "2026-09-01T00:00:00Z"}
    assert thesis._should_check(state, _FLAT_METRICS, latest_headline_at="2026-09-02T00:00:00Z") is True


def test_gate_skips_a_quiet_holding():
    state = {"last_checked_at": _iso(1), "last_headline_at": "2026-09-02T00:00:00Z"}
    assert thesis._should_check(state, _FLAT_METRICS, latest_headline_at="2026-09-02T00:00:00Z") is False
    assert thesis._should_check(state, _FLAT_METRICS, latest_headline_at=None) is False


def test_gate_skips_a_small_move():
    state = {"last_checked_at": _iso(1), "last_headline_at": None}
    moved = _moved_metrics(0.02)
    assert thesis._should_check(state, moved, latest_headline_at=None) is False


# ---------- Verdict dedup ----------

def test_concern_fires_once_per_episode(conn):
    sender = FakeSender()
    run(thesis._handle_verdict(conn, sender.send, "AAPL", _verdict("CONCERN", "Guidance cut.")))
    run(thesis._handle_verdict(conn, sender.send, "AAPL", _verdict("CONCERN", "Guidance cut.")))
    assert len(sender.messages) == 1
    assert "Guidance cut." in sender.messages[0]


def test_concern_message_is_labeled_sell(conn):
    sender = FakeSender()
    run(thesis._handle_verdict(conn, sender.send, "AAPL", _verdict("CONCERN", "Guidance cut.")))
    assert "SELL" in sender.messages[0]


def test_hold_never_sends_a_message(conn):
    sender = FakeSender()
    run(thesis._handle_verdict(conn, sender.send, "AAPL", _verdict("HOLD")))
    assert sender.messages == []


def test_concern_clears_silently_and_can_refire(conn):
    sender = FakeSender()
    run(thesis._handle_verdict(conn, sender.send, "AAPL", _verdict("CONCERN")))
    assert len(sender.messages) == 1

    run(thesis._handle_verdict(conn, sender.send, "AAPL", _verdict("HOLD")))
    assert len(sender.messages) == 1  # cleared silently

    run(thesis._handle_verdict(conn, sender.send, "AAPL", _verdict("CONCERN")))
    assert len(sender.messages) == 2  # new episode


# ---------- _get_verdict (the actual Claude call) ----------

def test_get_verdict_parses_the_response_and_uses_the_output_format():
    parsed = _verdict("CONCERN", "Missed earnings.", ["https://x"])
    response = SimpleNamespace(content=[SimpleNamespace(parsed_output=parsed)])
    mock_client = MagicMock()
    mock_client.messages.parse.return_value = response

    with patch("app.thesis.anthropic.Anthropic", return_value=mock_client):
        result = thesis._get_verdict(
            "fake-key", "AAPL", "Apple Inc.", "2026-01-01T00:00:00+00:00", 100.0, 90.0,
            [news.NewsItem(title="T", url="https://x", published_date="2026-09-01", text="body")],
        )

    assert result is parsed
    _, kwargs = mock_client.messages.parse.call_args
    assert kwargs["output_format"] is thesis.ThesisVerdict
    assert kwargs["model"] == thesis.MODEL
    prompt = kwargs["messages"][0]["content"]
    assert "AAPL" in prompt
    assert "https://x" in prompt


def test_get_verdict_reports_no_news_when_list_is_empty():
    parsed = _verdict("HOLD")
    response = SimpleNamespace(content=[SimpleNamespace(parsed_output=parsed)])
    mock_client = MagicMock()
    mock_client.messages.parse.return_value = response

    with patch("app.thesis.anthropic.Anthropic", return_value=mock_client):
        thesis._get_verdict("fake-key", "AAPL", "Apple Inc.", "2026-01-01T00:00:00+00:00", 100.0, 90.0, [])

    _, kwargs = mock_client.messages.parse.call_args
    prompt = kwargs["messages"][0]["content"]
    assert "none found" in prompt.lower()


# ---------- The weekly sweep ----------

def test_sweep_does_nothing_without_an_anthropic_key(conn, settings):
    settings = Settings(**{**settings.__dict__, "anthropic_api_key": None})
    sender = FakeSender()
    with patch("app.thesis._get_verdict") as mock_get_verdict:
        run(thesis.run_weekly_sweep(conn, settings, sender.send))
    mock_get_verdict.assert_not_called()


def test_sweep_does_nothing_without_an_exa_key(conn, settings):
    settings = Settings(**{**settings.__dict__, "exa_api_key": None})
    sender = FakeSender()
    with patch("app.thesis._get_verdict") as mock_get_verdict:
        run(thesis.run_weekly_sweep(conn, settings, sender.send))
    mock_get_verdict.assert_not_called()


def test_sweep_skips_a_gated_out_holding(conn, settings):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    db.set_thesis_check_state(conn, "AAPL", _iso(1), "2026-09-02T00:00:00Z")
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=_FLAT_METRICS), \
         patch("app.prices.fetch_latest_headline_time", return_value="2026-09-02T00:00:00Z"), \
         patch("app.news.fetch_recent_news") as mock_fetch_news, \
         patch("app.thesis._get_verdict") as mock_get_verdict:
        run(thesis.run_weekly_sweep(conn, settings, sender.send))

    mock_fetch_news.assert_not_called()
    mock_get_verdict.assert_not_called()
    assert sender.messages == []


def test_sweep_checks_a_gated_in_holding_and_sends_on_concern(conn, settings):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=_FLAT_METRICS), \
         patch("app.prices.fetch_latest_headline_time", return_value="2026-09-02T00:00:00Z"), \
         patch("app.news.fetch_recent_news", return_value=[]), \
         patch("app.thesis._get_verdict", return_value=_verdict("CONCERN", "Missed earnings.", ["https://x"])):
        run(thesis.run_weekly_sweep(conn, settings, sender.send))

    assert len(sender.messages) == 1
    assert "AAPL" in sender.messages[0]
    assert "Missed earnings." in sender.messages[0]

    state = db.get_thesis_check_state(conn, "AAPL")
    assert state is not None
    assert state["last_headline_at"] == "2026-09-02T00:00:00Z"


def test_sweep_averages_cost_across_lots_for_the_prompt(conn, settings):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    db.add_holding(conn, "AAPL", "Apple Inc.", 120.0)
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=_FLAT_METRICS), \
         patch("app.prices.fetch_latest_headline_time", return_value=None), \
         patch("app.news.fetch_recent_news", return_value=[]), \
         patch("app.thesis._get_verdict", return_value=_verdict("HOLD")) as mock_get_verdict:
        run(thesis.run_weekly_sweep(conn, settings, sender.send))

    _, kwargs = mock_get_verdict.call_args
    args = mock_get_verdict.call_args.args
    all_args = args + tuple(kwargs.values())
    assert 110.0 in all_args


def test_sweep_leaves_state_untouched_when_the_verdict_call_fails(conn, settings):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=_FLAT_METRICS), \
         patch("app.prices.fetch_latest_headline_time", return_value=None), \
         patch("app.news.fetch_recent_news", return_value=[]), \
         patch("app.thesis._get_verdict", side_effect=RuntimeError("boom")):
        run(thesis.run_weekly_sweep(conn, settings, sender.send))

    assert sender.messages == []
    assert db.get_thesis_check_state(conn, "AAPL") is None


def test_sweep_skips_a_ticker_with_unavailable_metrics(conn, settings):
    db.add_holding(conn, "AAPL", "Apple Inc.", 100.0)
    sender = FakeSender()

    with patch("app.alerts.build_ticker_metrics", return_value=None), \
         patch("app.thesis._get_verdict") as mock_get_verdict:
        run(thesis.run_weekly_sweep(conn, settings, sender.send))

    mock_get_verdict.assert_not_called()
    assert sender.messages == []
