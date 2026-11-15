"""Charts for the report pack.

Design rules followed here (so the charts read as one set and survive print):

* colour does one job per chart: *identity* (fixed categorical order - blue,
  orange, aqua, yellow), *polarity* (blue above / red below a neutral
  midpoint) or *magnitude* (one blue ramp). The categorical set was checked for
  colour-blind separation; the two lighter hues have low contrast on white, so
  every multi-line chart labels its lines directly and every chart has a table
  twin (the CSV the pipeline writes next to it).
* one y-axis per panel - two measures get two stacked panels, never twin axes;
* thin marks, hairline solid grid, text in ink colours (never the series colour);
* a legend whenever there are two or more series, plus labels at the line ends.

Charts made from the synthetic test fixture carry a visible stamp.
The functions draw; they do not interpret.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402
from matplotlib.patches import PathPatch, Rectangle  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

SURFACE = "#fcfcfb"
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # fixed order, never cycled
NEUTRAL = "#b9b8b1"  # context series / de-emphasised marks
CRITICAL = "#d03b3b"  # reserved for "limit breached"
POS, NEG, MID = "#2a78d6", "#e34948", "#f0efec"  # diverging poles and neutral midpoint
BLUE_RAMP = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
DIVERGING = LinearSegmentedColormap.from_list("blue_red", ["#c93433", "#f0a3a2", MID, "#9ec5f4", "#1c5cab"])
SEQUENTIAL = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)

plt.rcParams.update({
    "font.family": "sans-serif", "font.size": 9, "axes.titlesize": 11, "axes.labelsize": 9,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "grid.linestyle": "-", "axes.axisbelow": True,
    "text.color": INK, "axes.labelcolor": INK_2, "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.6,
    "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round",
})

_pct = FuncFormatter(lambda v, _: f"{v:.0%}")
_pct1 = FuncFormatter(lambda v, _: f"{v:.1%}")
_LOG_TICKS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 6, 8, 10,
              15, 20, 30, 50, 75, 100]
_PRETTY = {"UNIVERSE_CAP": "Universe, cap-weighted", "UNIVERSE_EQUAL": "Universe, equal-weighted"}


def pretty(name: str) -> str:
    """Display name of a benchmark series."""
    key = str(name).strip().upper()
    return _PRETTY.get(key, str(name).title().replace(" Tri", " TRI"))


def _pct_axis(axis, lo: float, hi: float) -> None:
    """Percent tick labels with as many decimals as the range needs."""
    span = abs(hi - lo)
    axis.set_major_formatter(_pct if span >= 0.08 else _pct1 if span >= 0.008 else FuncFormatter(lambda v, _: f"{v:.2%}"))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _fig(rows: int = 1, width: float = 7.4, height: float = 3.4, ratios: Optional[Sequence[float]] = None,
         sharex: bool = False):
    fig, axes = plt.subplots(rows, 1, figsize=(width, height), sharex=sharex,
                             gridspec_kw={"height_ratios": ratios} if ratios else None)
    return fig, (list(axes) if rows > 1 else [axes])


def _titles(ax, title: str, subtitle: str = "") -> None:
    """Bold title with an optional subtitle under it (offsets in points, so tall charts do not collide)."""
    ax.set_title(title, loc="left", color=INK, fontweight="bold", pad=20 if subtitle else 8)
    if subtitle:
        ax.annotate(subtitle, xy=(0.0, 1.0), xycoords="axes fraction", xytext=(0, 6), textcoords="offset points",
                    color=INK_2, fontsize=8.5, va="bottom", ha="left")


def _legend(ax, ncol: int = 4, drop: float = 0.13) -> None:
    """Legend under the plot, left-aligned, so it can never sit on top of the data."""
    handles, labels = ax.get_legend_handles_labels()
    if len(handles) >= 2:
        ax.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.0, -drop), ncol=ncol, fontsize=8,
                  borderaxespad=0.0, handlelength=1.6, columnspacing=1.6)


def _time_axis(ax) -> None:
    loc = mdates.AutoDateLocator(minticks=4, maxticks=9)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    ax.grid(axis="x", visible=False)


def _end_labels(ax, items: List[tuple], fmt=None) -> None:
    """Label each line at its right end; labels that would collide are spread apart with a leader."""
    if not items:
        return
    fig = ax.figure
    fig.canvas.draw()
    inv = ax.transData.inverted()
    pts = sorted(((ax.transData.transform((mdates.date2num(x) if isinstance(x, pd.Timestamp) else x, y)), text, y)
                  for x, y, text in items), key=lambda p: p[0][1])
    min_gap = 11.0 * fig.dpi / 72.0  # one text line, in pixels
    placed = []
    for (px, py), text, y in pts:
        target = py if not placed else max(py, placed[-1] + min_gap)
        placed.append(target)
        x_data, y_data = inv.transform((px + 6, target))
        ax.annotate(text, xy=inv.transform((px, py)), xytext=(x_data, y_data), color=INK_2, fontsize=8,
                    va="center", ha="left", annotation_clip=False,
                    arrowprops={"arrowstyle": "-", "color": AXIS, "lw": 0.6} if abs(target - py) > 2 else None)


def _round_bar_ends(ax, radius_px: float = 3.0) -> None:
    """Round the data end of every bar (the end away from its baseline); keep the baseline end square."""
    fig = ax.figure
    fig.canvas.draw()
    bars = [p for p in ax.patches if isinstance(p, Rectangle) and p.get_width() != 0 and p.get_height() != 0]
    if not bars:
        return
    x0, y0 = ax.transData.transform((0, 0))
    x1, y1 = ax.transData.transform((1, 1))
    per_x, per_y = 1.0 / abs(x1 - x0), 1.0 / abs(y1 - y0)  # data units per pixel
    M, L, C = MplPath.MOVETO, MplPath.LINETO, MplPath.CURVE3
    for bar in bars:
        x, y, w, h = bar.get_x(), bar.get_y(), bar.get_width(), bar.get_height()
        horizontal = getattr(bar, "_momo_horizontal", False)
        length_px = abs(w) / per_x if horizontal else abs(h) / per_y
        thick_px = abs(h) / per_y if horizontal else abs(w) / per_x
        r = min(radius_px, thick_px / 2.0, length_px / 2.0)
        if r < 1.0:
            continue
        rx, ry = r * per_x, r * per_y
        if horizontal:  # baseline at x, data end at x + w
            e, sgn, lo, hi = x + w, np.sign(w), min(y, y + h), max(y, y + h)
            verts = [(x, lo), (e - sgn * rx, lo), (e, lo), (e, lo + ry), (e, hi - ry), (e, hi), (e - sgn * rx, hi),
                     (x, hi), (x, lo)]
        else:  # baseline at y, data end at y + h
            e, sgn, lo, hi = y + h, np.sign(h), min(x, x + w), max(x, x + w)
            verts = [(lo, y), (lo, e - sgn * ry), (lo, e), (lo + rx, e), (hi - rx, e), (hi, e), (hi, e - sgn * ry),
                     (hi, y), (lo, y)]
        codes = [M, L, C, C, L, C, C, L, MplPath.CLOSEPOLY]
        bar.set_visible(False)
        ax.add_patch(PathPatch(MplPath(verts, codes), facecolor=bar.get_facecolor(), edgecolor="none",
                               zorder=bar.get_zorder()))


def _hbars(ax, labels: Sequence[str], values: Sequence[float], colours, height: float = 0.56,
           slots: Optional[int] = None) -> np.ndarray:
    """Horizontal bars listed from the top. ``slots`` reserves rows so a short list keeps thin bars."""
    n = len(labels)
    slots = max(n, slots or 0, 1)
    pos = slots - 1 - np.arange(n)
    bars = ax.barh(pos, values, height=height, color=colours, edgecolor="none")
    for b in bars:
        b._momo_horizontal = True
    ax.set_yticks(pos)
    ax.set_yticklabels(labels)
    ax.set_ylim(-0.6, slots - 0.4)
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    return pos


def stamp(fig, data_kind: str) -> None:
    """Mark charts made from the synthetic fixture so they can never pass as results."""
    if data_kind != "synthetic":
        return
    fig.text(0.5, 0.5, "SYNTHETIC TEST DATA\nNOT A BACK-TEST RESULT", ha="center", va="center", rotation=24,
             fontsize=26, color=MUTED, alpha=0.22, fontweight="bold", zorder=100)


def save(fig, path: str | Path, data_kind: str = "real", dpi: int = 200) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp(fig, data_kind)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# Performance
# --------------------------------------------------------------------------- #
def equity_and_drawdown(nav: pd.Series, benchmarks: pd.DataFrame, title: str = "Growth of Rs 1",
                        strategy_label: str = "Strategy (net)"):
    """Top: growth of Rs 1 on a log scale (strategy and up to three benchmarks).
    Bottom: drawdown of the strategy and the first benchmark."""
    fig, (ax, ax2) = _fig(2, height=5.4, ratios=[2.2, 1], sharex=True)
    series = {strategy_label: nav / nav.iloc[0]}
    for name in list(benchmarks.columns)[:3]:
        series[pretty(name)] = (1.0 + benchmarks[name].fillna(0.0)).cumprod()
    ends = []
    for k, ((name, s), colour) in enumerate(zip(series.items(), SERIES)):
        ax.plot(s.index, s.to_numpy(), color=colour, label=name, lw=2.0 if k == 0 else 1.3, zorder=5 - k)
        ends.append((s.index[-1], s.iloc[-1], f"{s.iloc[-1]:.2f}"))
        if k < 2:
            dd = s / s.cummax() - 1.0
            ax2.plot(dd.index, dd.to_numpy(), color=colour, lw=1.6 if k == 0 else 1.2, label=name, zorder=5 - k)
            if k == 0:
                ax2.fill_between(dd.index, dd.to_numpy(), 0.0, color=colour, alpha=0.10, lw=0)
    lo = min(float(s.min()) for s in series.values())
    hi = max(float(s.max()) for s in series.values())
    ax.set_yscale("log")
    ticks = [t for t in _LOG_TICKS if lo * 0.97 <= t <= hi * 1.03]
    if len(ticks) >= 2:
        ax.set_yticks(ticks)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.minorticks_off()
    _titles(ax, title, "Log scale; each series starts at 1. End values at right. Benchmarks are gross of costs.")
    _end_labels(ax, ends)
    ax.grid(axis="x", visible=False)
    ax2.yaxis.set_major_formatter(_pct)
    ax2.set_title("Drawdown from running peak", loc="left", color=INK_2, fontsize=9, pad=4)
    _time_axis(ax2)
    handles, labels = ax.get_legend_handles_labels()
    ax2.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.0, -0.22), ncol=2, fontsize=8, borderaxespad=0.0)
    fig.subplots_adjust(right=0.90, hspace=0.16)
    return fig


def rolling_return(returns: pd.Series, bench: pd.Series, window: int = 252, bench_label: str = "Benchmark"):
    fig, (ax,) = _fig(1, height=3.0)
    rs = np.expm1(np.log1p(returns).rolling(window).sum())
    rb = np.expm1(np.log1p(bench.fillna(0.0)).rolling(window).sum())
    ax.plot(rs.index, rs.to_numpy(), color=SERIES[0], label="Strategy (net)", lw=2.0, zorder=3)
    ax.plot(rb.index, rb.to_numpy(), color=SERIES[1], label=pretty(bench_label), lw=1.3)
    ax.axhline(0, color=AXIS, lw=0.8)
    ax.yaxis.set_major_formatter(_pct)
    _titles(ax, f"Rolling {window}-day return")
    _time_axis(ax)
    _legend(ax)
    return fig


def monthly_heatmap(table: pd.DataFrame):
    """Year x month grid of returns; blue above zero, red below, neutral at zero."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    grid = table.reindex(columns=list(range(1, 13)))
    fig, (ax,) = _fig(1, height=0.42 * len(grid) + 1.2)
    lim = max(0.02, float(np.nanmax(np.abs(grid.to_numpy(float)))))
    ax.imshow(grid.to_numpy(float), cmap=DIVERGING, norm=TwoSlopeNorm(0.0, -lim, lim), aspect="auto")
    ax.set_xticks(range(12))
    ax.set_xticklabels(months)
    ax.set_yticks(range(len(grid)))
    ax.set_yticklabels(grid.index)
    ax.grid(False)
    ax.tick_params(length=0)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    for i in range(grid.shape[0]):
        for j in range(12):
            v = grid.iat[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.1%}", ha="center", va="center", fontsize=7.5,
                        color="#ffffff" if abs(v) > 0.6 * lim else INK)
    if "year" in table.columns:
        for i, v in enumerate(table["year"]):
            ax.text(11.75, i, f"{v:.1%}", ha="left", va="center", fontsize=8, color=INK, fontweight="bold")
        ax.text(11.75, -0.72, "Year", ha="left", va="center", fontsize=8, color=INK_2)
    ax.set_xticks(np.arange(-0.5, 12, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(grid), 1), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)  # surface gap between cells
    ax.tick_params(which="minor", length=0)
    _titles(ax, "Monthly returns (net)")
    return fig


def calendar_years(table: pd.DataFrame, bench_col: str):
    fig, (ax,) = _fig(1, height=3.0)
    x = np.arange(len(table))
    width = min(0.34, 0.085 * max(len(table), 4))
    ax.bar(x - width / 2 - 0.02, table["return"], width=width, color=SERIES[0], label="Strategy (net)")
    ax.bar(x + width / 2 + 0.02, table[bench_col], width=width, color=SERIES[1], label=pretty(bench_col))
    ax.axhline(0, color=AXIS, lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(table.index)
    ax.set_xlim(-0.6, max(len(table), 4) - 0.4)
    ax.yaxis.set_major_formatter(_pct)
    ax.grid(axis="x", visible=False)
    _titles(ax, "Return by calendar year", "First and last years may be partial.")
    _legend(ax)
    _round_bar_ends(ax)
    return fig


def variants(navs: Dict[str, pd.Series]):
    """Paper vs gross vs net: what liquidity limits and costs take away."""
    fig, (ax,) = _fig(1, height=3.2)
    labels = {"paper": "Paper (no limits, no costs)", "gross": "Gross (liquidity limits, no costs)",
              "net": "Net (limits and costs)"}
    ends = []
    for name, colour in zip(("paper", "gross", "net"), SERIES):
        if name not in navs:
            continue
        s = navs[name] / navs[name].iloc[0]
        ax.plot(s.index, s.to_numpy(), color=colour, label=labels[name], lw=2.0 if name == "net" else 1.3)
        ends.append((s.index[-1], s.iloc[-1], f"{name.title()} {s.iloc[-1]:.2f}"))
    _titles(ax, "From paper portfolio to net result", "Growth of Rs 1")
    _time_axis(ax)
    _end_labels(ax, ends)
    _legend(ax, ncol=3)
    fig.subplots_adjust(right=0.86)
    return fig


_REGIME_ORDER = ["above 200d average", "below 200d average", "low vol", "mid vol", "high vol",
                 "benchmark near peak", "benchmark >10% off peak"]


def regimes(table: pd.DataFrame, bench_label: str = "Benchmark"):
    """Annualised strategy and benchmark return in each market state."""
    t = table.drop(index="direction", level=0, errors="ignore").reset_index(level=0, drop=True)
    t = t.reindex([s for s in _REGIME_ORDER if s in t.index])
    n = len(t)
    fig, (ax,) = _fig(1, height=0.44 * n + 1.5)
    pos = n - 1 - np.arange(n)
    for off, col, colour, lab in ((0.17, "strategy_ann_return", SERIES[0], "Strategy (net)"),
                                  (-0.17, "benchmark_ann_return", SERIES[1], pretty(bench_label))):
        bars = ax.barh(pos + off, t[col], height=0.30, color=colour, label=lab)
        for b in bars:
            b._momo_horizontal = True
    ax.set_yticks(pos)
    ax.set_yticklabels(t.index)
    ax.set_ylim(-0.6, n - 0.4)
    ax.axvline(0, color=AXIS, lw=0.8)
    ax.xaxis.set_major_formatter(_pct)
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    _titles(ax, "Annualised return by market regime", "Regimes classified from benchmark data up to the previous day.")
    _legend(ax, drop=0.10)
    _round_bar_ends(ax)
    return fig


# --------------------------------------------------------------------------- #
# Trading, costs, limits
# --------------------------------------------------------------------------- #
def cost_breakdown(costs: pd.DataFrame):
    c = costs.drop(index="TOTAL", errors="ignore")
    c = c[c["rupees"] > 0].sort_values("bps_of_traded_value", ascending=False)
    fig, (ax,) = _fig(1, height=0.36 * max(len(c), 4) + 1.3)
    pos = _hbars(ax, [s.replace("_", " ") for s in c.index], c["bps_of_traded_value"], SERIES[0], slots=4)
    for y, v in zip(pos, c["bps_of_traded_value"]):
        ax.text(v, y, f"  {v:.1f}", va="center", ha="left", color=INK_2, fontsize=8)
    ax.set_xlabel("basis points of traded value")
    total = costs.loc["TOTAL", "bps_of_traded_value"] if "TOTAL" in costs.index else c["bps_of_traded_value"].sum()
    _titles(ax, "Trading costs by component", f"Total {total:.1f} bp of traded value")
    ax.margins(x=0.12)
    _round_bar_ends(ax)
    return fig


def blocked_signals(screening: pd.DataFrame, execution: pd.DataFrame):
    """Ranked bars: signals removed at screening (left), order-days that fell short at execution (right)."""
    s = screening.sort_values("signals_removed", ascending=False) if len(screening) else screening
    e = (execution.groupby(level="reason")["order_days"].sum().sort_values(ascending=False)
         if len(execution) else pd.Series(dtype=float))
    slots = max(len(s), len(e), 4)
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 0.36 * slots + 1.4))
    for ax, labels, values, title, sub in (
            (axes[0], list(s.index), s["signals_removed"].to_numpy(float) if len(s) else [],
             "Top-ranked signals removed", "before portfolio construction, by filter"),
            (axes[1], list(e.index), e.to_numpy(float), "Order-days that fell short", "at execution, by cause")):
        pos = _hbars(ax, [str(i).replace("_", " ") for i in labels], values, SERIES[0], slots=slots)
        for y, v in zip(pos, values):
            ax.text(v, y, f"  {v:,.0f}", va="center", ha="left", color=INK_2, fontsize=8)
        if len(labels) == 0:
            ax.text(0.5, 0.5, "none", transform=ax.transAxes, ha="center", va="center", color=MUTED)
            ax.set_xticks([])
        ax.margins(x=0.2)
        ax.xaxis.set_major_locator(MaxNLocator(4, integer=True))
        _titles(ax, title, sub)
        _round_bar_ends(ax)
    fig.subplots_adjust(wspace=0.8)
    return fig


def exposure(daily: pd.DataFrame):
    fig, (ax, ax2) = _fig(2, height=4.2, ratios=[1, 1], sharex=True)
    ax.plot(daily.index, daily["exposure"], color=SERIES[0])
    ax.fill_between(daily.index, daily["exposure"], 0.0, color=SERIES[0], alpha=0.10, lw=0)
    ax.yaxis.set_major_formatter(_pct)
    ax.set_ylim(0, max(1.05, float(daily["exposure"].max()) * 1.05))
    _titles(ax, "Invested fraction of NAV")
    ax.grid(axis="x", visible=False)
    ax2.plot(daily.index, daily["n_positions"], color=SERIES[0])
    ax2.set_title("Number of holdings", loc="left", color=INK, fontweight="bold", fontsize=11)
    ax2.set_ylim(0, None)
    _time_axis(ax2)
    fig.subplots_adjust(hspace=0.35)
    return fig


def sector_weights(avg: pd.Series, peak: pd.Series, cap: float):
    """Average sector weight (bar) and its peak (tick), against the sector limit."""
    order = avg.sort_values(ascending=False).index
    fig, (ax,) = _fig(1, height=0.36 * max(len(order), 4) + 1.6)
    pos = _hbars(ax, list(order), avg[order], SERIES[0], slots=4)
    ax.scatter(peak[order], pos, marker="|", s=170, color=INK, lw=1.6, zorder=3)
    ax.axvline(cap, color=CRITICAL, lw=1.0)
    ax.text(cap, ax.get_ylim()[1], f" limit {cap:.0%}", color=INK_2, fontsize=8, va="top", ha="left")
    ax.xaxis.set_major_formatter(_pct)
    ax.set_xlim(0, max(cap * 1.25, float(peak.max()) * 1.08))
    _titles(ax, "Sector weights: average and peak", "Bar = average over the period; black tick = highest daily weight")
    _round_bar_ends(ax)
    return fig


def var_backtest(series: pd.DataFrame, limit: float, confidence: float):
    """Next-day returns against the ex-ante VaR; breaches are marked."""
    s = series.dropna(subset=["var"])
    fig, (ax,) = _fig(1, height=3.4)
    ax.vlines(s.index, 0, s["next_day_return"], color=NEUTRAL, lw=0.5, label="Next-day return")
    ax.plot(s.index, -s["var"], color=SERIES[0], lw=1.6, label=f"Minus VaR ({confidence:.0%}, 1 day)")
    br = s[s["breach"] == 1]
    ax.scatter(br.index, br["next_day_return"], s=38, color=CRITICAL, edgecolor=SURFACE, linewidth=1.4, zorder=4,
               label=f"Breach ({len(br)} of {len(s)} days)")
    ax.axhline(-limit, color=INK_2, lw=0.9)
    ax.text(s.index[-1], -limit, f"VaR limit {limit:.1%} ", va="bottom", ha="right", color=INK_2, fontsize=8)
    ax.axhline(0, color=AXIS, lw=0.8)
    ax.yaxis.set_major_formatter(_pct1)
    _titles(ax, "VaR back-test", "A breach is a next-day loss larger than that day's VaR")
    _time_axis(ax)
    _legend(ax)
    return fig


def stress_windows(table: pd.DataFrame, bench_col: str):
    t = table.dropna(subset=["strategy_return"]) if "strategy_return" in table.columns else table.iloc[0:0]
    n = len(t)
    slots = max(n, 2)
    fig, (ax,) = _fig(1, height=0.62 * slots + 1.6)
    pos = slots - 1 - np.arange(n)
    for off, col, colour, lab in ((0.17, "strategy_return", SERIES[0], "Strategy (net)"),
                                  (-0.17, bench_col, SERIES[1], pretty(bench_col.replace(" return", "")))):
        bars = ax.barh(pos + off, t[col], height=0.28, color=colour, label=lab)
        for b, v in zip(bars, t[col]):
            b._momo_horizontal = True
            ax.text(v, b.get_y() + b.get_height() / 2, f" {v:.1%} " if np.isfinite(v) else "", va="center",
                    ha="right" if v < 0 else "left", color=INK_2, fontsize=8)
    ax.set_yticks(pos)
    ax.set_yticklabels([f"{name}\n{row.start} to {row.end}" for name, row in t.iterrows()])
    ax.set_ylim(-0.6, slots - 0.4)
    ax.axvline(0, color=AXIS, lw=0.8)
    ax.margins(x=0.2)
    _pct_axis(ax.xaxis, *ax.get_xlim())
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    _titles(ax, "Stress windows: return over each episode")
    _legend(ax, drop=0.14)
    _round_bar_ends(ax)
    return fig


def capacity(table: pd.DataFrame):
    """Two panels sharing the fund-size axis: net return, and how much of each order was filled."""
    fig, (ax, ax2) = _fig(2, height=4.6, ratios=[1, 1], sharex=True)
    x = table.index.to_numpy(float)
    ax.plot(x, table["net_cagr"], color=SERIES[0], marker="o", ms=5.5, mec=SURFACE, mew=1.4)
    ax.axhline(0, color=AXIS, lw=0.8)
    ax.yaxis.set_major_formatter(_pct)
    _titles(ax, "Capacity: net annual return by fund size")
    ax2.plot(x, table["value_fill_ratio"], color=SERIES[0], marker="o", ms=5.5, mec=SURFACE, mew=1.4)
    ax2.yaxis.set_major_formatter(_pct)
    ax2.set_ylim(0, 1.05)
    ax2.set_title("Share of ordered value that was filled", loc="left", color=INK, fontweight="bold", fontsize=11)
    ax2.set_xscale("log")
    ax2.set_xticks(x)
    ax2.set_xticklabels([f"{v:g}" for v in x])
    ax2.minorticks_off()
    ax2.set_xlabel("fund size, Rs crore (log scale)")
    for a in (ax, ax2):
        a.grid(axis="x", visible=False)
    fig.subplots_adjust(hspace=0.35)
    return fig


def liquidity_profile(profile: Dict[str, float], limit: Optional[float] = None, horizon: Optional[int] = None,
                      as_of: str = ""):
    keys = [k for k in profile if k.startswith("sellable_")]
    days = [k.replace("sellable_", "").replace("d", "") for k in keys]
    fig, (ax,) = _fig(1, width=5.2, height=3.0)
    ax.bar(np.arange(len(keys)), [profile[k] for k in keys], width=0.36, color=SERIES[0])
    for i, k in enumerate(keys):
        ax.text(i, profile[k], f"{profile[k]:.0%}", ha="center", va="bottom", color=INK_2, fontsize=8)
    ax.set_xticks(np.arange(len(keys)))
    ax.set_xticklabels([f"{d} day{'s' if d != '1' else ''}" for d in days])
    ax.yaxis.set_major_formatter(_pct)
    ax.set_ylim(0, 1.15)
    ax.grid(axis="x", visible=False)
    if limit is not None:
        ax.axhline(limit, color=CRITICAL, lw=1.0)
        ax.text(len(keys) - 0.5, limit, f"minimum {limit:.0%} within {horizon} days ", va="bottom", ha="right",
                color=INK_2, fontsize=8)
    _titles(ax, "Share of the book sellable within", f"at the participation cap{', on ' + as_of if as_of else ''}")
    _round_bar_ends(ax)
    return fig


# --------------------------------------------------------------------------- #
# Attribution, factors, research
# --------------------------------------------------------------------------- #
def attribution_bars(attr: pd.DataFrame, title: str = "Contribution to return by sector", top: int = 15):
    """Diverging bars: blue for a positive contribution, red for a negative one."""
    a = attr.sort_values("contribution_net", ascending=False)
    if len(a) > top:
        a = pd.concat([a.head(top // 2 + top % 2), a.tail(top // 2)])
    fig, (ax,) = _fig(1, height=0.34 * max(len(a), 4) + 1.3)
    _hbars(ax, [str(i) for i in a.index], a["contribution_net"],
           [POS if v >= 0 else NEG for v in a["contribution_net"]], slots=4)
    ax.axvline(0, color=AXIS, lw=0.8)
    _pct_axis(ax.xaxis, *ax.get_xlim())
    _titles(ax, title, "Net of costs; sum of daily P&L over previous-day NAV. Blue = gain, red = loss.")
    _round_bar_ends(ax)
    return fig


def factor_loadings(table: pd.DataFrame, alpha_annual: float, r2: float, frequency: str):
    names = {"mkt_rf": "Market", "smb": "Size (SMB)", "hml": "Value (HML)", "wml": "Momentum (WML)"}
    t = table.loc[[i for i in table.index if i in names]]
    fig, (ax,) = _fig(1, width=5.8, height=0.5 * max(len(t), 3) + 1.5)
    pos = _hbars(ax, [names[i] for i in t.index], t["coefficient"],
                 [POS if v >= 0 else NEG for v in t["coefficient"]], height=0.42, slots=3)
    for y, v, ts in zip(pos, t["coefficient"], t["t_stat"]):
        ax.text(v, y, f"  {v:.2f} (t = {ts:.1f})  ", va="center", ha="left" if v >= 0 else "right",
                color=INK_2, fontsize=8)
    ax.axvline(0, color=AXIS, lw=0.8)
    ax.margins(x=0.34)
    _titles(ax, "Factor exposures", f"{frequency.title()} regression; alpha {alpha_annual:.1%} a year, R-squared {r2:.2f}")
    _round_bar_ends(ax)
    return fig


def rolling_factor_betas(betas: pd.DataFrame):
    names = {"mkt_rf": "Market", "smb": "Size", "hml": "Value", "wml": "Momentum"}
    fig, (ax,) = _fig(1, height=3.2)
    ends = []
    for col, colour in zip([c for c in ("mkt_rf", "smb", "hml", "wml") if c in betas.columns], SERIES):
        s = betas[col].dropna()
        if s.empty:
            continue
        ax.plot(s.index, s.to_numpy(), color=colour, label=names[col])
        ends.append((s.index[-1], s.iloc[-1], names[col]))
    ax.axhline(0, color=AXIS, lw=0.8)
    _titles(ax, "Rolling factor betas", "252-day window, daily returns")
    _time_axis(ax)
    _end_labels(ax, ends)
    _legend(ax)
    fig.subplots_adjust(right=0.88)
    return fig


def sensitivity_heatmap(pivot: pd.DataFrame, metric: str, row_label: str, col_label: str):
    """One blue ramp: darker = larger. Use it to look for a plateau, not a peak."""
    fig, (ax,) = _fig(1, width=1.0 * pivot.shape[1] + 2.6, height=0.5 * pivot.shape[0] + 1.7)
    data = pivot.to_numpy(float)
    ax.imshow(data, cmap=SEQUENTIAL, aspect="auto")
    lo, hi = np.nanmin(data), np.nanmax(data)
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            if np.isfinite(data[i, j]):
                dark = hi > lo and (data[i, j] - lo) / (hi - lo) > 0.55
                ax.text(j, i, f"{data[i, j]:.2f}", ha="center", va="center", fontsize=8.5,
                        color="#ffffff" if dark else INK)
    ax.set_xticks(range(pivot.shape[1]))
    ax.set_xticklabels(pivot.columns)
    ax.set_yticks(range(pivot.shape[0]))
    ax.set_yticklabels(pivot.index)
    ax.set_xlabel(col_label)
    ax.set_ylabel(row_label)
    ax.grid(False)
    ax.tick_params(length=0)
    ax.set_xticks(np.arange(-0.5, pivot.shape[1], 1), minor=True)
    ax.set_yticks(np.arange(-0.5, pivot.shape[0], 1), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="minor", length=0)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    _titles(ax, f"Training-period {metric.replace('_', ' ')} by parameter pair")
    return fig
