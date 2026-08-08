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
class PriceSnapshot:
    id: int
    ticker: str
    price: float
    fetched_at: str


@dataclass
class AlertState:
    ticker: str
    alert_type: str  # "watchlist_drop" | "holding_gain"
    in_alert: bool
    last_alerted_at: str | None
