from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import news


def _fake_result(title="Some Headline", url="https://example.com/a", published_date="2026-09-01", text="body text"):
    return SimpleNamespace(title=title, url=url, published_date=published_date, text=text)


def test_fetch_recent_news_returns_items_from_the_response():
    fake_client = MagicMock()
    fake_client.search_and_contents.return_value = SimpleNamespace(
        results=[_fake_result(title="A"), _fake_result(title="B")]
    )

    with patch("app.news.Exa", return_value=fake_client):
        items = news.fetch_recent_news("key", "AAPL", "Apple Inc.", "2026-08-25T00:00:00+00:00")

    assert [item.title for item in items] == ["A", "B"]
    assert items[0].url == "https://example.com/a"
    assert items[0].published_date == "2026-09-01"
    assert items[0].text == "body text"


def test_fetch_recent_news_passes_the_date_filter_and_ticker():
    fake_client = MagicMock()
    fake_client.search_and_contents.return_value = SimpleNamespace(results=[])

    with patch("app.news.Exa", return_value=fake_client):
        news.fetch_recent_news("key", "AAPL", "Apple Inc.", "2026-08-25T00:00:00+00:00")

    args, kwargs = fake_client.search_and_contents.call_args
    assert "AAPL" in args[0]
    assert "Apple Inc." in args[0]
    assert kwargs["start_published_date"] == "2026-08-25T00:00:00+00:00"
    assert kwargs["text"] is True
    assert kwargs["num_results"] == news.NUM_RESULTS


def test_fetch_recent_news_truncates_long_article_text():
    long_text = "x" * 5000
    fake_client = MagicMock()
    fake_client.search_and_contents.return_value = SimpleNamespace(
        results=[_fake_result(text=long_text)]
    )

    with patch("app.news.Exa", return_value=fake_client):
        items = news.fetch_recent_news("key", "AAPL", "Apple Inc.", "2026-08-25T00:00:00+00:00")

    assert len(items[0].text) == news.MAX_TEXT_CHARS


def test_fetch_recent_news_handles_missing_optional_fields():
    fake_client = MagicMock()
    fake_client.search_and_contents.return_value = SimpleNamespace(
        results=[_fake_result(title=None, text=None)]
    )

    with patch("app.news.Exa", return_value=fake_client):
        items = news.fetch_recent_news("key", "AAPL", "Apple Inc.", "2026-08-25T00:00:00+00:00")

    assert items[0].title == ""
    assert items[0].text == ""


def test_fetch_recent_news_returns_empty_list_on_any_error():
    fake_client = MagicMock()
    fake_client.search_and_contents.side_effect = RuntimeError("boom")

    with patch("app.news.Exa", return_value=fake_client):
        items = news.fetch_recent_news("key", "AAPL", "Apple Inc.", "2026-08-25T00:00:00+00:00")

    assert items == []
