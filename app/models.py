"""Plain dataclasses mirroring the SQLite schema for typed access in app code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WatchlistItem:
    id: int
    ticker: str
    company_name: str
    date_added: str
    active: bool


@dataclass
class Holding:
    id: int
    ticker: str
    company_name: str
    purchase_price: float
    purchase_date: str
    active: bool


@dataclass
class AlertState:
    ticker: str
    alert_type: str  # one of the 5 setup ids in app.setups.ALL_SETUPS
    in_alert: bool
    last_alerted_at: str | None
