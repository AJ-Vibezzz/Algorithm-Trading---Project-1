"""Point-in-time investable universe.

For every decision date the universe is rebuilt using only what was known that
day. Two sources are supported (``universe.source`` in the strategy config):

``mcap_rank``
    Rank every NSE equity share by its average market capitalisation over the
    trailing ``mcap_average_days`` and cut the ranking into segments (large,
    mid, small, micro) with ``rank_bounds``. This mirrors how SEBI/AMFI and NSE
    Indices define the segments, and needs no purchased constituent history:
    the market caps come from the exchange's own daily file for that date.

``index_constituents``
    Use the group's own historical index-membership file (start/end dates per
    stock). The loader refuses a file without start dates, because applying
    today's members to past dates is survivorship bias.

After the size cut, tradability filters are applied **in a fixed order**, and
the first filter a stock fails is recorded. The pipeline later reports how many
would-be signals each filter removed, as the project brief requires.

Companies that later delisted are in the universe for as long as they traded:
the panel never drops them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import UniverseCfg
from .frictions import rolling_mean
from .market import SERIES_CODE, MarketData

# Order matters: the first failing test is the recorded reason.
FILTER_ORDER = ["series", "surveillance", "listing_age", "price", "traded_days", "liquidity", "circuit"]
RECENT_TRADE_DAYS = 5  # a stock must have traded within this many market days to be ranked


@dataclass
class UniverseSnapshot:
    """The universe on one decision date (arrays aligned to ``MarketData.sids``)."""

    row: int
    date: pd.Timestamp
    rank: np.ndarray  # market-cap rank (1 = largest); NaN if not ranked
    segment: np.ndarray  # object array: segment name, '' if outside every segment
    in_segment: np.ndarray  # bool: belongs to one of the chosen segments
    eligible: np.ndarray  # bool: in a chosen segment and passes every filter
    reason: np.ndarray  # object array: '' if eligible, else the first filter failed
    size_metric: str = "mcap"

    def counts(self) -> Dict[str, int]:
        out = {"in_segment": int(self.in_segment.sum()), "eligible": int(self.eligible.sum())}
        for r in FILTER_ORDER:
            out[f"removed_{r}"] = int((self.reason == r).sum())
        return out


@dataclass
class Universe:
    """All snapshots, plus a tidy table of counts for the report."""

    snapshots: Dict[int, UniverseSnapshot] = field(default_factory=dict)

    def __getitem__(self, row: int) -> UniverseSnapshot:
        return self.snapshots[row]

    def table(self) -> pd.DataFrame:
        rows = [{"date": s.date, **s.counts(), "size_metric": s.size_metric} for s in self.snapshots.values()]
        return pd.DataFrame(rows).set_index("date").sort_index() if rows else pd.DataFrame()


def _spell_flags(md: MarketData, spells: pd.DataFrame, rows: List[int]) -> Dict[int, np.ndarray]:
    """For each decision row, which companies are inside a (symbol, start, end) spell."""
    flags = {t: np.zeros(md.N, bool) for t in rows}
    if spells is None or spells.empty:
        return flags
    # Map every symbol a company has carried to its column.
    col_of: Dict[str, int] = {}
    for j, names in enumerate(md.securities["symbols"].fillna("").to_numpy()):
        for name in str(names).split("|"):
            if name:
                col_of.setdefault(name, j)
    isin_of: Dict[str, int] = {}
    for j, names in enumerate(md.securities["isins"].fillna("").to_numpy()):
        for name in str(names).split("|"):
            if name:
                isin_of.setdefault(name, j)
    row_dates = {t: md.dates[t] for t in rows}
    far_future = pd.Timestamp.max
    for rec in spells.itertuples(index=False):
        j = col_of.get(getattr(rec, "symbol", ""))
        if j is None and hasattr(rec, "isin"):
            j = isin_of.get(rec.isin)
        if j is None:
            continue
        start = rec.start
        end = rec.end if pd.notna(rec.end) else far_future
        for t, d in row_dates.items():
            if start <= d <= end:
                flags[t][j] = True
    return flags


def build_universe(
    md: MarketData,
    cfg: UniverseCfg,
    rows: List[int],
    surveillance: Optional[pd.DataFrame] = None,
    constituents: Optional[pd.DataFrame] = None,
) -> Universe:
    """Universe snapshots for the given decision rows."""
    T, N = md.T, md.N
    recent = md.days_since_trade <= RECENT_TRADE_DAYS

    # ---- size metric: trailing average market cap (fallback: traded value) -----------
    have_mcap = np.isfinite(md.mcap).any()
    if have_mcap:
        size = rolling_mean(md.mcap_ff, cfg.mcap_average_days, min_periods=min(20, cfg.mcap_average_days))
        size_metric = "mcap"
    else:
        # No shares-outstanding data: rank by typical traded value instead. Weaker - say so in the report.
        size = md.median_value(cfg.mcap_average_days)
        size_metric = "traded_value_proxy"

    surv_flags = _spell_flags(md, surveillance, rows) if cfg.exclude_surveillance else {t: np.zeros(N, bool) for t in rows}
    member_flags: Dict[str, Dict[int, np.ndarray]] = {}
    if cfg.source == "index_constituents":
        if constituents is None or constituents.empty:
            raise ValueError(
                "universe.source = index_constituents but no membership file was found under "
                "data/external/index_constituents (see docs/01_data_requirements.md)."
            )
        wanted = [n.strip().upper() for n in cfg.index_names]
        for name in wanted:
            sub = constituents[constituents["index_name"] == name]
            if sub.empty:
                raise ValueError(f"No membership rows for index '{name}' in data/external/index_constituents")
            member_flags[name] = _spell_flags(md, sub, rows)

    allowed_codes = [SERIES_CODE.get(s, 3) for s in cfg.allowed_series]
    liq_win = cfg.liquidity_lookback_days
    med_val = md.median_value(liq_win)
    trd_frac = md.traded_fraction(liq_win)
    lock_frac = md.lock_fraction(cfg.circuit_lookback_days) if cfg.max_circuit_days_fraction is not None else None

    # last known series for each stock (so a stock that skipped today keeps yesterday's series)
    series_ff = md.series.astype(np.int16).copy()
    for t in range(1, T):
        gap = series_ff[t] < 0
        series_ff[t, gap] = series_ff[t - 1, gap]

    uni = Universe()
    for t in rows:
        s = size[t].copy()
        s[~recent[t]] = np.nan
        order = np.argsort(-np.nan_to_num(s, nan=-np.inf), kind="stable")
        rank = np.full(N, np.nan)
        n_ranked = int(np.isfinite(s).sum())
        rank[order[:n_ranked]] = np.arange(1, n_ranked + 1)

        segment = np.full(N, "", dtype=object)
        if cfg.source == "mcap_rank":
            for name, (lo, hi) in cfg.rank_bounds.items():
                segment[(rank >= lo) & (rank <= hi)] = name
            in_segment = np.isin(segment, cfg.segments)
        else:
            in_segment = np.zeros(N, bool)
            for name, flags in member_flags.items():
                segment[flags[t] & (segment == "")] = name
                in_segment |= flags[t]
            in_segment &= recent[t]

        reason = np.full(N, "", dtype=object)

        def fail(mask: np.ndarray, label: str) -> None:
            reason[in_segment & (reason == "") & mask] = label

        fail(~np.isin(series_ff[t], allowed_codes), "series")
        fail(surv_flags[t], "surveillance")
        fail(md.age[t] < cfg.min_listing_days, "listing_age")
        fail(~(md.close_ff[t] >= cfg.min_price_inr), "price")
        fail(~(trd_frac[t] >= cfg.min_traded_days_fraction), "traded_days")
        fail(~(med_val[t] >= cfg.min_median_traded_value_inr), "liquidity")
        if lock_frac is not None:
            fail(lock_frac[t] > cfg.max_circuit_days_fraction, "circuit")

        eligible = in_segment & (reason == "")
        uni.snapshots[t] = UniverseSnapshot(
            row=t, date=md.dates[t], rank=rank, segment=segment, in_segment=in_segment,
            eligible=eligible, reason=reason, size_metric=size_metric,
        )
    return uni


def universe_benchmark_returns(md: MarketData, uni: Universe, rows: List[int]) -> pd.DataFrame:
    """Daily returns of the eligible universe itself: cap-weighted and equal-weighted.

    Membership is fixed at each decision date and held until the next one, so
    the series is investable in principle and free of look-ahead. It is the
    fairest yardstick for stock selection: same stocks, same data, no signal.
    It is gross of costs - label it so when comparing.
    """
    cap = np.full(md.T, np.nan)
    eq = np.full(md.T, np.nan)
    rows = sorted(rows)
    for k, t in enumerate(rows):
        t_next = rows[k + 1] if k + 1 < len(rows) else md.T - 1
        members = uni[t].eligible
        if not members.any() or t + 1 > t_next:
            continue
        w0 = np.where(members, np.nan_to_num(md.mcap_ff[t]), 0.0)
        r = md.ret[t + 1:t_next + 1][:, members]
        r0 = np.nan_to_num(r)  # a non-trading day contributes zero return
        # equal weight at the decision date, then buy and hold until the next one
        growth = np.cumprod(1.0 + r0, axis=0)
        eq_val = growth.mean(axis=1)
        eq[t + 1:t_next + 1] = np.diff(np.concatenate([[1.0], eq_val])) / np.concatenate([[1.0], eq_val[:-1]])
        wc = w0[members]
        if wc.sum() > 0:
            cap_val = growth @ (wc / wc.sum())
            cap[t + 1:t_next + 1] = np.diff(np.concatenate([[1.0], cap_val])) / np.concatenate([[1.0], cap_val[:-1]])
    return pd.DataFrame({"UNIVERSE_CAP": cap, "UNIVERSE_EQUAL": eq}, index=md.dates)


def segment_liquidity_table(md: MarketData, rank_bounds: Dict[str, List[int]], mcap_average_days: int = 126,
                            window: int = 60) -> pd.DataFrame:
    """How liquid is each size segment? One row per (year-end, segment).

    Descriptive facts to inform the group's choice of liquidity filter, fund
    size and participation cap: the number of stocks in each segment and the
    spread of their typical daily traded value (in Rs crore), plus how many
    are low-priced or outside the normal EQ series. Uses the same market-cap
    ranking as the universe, on the last trading day of each calendar year.
    """
    size = rolling_mean(md.mcap_ff, mcap_average_days, min_periods=min(20, mcap_average_days))
    med = md.median_value(window)
    years = pd.Series(md.dates.year, index=np.arange(md.T))
    year_ends = years.groupby(years).apply(lambda s: s.index[-1]).to_numpy()
    rows = []
    for t in year_ends:
        s = np.where(md.days_since_trade[t] <= RECENT_TRADE_DAYS, size[t], np.nan)
        if not np.isfinite(s).any():
            continue
        order = np.argsort(-np.nan_to_num(s, nan=-np.inf), kind="stable")
        n = int(np.isfinite(s).sum())
        rank = np.full(md.N, np.nan)
        rank[order[:n]] = np.arange(1, n + 1)
        for name, (lo, hi) in rank_bounds.items():
            m = (rank >= lo) & (rank <= hi)
            if not m.any():
                continue
            v = med[t, m] / 1e7
            px = md.close_ff[t, m]
            rows.append({
                "date": md.dates[t].date(), "segment": name, "stocks": int(m.sum()),
                "median_mcap_cr": float(np.nanmedian(md.mcap_ff[t, m]) / 1e7),
                "traded_value_cr_p10": float(np.nanpercentile(v, 10)),
                "traded_value_cr_p25": float(np.nanpercentile(v, 25)),
                "traded_value_cr_median": float(np.nanmedian(v)),
                "traded_value_cr_p75": float(np.nanpercentile(v, 75)),
                "share_price_below_20": float(np.mean(px < 20)),
                "share_not_in_EQ_series": float(np.mean(md.series[t, m] > 0)),
                "share_locked_10pct_of_days": float(np.mean(md.lock_fraction(window)[t, m] > 0.10)),
            })
    return pd.DataFrame(rows).set_index(["date", "segment"]) if rows else pd.DataFrame()
