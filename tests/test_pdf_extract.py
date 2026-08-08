"""PDF -> structured trades, mocked against both requests (PDF download)
and the Anthropic client (extraction) — mirrors the mocking style in
tests/test_analysis.py and tests/test_ticker_resolver.py."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.politician_trades import pdf_extract


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _mock_pdf_response() -> MagicMock:
    response = MagicMock()
    response.content = b"%PDF-1.4 fake pdf bytes"
    response.raise_for_status = MagicMock()
    return response


def test_extract_trades_returns_parsed_trade_list():
    trades = [
        {
            "asset_description": "Apple Inc.",
            "transaction_type": "purchase",
            "amount_band": "$15,001 - $50,000",
            "trade_date": "2026-07-28",
        }
    ]
    response = SimpleNamespace(content=[_text_block(json.dumps({"trades": trades}))])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("app.politician_trades.pdf_extract.requests.get", return_value=_mock_pdf_response()), \
         patch("app.politician_trades.pdf_extract.anthropic.Anthropic", return_value=mock_client):
        result = pdf_extract.extract_trades("https://example.com/filing.pdf", api_key="fake-key")

    assert result == trades


def test_extract_trades_pdf_download_failure_returns_empty():
    with patch("app.politician_trades.pdf_extract.requests.get", side_effect=Exception("network down")):
        result = pdf_extract.extract_trades("https://example.com/filing.pdf", api_key="fake-key")
    assert result == []


def test_extract_trades_anthropic_failure_returns_empty():
    with patch("app.politician_trades.pdf_extract.requests.get", return_value=_mock_pdf_response()), \
         patch("app.politician_trades.pdf_extract.anthropic.Anthropic", side_effect=Exception("api down")):
        result = pdf_extract.extract_trades("https://example.com/filing.pdf", api_key="fake-key")
    assert result == []


def test_extract_trades_malformed_json_returns_empty():
    response = SimpleNamespace(content=[_text_block("not valid json")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("app.politician_trades.pdf_extract.requests.get", return_value=_mock_pdf_response()), \
         patch("app.politician_trades.pdf_extract.anthropic.Anthropic", return_value=mock_client):
        result = pdf_extract.extract_trades("https://example.com/filing.pdf", api_key="fake-key")

    assert result == []


def test_extract_trades_no_text_block_returns_empty():
    response = SimpleNamespace(content=[])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("app.politician_trades.pdf_extract.requests.get", return_value=_mock_pdf_response()), \
         patch("app.politician_trades.pdf_extract.anthropic.Anthropic", return_value=mock_client):
        result = pdf_extract.extract_trades("https://example.com/filing.pdf", api_key="fake-key")

    assert result == []


def test_extract_trades_sends_pdf_as_base64_document_block():
    response = SimpleNamespace(content=[_text_block(json.dumps({"trades": []}))])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("app.politician_trades.pdf_extract.requests.get", return_value=_mock_pdf_response()), \
         patch("app.politician_trades.pdf_extract.anthropic.Anthropic", return_value=mock_client):
        pdf_extract.extract_trades("https://example.com/filing.pdf", api_key="fake-key")

    call_kwargs = mock_client.messages.create.call_args.kwargs
    content_blocks = call_kwargs["messages"][0]["content"]
    document_block = next(b for b in content_blocks if b["type"] == "document")
    assert document_block["source"]["type"] == "base64"
    assert document_block["source"]["media_type"] == "application/pdf"
    assert call_kwargs["model"] == pdf_extract.MODEL
