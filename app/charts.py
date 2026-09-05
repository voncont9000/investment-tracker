"""Renders the price chart attached to every entry-point alert. Pure
rendering — takes a TickerMetrics and returns PNG bytes, no I/O beyond
the in-memory buffer.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.technicals import TickerMetrics, rolling_sma

CHART_WINDOW_SESSIONS = 126
_MA_COLORS = {20: "#ff7f0e", 50: "#2ca02c", 200: "#d62728"}


def render_price_chart(metrics: TickerMetrics) -> bytes:
    window_closes = metrics.daily_closes[-CHART_WINDOW_SESSIONS:]
    closes = window_closes + [metrics.current_price]
    x = list(range(len(closes)))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(x, closes, label="Price", color="#1f77b4", linewidth=1.5)

    for window, color in _MA_COLORS.items():
        # Compute over the FULL close history, not just the chart window —
        # a 200-session SMA needs 200 input bars, more than fit in the
        # trailing CHART_WINDOW_SESSIONS alone — then slice down to display.
        full_sma = rolling_sma(metrics.daily_closes, window)
        sma_series = full_sma[-CHART_WINDOW_SESSIONS:]
        sma_series = sma_series + [sma_series[-1] if sma_series else None]
        if any(v is not None for v in sma_series):
            ax.plot(x, sma_series, label=f"SMA{window}", color=color, linewidth=1.0)

    ax.scatter([x[-1]], [metrics.current_price], color="black", zorder=5, label="Current")
    ax.set_title(f"{metrics.ticker} — trailing {len(window_closes)} sessions")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()
