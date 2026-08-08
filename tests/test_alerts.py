import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import alerts, db


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def settings():
    return SimpleNamespace(
        trailing_window_hours=12,
        watchlist_drop_threshold=-0.10,
        holding_gain_threshold=0.10,
    )


class FakeSender:
    def __init__(self):
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        self.messages.append(text)


def run(coro):
    return asyncio.run(coro)


def test_watchlist_drop_fires_once_per_episode(conn, settings):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    sender = FakeSender()

    with patch("app.alerts.compute_trailing_change", return_value=-0.15):
        run(alerts.check_and_fire_alerts(conn, settings, sender.send))
        # Still breached on the next poll — must not re-alert.
        run(alerts.check_and_fire_alerts(conn, settings, sender.send))
    assert len(sender.messages) == 1
    assert "AAPL" in sender.messages[0]
    assert "dropped" in sender.messages[0]

    # Recovers back under the threshold — clears silently, no new message.
    with patch("app.alerts.compute_trailing_change", return_value=-0.02):
        run(alerts.check_and_fire_alerts(conn, settings, sender.send))
    assert len(sender.messages) == 1

    # Breaches again — a new episode, must re-alert.
    with patch("app.alerts.compute_trailing_change", return_value=-0.20):
        run(alerts.check_and_fire_alerts(conn, settings, sender.send))
    assert len(sender.messages) == 2


def test_holding_gain_dedupes_across_multiple_lots(conn, settings):
    db.add_holding(conn, "TSLA", "Tesla Inc.", 200.0)
    db.add_holding(conn, "TSLA", "Tesla Inc.", 210.0)
    sender = FakeSender()

    with patch("app.alerts.compute_trailing_change", return_value=0.12):
        run(alerts.check_and_fire_alerts(conn, settings, sender.send))
    # Two lots of the same ticker must not produce two alerts.
    assert len(sender.messages) == 1
    assert "risen" in sender.messages[0]


def test_no_alert_when_change_unavailable(conn, settings):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    sender = FakeSender()

    with patch("app.alerts.compute_trailing_change", return_value=None):
        run(alerts.check_and_fire_alerts(conn, settings, sender.send))
    assert sender.messages == []


def test_no_alert_below_threshold(conn, settings):
    db.add_watchlist_item(conn, "AAPL", "Apple Inc.")
    sender = FakeSender()

    with patch("app.alerts.compute_trailing_change", return_value=-0.05):
        run(alerts.check_and_fire_alerts(conn, settings, sender.send))
    assert sender.messages == []
