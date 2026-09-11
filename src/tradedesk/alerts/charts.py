"""Chart images for watchlist entries and alerts (PLAN.md 6.4, 7): daily candles with the
setup's geometry drawn on - base/flag box, trigger, stop, T1/T2, EMAs - saved as PNG.
Runs headless (Agg backend); nothing here touches the event loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from tradedesk.engine.signals import Signal  # noqa: E402

BARS = 120


def _levels(sig: Signal) -> list[tuple[float, str, str]]:
    return [
        (sig.trigger, "trigger", "#1f77b4"),
        (sig.stop, "stop", "#d62728"),
        (sig.t1, "T1", "#2ca02c"),
        (sig.t2, "T2", "#17becf"),
    ]


def _geometry_lines(sig: Signal, df: pd.DataFrame, offset: int) -> list[dict[str, Any]]:
    """Segments (in plotted-bar coordinates) outlining the detected structure."""
    g = sig.geometry
    idx = df.index
    segs: list[dict[str, Any]] = []

    def seg(i0: int, i1: int, y: float, color: str) -> None:
        a, b = i0 - offset, i1 - offset
        if b < 0:
            return
        a = max(a, 0)
        segs.append({"pts": [(idx[a], y), (idx[min(b, len(idx) - 1)], y)], "color": color})

    if "base" in g:
        b = g["base"]
        seg(b["start"], b["end"], b["high"], "#ff7f0e")
        seg(b["start"], b["end"], b["low"], "#ff7f0e")
    if "flag" in g:
        f = g["flag"]
        seg(f["flag_start"], f["flag_end"], f["flag_high"], "#ff7f0e")
        seg(f["flag_start"], f["flag_end"], f["flag_low"], "#ff7f0e")
    if "pullback" in g:
        p = g["pullback"]
        seg(p["swing_high_index"], p["end"], p["swing_high"], "#ff7f0e")
    return segs


def render_signal_chart(
    df: pd.DataFrame,
    sig: Signal,
    out: Path,
    *,
    title: str | None = None,
    bars: int = BARS,
) -> Path:
    """`df` is the stock's daily feature frame up to the arming session (full length, so
    geometry bar indices stay valid). Writes a PNG and returns its path."""
    n = len(df)
    offset = max(0, n - bars)
    view = df.iloc[offset:].copy()
    view.index = pd.DatetimeIndex(view.index).tz_localize(None)
    plots = []
    for col, color in (("ema10", "#9467bd"), ("ema20", "#8c564b"), ("ema50", "#e377c2")):
        if col in view.columns:
            plots.append(mpf.make_addplot(view[col], color=color, width=0.8))

    segs = _geometry_lines(sig, view, offset)
    alines: dict[str, Any] | None = None
    if segs:
        alines = {
            "alines": [s["pts"] for s in segs],
            "colors": [s["color"] for s in segs],
            "linewidths": 1.2,
        }
    hlines = {
        "hlines": [lv for lv, _, _ in _levels(sig)],
        "colors": [c for _, _, c in _levels(sig)],
        "linestyle": "--",
        "linewidths": 0.9,
    }
    style = mpf.make_mpf_style(base_mpf_style="yahoo", gridstyle=":", y_on_right=True)
    extra: dict[str, Any] = {}
    if plots:
        extra["addplot"] = plots
    if alines is not None:
        extra["alines"] = alines
    fig, axes = mpf.plot(
        view[["open", "high", "low", "close", "volume"]],
        type="candle",
        volume=True,
        hlines=hlines,
        style=style,
        **extra,
        figsize=(11, 6.5),
        returnfig=True,
        title=title or f"{sig.symbol} · {sig.setup.value} · armed {sig.armed_on}",
        tight_layout=True,
    )
    ax = axes[0]
    x_right = view.index[-1]
    for lv, label, color in _levels(sig):
        ax.annotate(
            f"{label} {lv:.2f}",
            xy=(x_right, lv),
            xytext=(4, 0),
            textcoords="offset points",
            fontsize=8,
            color=color,
            va="center",
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out
