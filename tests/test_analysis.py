"""app.analysis: split_report's parsing, and run_analysis's pause_turn resume
loop, both tested without hitting the real Anthropic API.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import analysis


def test_split_report_separates_quick_take_from_full_report():
    text = "This is the quick take.\n\n---\n\n## Full Report\nDetails here."

    quick_take, full_report = analysis.split_report(text)

    assert quick_take == "This is the quick take."
    assert full_report == "## Full Report\nDetails here."


def test_split_report_handles_missing_delimiter():
    text = "No delimiter anywhere in this text."

    quick_take, full_report = analysis.split_report(text)

    assert quick_take == text
    assert full_report == text


class _FakeTextBlock(SimpleNamespace):
    """Stands in for an SDK content block: has .type/.text like the real
    thing, plus model_dump() since _cached_assistant_message() calls it
    when building a resend."""

    def model_dump(self) -> dict:
        return {"type": self.type, "text": self.text}


def _text_block(text: str) -> _FakeTextBlock:
    return _FakeTextBlock(type="text", text=text)


_USAGE = SimpleNamespace(
    input_tokens=100, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0
)


def test_run_analysis_returns_text_when_turn_completes_immediately():
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("Report body.")], usage=_USAGE)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("app.analysis.anthropic.Anthropic", return_value=mock_client):
        result = analysis.run_analysis("AAPL", "Apple Inc.", "financial data here", api_key="fake-key")

    assert result == "Report body."
    assert mock_client.messages.create.call_count == 1


def test_run_analysis_resumes_on_pause_turn():
    paused_response = SimpleNamespace(stop_reason="pause_turn", content=[_text_block("partial...")], usage=_USAGE)
    final_response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("Full report.")], usage=_USAGE)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [paused_response, final_response]

    with patch("app.analysis.anthropic.Anthropic", return_value=mock_client):
        result = analysis.run_analysis("AAPL", "Apple Inc.", "financial data here", api_key="fake-key")

    assert result == "Full report."
    assert mock_client.messages.create.call_count == 2

    # Second call must resend with a cache breakpoint on the last block of
    # the resent assistant turn, not the raw response content.
    second_call_messages = mock_client.messages.create.call_args_list[1].kwargs["messages"]
    resent_assistant_content = second_call_messages[1]["content"]
    assert resent_assistant_content[-1]["cache_control"] == {"type": "ephemeral"}


def test_run_analysis_stops_resuming_after_max_pause_resumes():
    paused_response = SimpleNamespace(
        stop_reason="pause_turn", content=[_text_block("still going...")], usage=_USAGE
    )
    mock_client = MagicMock()
    mock_client.messages.create.return_value = paused_response

    with patch("app.analysis.anthropic.Anthropic", return_value=mock_client):
        result = analysis.run_analysis("AAPL", "Apple Inc.", "financial data here", api_key="fake-key")

    assert result == "still going..."
    assert mock_client.messages.create.call_count == 1 + analysis.MAX_PAUSE_RESUMES


def test_web_fetch_tool_caps_content_tokens_per_page():
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("Report body.")], usage=_USAGE)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("app.analysis.anthropic.Anthropic", return_value=mock_client):
        analysis.run_analysis("AAPL", "Apple Inc.", "financial data here", api_key="fake-key")

    tools = mock_client.messages.create.call_args.kwargs["tools"]
    web_fetch = next(t for t in tools if t["name"] == "web_fetch")
    assert web_fetch["max_content_tokens"] == analysis.WEB_FETCH_MAX_CONTENT_TOKENS


def test_estimate_cost_usd_matches_sonnet_5_list_pricing():
    totals = {
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    assert analysis._estimate_cost_usd(totals) == pytest.approx(3.0)

    totals = {
        "input_tokens": 0,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    assert analysis._estimate_cost_usd(totals) == pytest.approx(15.0)

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1_000_000,
    }
    assert analysis._estimate_cost_usd(totals) == pytest.approx(0.3)
