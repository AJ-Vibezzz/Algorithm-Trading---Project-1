"""Point-in-time investable universe.

For every decision date the universe is rebuilt using only what was known that
day. Two sources are supported (``universe.source`` in the strategy config):

``mcap_rank``
    Rank every NSE equity share by size and cut the ranking into segments
    (large, mid, small, micro) with ``rank_bounds``, as SEBI/AMFI and NSE
    Indices do. No purchased constituent history is needed.

    The exchange only publishes market capitalisation daily from January 2024,
    so "size" has to come from different measures in different years. The
    group lists the measures it accepts in order of preference
    (``universe.size_metrics``); on each decision date the first one that can
    rank deep enough for the chosen segments is used for every stock, and the
    choice is recorded in the universe table:

    ``exchange_mcap``  average exchange market cap over ``mcap_average_days``
                       (daily file from 2024; before that the exchange's latest
                       market-cap list carried forward with the share price -
                       available from the list of 31 March 2020)
    ``exchange_list``  rank in the exchange's latest market-cap list, unchanged
                       until the next list (available from 31 March 2019)
    ``amfi_list``      rank in the latest AMFI half-yearly list, if supplied
    ``traded_value``   median daily traded value over ``mcap_average_days`` -
                       a liquidity measure standing in for size; always available

    ``universe.large_cap_indices`` optionally fixes the top of the ranking: the
    members of the named indices on that date (the monthly Nifty 50 and Nifty
    Next 50 lists go back to 2008) take the first ranks, whatever the measure.

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
SIZE_METRICS = ("exchange_mcap", "exchange_list", "amfi_list", "traded_value")
LIST_SOURCE = {"exchange_list": "nse_list", "amfi_list": "amfi"}  # metric -> source label in data/size.py
MIN_COVERAGE = 0.8  # a measure must rank this share of the traded stocks (or as deep as the segments need)


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
    size_metric: str = "exchange_mcap"  # which size measure ranked the stocks ('none' = no measure usable)
    size_list_date: Optional[pd.Timestamp] = None  # as-on date of the list used, for the list measures
    n_ranked: int = 0
    large_from_index: Optional[pd.Timestamp] = None  # as-on date of the index member list used, if any

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
        rows = [{"date": s.date, **s.counts(), "size_metric": s.size_metric, "ranked": s.n_ranked,
                 "size_list_date": s.size_list_date.date() if s.size_list_date is not None else "",
                 "large_caps_from_index_list": s.large_from_index.date() if s.large_from_index is not None else ""}
                for s in self.snapshots.values()]
        return pd.DataFrame(rows).set_index("date").sort_index() if rows else pd.DataFrame()

    def size_metric_periods(self) -> pd.DataFrame:
        """Which size measure was used when: one row per unbroken run of decision dates."""
        tab = self.table()
        if tab.empty:
            return pd.DataFrame(columns=["size_metric", "first_decision", "last_decision", "decisions"])
        run = (tab["size_metric"] != tab["size_metric"].shift()).cumsum()
        out = tab.reset_index().groupby(run.to_numpy()).agg(
            size_metric=("size_metric", "first"), first_decision=("date", "first"),
            last_decision=("date", "last"), decisions=("date", "size"),
            median_ranked=("ranked", "median"), median_eligible=("eligible", "median"),
        )
        return out.reset_index(drop=True)


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


class SizeRanker:
    """Ranks all stocks by size on a decision date, using only lists and prices known that day.

    ``metrics`` is the order of preference (see the module docstring). On each
    date the first measure that ranks enough stocks is used for *every* stock,
    so ranks on one date are always comparable. "Enough" means as deep as the
    chosen segments need (``deepest_rank``), or ``MIN_COVERAGE`` of the stocks
    that traded recently if that is fewer.
    """

    def __init__(self, md: MarketData, metrics: Optional[List[str]], average_days: int,
                 large_cap_indices: Optional[List[str]] = None, deepest_rank: int = 0):
        self.md = md
        self.metrics = list(metrics or [])
        unknown = [m for m in self.metrics if m not in SIZE_METRICS]
        if unknown:
            raise ValueError(f"unknown size measure(s) {unknown}; choose from {list(SIZE_METRICS)}")
        self.large_cap_indices = list(large_cap_indices or [])
        self.deepest_rank = int(deepest_rank)
        self.recent = md.days_since_trade <= RECENT_TRADE_DAYS
        self.continuous: Dict[str, np.ndarray] = {}
        if "exchange_mcap" in self.metrics:
            self.continuous["exchange_mcap"] = rolling_mean(md.mcap_ff, average_days, min_periods=min(20, average_days))
        if "traded_value" in self.metrics:
            self.continuous["traded_value"] = md.median_value(average_days)

    def score(self, t: int, metric: str):
        """(score per stock, list date): higher score = bigger. ``(None, None)`` if the measure has no data."""
        md = self.md
        list_date = None
        if metric in self.continuous:
            score = self.continuous[metric][t].copy()
        else:
            rank, list_date = md.size_list(LIST_SOURCE[metric], md.dates[t])
            if rank is None:
                return None, None
            score = -rank  # a smaller rank is a bigger company
        score[~self.recent[t]] = np.nan
        return score, list_date

    def rank(self, t: int, metric: Optional[str] = None):
        """``(rank, metric used, list date, index-list date, number ranked)`` on decision row ``t``.

        ``rank`` is 1 for the largest stock and NaN for stocks the measure
        cannot rank. With ``metric`` given, that measure is used regardless of
        coverage (for comparing measures); otherwise the first usable one.
        """
        md, N = self.md, self.md.N
        needed = min(self.deepest_rank, int(MIN_COVERAGE * int(self.recent[t].sum())))
        chosen, s, list_date = "none", np.full(N, np.nan), None
        for m in ([metric] if metric else self.metrics):
            score, when = self.score(t, m)
            if score is None:
                continue
            n = int(np.isfinite(score).sum())
            if n > 0 and (metric is not None or n >= needed):
                chosen, s, list_date = m, score, when
                break
        n_ranked = int(np.isfinite(s).sum())

        large_date = None
        if self.large_cap_indices and n_ranked:
            members, large_date = md.index_member_flags(self.large_cap_indices, md.dates[t])
            if members is not None:
                # Index members take the first ranks (ordered by size among themselves). A member the
                # measure cannot rank is still a large cap: it is placed just below the ranked members.
                members = members & self.recent[t]
                finite = s[np.isfinite(s)]
                top, bottom = float(finite.max()), float(finite.min())
                s = np.where(members, np.where(np.isfinite(s), s, bottom) + (top - bottom) + 1.0, s)
                n_ranked = int(np.isfinite(s).sum())

        order = np.argsort(-np.nan_to_num(s, nan=-np.inf), kind="stable")
        rank = np.full(N, np.nan)
        rank[order[:n_ranked]] = np.arange(1, n_ranked + 1)
        return rank, chosen, list_date, large_date, n_ranked


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

    ranker = SizeRanker(md, cfg.size_metrics if cfg.source == "mcap_rank" else [], cfg.mcap_average_days,
                        cfg.large_cap_indices if cfg.source == "mcap_rank" else [],
                        deepest_rank=max((cfg.rank_bounds[seg][1] for seg in (cfg.segments or [])
                                          if seg in cfg.rank_bounds), default=0))

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
        rank, size_metric, list_date, large_date, n_ranked = ranker.rank(t)

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
            eligible=eligible, reason=reason,
            size_metric=size_metric if cfg.source == "mcap_rank" else "index_constituents",
            size_list_date=list_date, n_ranked=n_ranked, large_from_index=large_date,
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
                            window: int = 60, size_metrics: Optional[List[str]] = None,
                            large_cap_indices: Optional[List[str]] = None) -> pd.DataFrame:
    """How liquid is each size segment? One row per (year-end, segment).

    Descriptive facts to inform the group's choice of liquidity filter, fund
    size and participation cap: the number of stocks in each segment and the
    spread of their typical daily traded value (in Rs crore), plus how many
    are low-priced or outside the normal EQ series. Stocks are ranked exactly
    as the universe ranks them, on the last trading day of each calendar year;
    ``size_metric`` says which size measure was available that year.
    """
    metrics = list(size_metrics) if size_metrics else list(SIZE_METRICS)
    deepest = max((hi for _, hi in rank_bounds.values()), default=0)
    ranker = SizeRanker(md, metrics, mcap_average_days, large_cap_indices, deepest_rank=deepest)
    med = md.median_value(window)
    locked = md.lock_fraction(window)
    years = pd.Series(md.dates.year, index=np.arange(md.T))
    year_ends = years.groupby(years).apply(lambda s: s.index[-1]).to_numpy()
    rows = []
    for t in year_ends:
        rank, metric, _, _, n_ranked = ranker.rank(int(t))
        if not n_ranked:
            continue
        for name, (lo, hi) in rank_bounds.items():
            m = (rank >= lo) & (rank <= hi)
            if not m.any():
                continue
            v = med[t, m] / 1e7
            px = md.close_ff[t, m]
            caps = md.mcap_ff[t, m]
            rows.append({
                "date": md.dates[t].date(), "segment": name, "size_metric": metric, "stocks": int(m.sum()),
                "median_mcap_cr": float(np.nanmedian(caps) / 1e7) if np.isfinite(caps).any() else np.nan,
                "traded_value_cr_p10": float(np.nanpercentile(v, 10)),
                "traded_value_cr_p25": float(np.nanpercentile(v, 25)),
                "traded_value_cr_median": float(np.nanmedian(v)),
                "traded_value_cr_p75": float(np.nanpercentile(v, 75)),
                "share_price_below_20": float(np.mean(px < 20)),
                "share_not_in_EQ_series": float(np.mean(md.series[t, m] > 0)),
                "share_locked_10pct_of_days": float(np.mean(locked[t, m] > 0.10)),
            })
    return pd.DataFrame(rows).set_index(["date", "segment"]) if rows else pd.DataFrame()


def size_metric_comparison(md: MarketData, rank_bounds: Dict[str, List[int]], mcap_average_days: int = 126,
                           large_cap_indices: Optional[List[str]] = None) -> pd.DataFrame:
    """How far do the size measures agree? One row per (half-year end, pair of measures).

    On every end-June and end-December where two measures are both available,
    the stocks are ranked by each and compared: the rank correlation among the
    stocks either measure puts in the top ``deepest`` ranks, and the share of
    each segment under the first measure that the second puts in the same
    segment. Descriptive only - it tells the group how much a universe built
    on one measure differs from one built on another (for example how well
    traded value stands in for market capitalisation in the years when both
    exist), which is theirs to weigh.
    """
    deepest = max((hi for _, hi in rank_bounds.values()), default=0)
    ranker = SizeRanker(md, list(SIZE_METRICS), mcap_average_days, large_cap_indices, deepest_rank=deepest)
    months = pd.Series(md.dates.month, index=np.arange(md.T))
    key = pd.Series(md.dates.year * 100 + md.dates.month, index=np.arange(md.T))
    ends = [int(t) for t in key.groupby(key).apply(lambda s: s.index[-1]).to_numpy() if months[t] in (6, 12)]

    def segments(rank: np.ndarray) -> np.ndarray:
        seg = np.full(md.N, "", dtype=object)
        for name, (lo, hi) in rank_bounds.items():
            seg[(rank >= lo) & (rank <= hi)] = name
        return seg

    rows = []
    for t in ends:
        ranks = {}
        for metric in SIZE_METRICS:
            score, _ = ranker.score(t, metric)
            if score is not None and np.isfinite(score).sum() >= min(deepest, 50):
                ranks[metric] = ranker.rank(t, metric)[0]
        names = list(ranks)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ra, rb = ranks[a], ranks[b]
                both = np.isfinite(ra) & np.isfinite(rb) & ((ra <= deepest) | (rb <= deepest))
                if both.sum() < 20:
                    continue
                corr = float(pd.Series(ra[both]).corr(pd.Series(rb[both]), method="spearman"))
                sa, sb = segments(ra), segments(rb)
                row = {"date": md.dates[t].date(), "measure_a": a, "measure_b": b,
                       "stocks_compared": int(both.sum()), "rank_correlation": round(corr, 3)}
                for name in rank_bounds:
                    in_a = (sa == name) & np.isfinite(rb)
                    row[f"same_segment_{name}"] = round(float(np.mean(sb[in_a] == name)), 3) if in_a.any() else np.nan
                rows.append(row)
    return pd.DataFrame(rows)
