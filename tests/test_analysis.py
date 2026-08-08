"""app.analysis: split_report's parsing, and run_analysis's pause_turn resume
loop, both tested without hitting the real Anthropic API.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def test_run_analysis_returns_text_when_turn_completes_immediately():
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("Report body.")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("app.analysis.anthropic.Anthropic", return_value=mock_client):
        result = analysis.run_analysis("AAPL", "Apple Inc.", "financial data here", api_key="fake-key")

    assert result == "Report body."
    assert mock_client.messages.create.call_count == 1


def test_run_analysis_resumes_on_pause_turn():
    paused_response = SimpleNamespace(stop_reason="pause_turn", content=[_text_block("partial...")])
    final_response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("Full report.")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [paused_response, final_response]

    with patch("app.analysis.anthropic.Anthropic", return_value=mock_client):
        result = analysis.run_analysis("AAPL", "Apple Inc.", "financial data here", api_key="fake-key")

    assert result == "Full report."
    assert mock_client.messages.create.call_count == 2


def test_run_analysis_stops_resuming_after_max_pause_resumes():
    paused_response = SimpleNamespace(stop_reason="pause_turn", content=[_text_block("still going...")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = paused_response

    with patch("app.analysis.anthropic.Anthropic", return_value=mock_client):
        result = analysis.run_analysis("AAPL", "Apple Inc.", "financial data here", api_key="fake-key")

    assert result == "still going..."
    assert mock_client.messages.create.call_count == 1 + analysis.MAX_PAUSE_RESUMES
