"""MarketData: the panel reshaped into (trading days x companies) arrays.

The long panel (one row per stock per day) is convenient to build and audit;
the back-test needs fast "all stocks on day t" access. This class holds one
2-D NumPy array per field, all aligned on the same ``dates`` (rows) and ``sids``
(columns), plus index levels and the daily risk-free return.

Conventions
-----------
* A cell is NaN (or False / 0 for flags) when the stock did not trade that day.
* ``*_ff`` arrays carry the last traded value forward, i.e. "the latest price
  you could have known" - never a future one.
* Row ``t`` contains only information published by the end of day ``t``.
"""

from __future__ import annotations

import warnings
from functools import cached_property
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .config import DataConfig
from .data.external import daily_risk_free, load_benchmark_tri, load_risk_free
from .data.ingest import read_interim
from .data.panel import load_index_members, load_panel, load_securities, load_size_lists
from .frictions import ffill_rows, rolling_mean, rolling_median, rolling_std
from .utils import ns_index

SERIES_CODE = {"EQ": 0, "BE": 1, "BZ": 2}  # anything else -> 3; not traded -> -1
PANEL_FIELDS = ["date", "sid", "series", "open", "close", "vwap", "value", "ret_total", "tri", "pri", "mcap",
                "lock", "cs2d", "arprod"]


class MarketData:
    """Aligned wide arrays. Build with :meth:`load` (from disk) or :meth:`from_frames`."""

    def __init__(
        self,
        dates: pd.DatetimeIndex,
        sids: np.ndarray,
        fields: Dict[str, np.ndarray],
        securities: pd.DataFrame,
        indices: pd.DataFrame,
        rf_daily: pd.Series,
        data_kind: str = "real",
        size_lists: Optional[pd.DataFrame] = None,
        index_members: Optional[pd.DataFrame] = None,
        list_ages: Optional[Dict[str, int]] = None,
    ):
        self.dates = dates
        self.sids = sids
        self.T, self.N = len(dates), len(sids)
        self.close = fields["close"]
        self.open = fields["open"]
        self.vwap = fields["vwap"]
        self.value = fields["value"]
        self.ret = fields["ret"]
        self.tri = fields["tri"]
        self.pri = fields["pri"]
        self.mcap = fields["mcap"]
        self.series = fields["series"]
        self.lock = fields["lock"]
        self.cs2d = fields["cs2d"]  # daily inputs of the spread estimators (see data/panel.py)
        self.arprod = fields["arprod"]
        self.traded = fields["traded"]
        self.securities = securities.set_index("sid").reindex(sids)
        self.symbols = self.securities["symbol"].fillna("?").to_numpy(dtype=object)
        self.sector = self.securities["sector"].fillna("UNKNOWN").to_numpy(dtype=object)
        self.indices = indices  # dates x index name (levels, forward-filled)
        self.rf_daily = rf_daily.reindex(dates).fillna(0.0).to_numpy()
        self.rf_missing = bool(rf_daily.attrs.get("missing", False))
        self.data_kind = data_kind
        # Dated size lists and index member lists (see data/size.py). Every lookup below takes
        # the decision date and returns only lists already in force on it.
        self.size_lists = size_lists if size_lists is not None else pd.DataFrame()
        self.index_members = index_members if index_members is not None else pd.DataFrame()
        self.list_ages = {"size": 400, "index": 100, **(list_ages or {})}  # days a list stays usable

    # ------------------------------------------------------------------ builders
    @classmethod
    def from_frames(
        cls,
        panel: pd.DataFrame,
        securities: pd.DataFrame,
        indices_long: Optional[pd.DataFrame] = None,
        rf_annual: Optional[pd.Series] = None,
        benchmark_tri: Optional[pd.DataFrame] = None,
        data_kind: str = "real",
        size_lists: Optional[pd.DataFrame] = None,
        index_members: Optional[pd.DataFrame] = None,
        list_ages: Optional[Dict[str, int]] = None,
    ) -> "MarketData":
        dates = ns_index(np.sort(panel["date"].unique()))
        sids = np.sort(panel["sid"].unique())
        r = np.searchsorted(dates.values, panel["date"].to_numpy().astype("datetime64[ns]"))
        c = np.searchsorted(sids, panel["sid"].to_numpy())
        T, N = len(dates), len(sids)

        def wide(col: str, dtype, fill) -> np.ndarray:
            a = np.full((T, N), fill, dtype=dtype)
            a[r, c] = panel[col].to_numpy()
            return a

        fields = {
            "close": wide("close", np.float32, np.nan),
            "open": wide("open", np.float32, np.nan),
            "vwap": wide("vwap", np.float32, np.nan),
            "value": wide("value", np.float64, np.nan),
            "ret": wide("ret_total", np.float64, np.nan),
            "tri": wide("tri", np.float64, np.nan),
            "pri": wide("pri", np.float64, np.nan),
            "mcap": wide("mcap", np.float64, np.nan),
            "lock": wide("lock", np.int8, 0),
            "cs2d": wide("cs2d", np.float32, np.nan),
            "arprod": wide("arprod", np.float32, np.nan),
        }
        codes = panel["series"].map(SERIES_CODE).fillna(3).astype(np.int8)
        series = np.full((T, N), -1, np.int8)
        series[r, c] = codes.to_numpy()
        fields["series"] = series
        fields["traded"] = series >= 0

        # index levels: exchange price indices, plus any total-return series the group supplied
        frames = []
        if indices_long is not None and len(indices_long):
            lv = indices_long.pivot_table(index="date", columns="index_name", values="close", aggfunc="last")
            lv.index = ns_index(lv.index)
            frames.append(lv)
        if benchmark_tri is not None and len(benchmark_tri):
            tri = benchmark_tri.copy()
            tri.index = ns_index(tri.index)
            tri.columns = [f"{c} TRI" if not str(c).endswith("TRI") else str(c) for c in tri.columns]
            frames.append(tri)
        if frames:
            indices = pd.concat(frames, axis=1)
            indices = indices.reindex(indices.index.union(dates)).sort_index().ffill().reindex(dates)
        else:
            indices = pd.DataFrame(index=dates)
        rf = daily_risk_free(rf_annual if rf_annual is not None else pd.Series(dtype=float), dates)
        return cls(dates, sids, fields, securities, indices, rf, data_kind, size_lists, index_members, list_ages)

    @classmethod
    def load(cls, cfg: DataConfig, start: Optional[str] = None, end: Optional[str] = None) -> "MarketData":
        """Read the processed panel and everything aligned to it."""
        panel = load_panel(cfg, start, end, columns=PANEL_FIELDS)
        return cls.from_frames(
            panel,
            load_securities(cfg),
            read_interim(cfg, "indices"),
            load_risk_free(cfg, verbose=False),
            load_benchmark_tri(cfg),
            cfg.data_kind,
            load_size_lists(cfg),
            load_index_members(cfg),
            {"size": cfg.size_list_max_age_days, "index": cfg.index_list_max_age_days},
        )

    # ------------------------------------------------------------------ lookups
    def loc(self, date) -> int:
        """Row of the last trading day on or before ``date``."""
        i = int(np.searchsorted(self.dates.values, np.datetime64(pd.Timestamp(date), "ns"), side="right")) - 1
        if i < 0:
            raise KeyError(f"{date} is before the first trading day in the data ({self.dates[0].date()})")
        return i

    def first_on_or_after(self, date) -> int:
        i = int(np.searchsorted(self.dates.values, np.datetime64(pd.Timestamp(date), "ns"), side="left"))
        return min(i, self.T - 1)

    def index_level(self, name: str) -> np.ndarray:
        key = name.strip().upper()
        if key not in self.indices.columns:
            raise KeyError(
                f"Index '{name}' not found. Available: {sorted(self.indices.columns)[:40]} ..."
            )
        return self.indices[key].to_numpy(float)

    def index_return(self, name: str) -> np.ndarray:
        lv = self.index_level(name)
        out = np.full(self.T, np.nan)
        out[1:] = lv[1:] / lv[:-1] - 1.0
        return out

    # ------------------------------------------------------------------ dated lists
    def size_list(self, source: str, date) -> tuple:
        """The ``source`` size list in force on ``date``: ``(rank per company, as-on date)``.

        ``rank`` is aligned to ``sids`` (NaN = not in the list). The list in
        force is the latest one whose ``effective`` date is on or before
        ``date``; it is dropped once older than the maximum age. Returns
        ``(None, None)`` when no list is in force.
        """
        def make():
            lists = self.size_lists
            out = {}
            if len(lists):
                for (src, as_of), part in lists.groupby(["source", "as_of"], sort=True):
                    rank = np.full(self.N, np.nan)
                    col = np.searchsorted(self.sids, part["sid"].to_numpy())
                    ok = (col < self.N) & (self.sids[np.minimum(col, self.N - 1)] == part["sid"].to_numpy())
                    rank[col[ok]] = part["rank"].to_numpy(float)[ok]
                    out.setdefault(src, []).append((pd.Timestamp(part["effective"].iloc[0]), pd.Timestamp(as_of), rank))
            return out
        date = pd.Timestamp(date)
        best = None
        for effective, as_of, rank in self._cache(("size_list_index",), make).get(source, []):
            if effective <= date and (date - as_of).days <= self.list_ages["size"]:
                best = (rank, as_of)  # lists are in date order, so the last match is the latest
        return best if best is not None else (None, None)

    def index_member_flags(self, names, date) -> tuple:
        """Members of the named indices on ``date``: ``(bool per company, as-on date)``.

        Uses the latest monthly member list dated on or before ``date`` (index
        membership is announced in advance, so the month-end list is public on
        that day). ``(None, None)`` if no list is recent enough.
        """
        def make():
            mem = self.index_members
            out = {}
            if len(mem):
                for (as_of, name), part in mem.groupby(["as_of", "index_name"], sort=True):
                    col = np.searchsorted(self.sids, part["sid"].to_numpy())
                    ok = (col < self.N) & (self.sids[np.minimum(col, self.N - 1)] == part["sid"].to_numpy())
                    flags = np.zeros(self.N, bool)
                    flags[col[ok]] = True
                    out.setdefault(pd.Timestamp(as_of), {})[name] = flags
            return sorted(out.items())
        date = pd.Timestamp(date)
        wanted = [str(n).strip().upper() for n in names]
        best = None
        for as_of, by_name in self._cache(("index_member_index",), make):
            if as_of > date:
                break
            if (date - as_of).days <= self.list_ages["index"] and all(n in by_name for n in wanted):
                flags = np.zeros(self.N, bool)
                for n in wanted:
                    flags |= by_name[n]
                best = (flags, as_of)
        return best if best is not None else (None, None)

    # ------------------------------------------------------------------ derived arrays (computed once)
    @cached_property
    def tri_ff(self) -> np.ndarray:
        """Total-return index carried forward over non-trading days."""
        return ffill_rows(self.tri)

    @cached_property
    def pri_ff(self) -> np.ndarray:
        return ffill_rows(self.pri)

    @cached_property
    def close_ff(self) -> np.ndarray:
        return ffill_rows(self.close.astype(np.float64))

    @cached_property
    def mcap_ff(self) -> np.ndarray:
        return ffill_rows(self.mcap)

    @cached_property
    def first_row(self) -> np.ndarray:
        """Row of each company's first trade (T if it never trades in the window)."""
        any_trade = self.traded.any(axis=0)
        return np.where(any_trade, self.traded.argmax(axis=0), self.T)

    @cached_property
    def last_row(self) -> np.ndarray:
        any_trade = self.traded.any(axis=0)
        return np.where(any_trade, self.T - 1 - self.traded[::-1].argmax(axis=0), -1)

    @cached_property
    def age(self) -> np.ndarray:
        """Market days since first trade (negative before listing)."""
        return np.arange(self.T)[:, None] - self.first_row[None, :]

    @cached_property
    def days_since_trade(self) -> np.ndarray:
        """0 if traded today, k if the last trade was k market days ago (large before listing)."""
        rows = np.arange(self.T)[:, None]
        last = np.where(self.traded, rows, -1)
        np.maximum.accumulate(last, axis=0, out=last)
        return np.where(last >= 0, rows - last, 10 ** 6)

    @cached_property
    def value0(self) -> np.ndarray:
        """Traded value with non-trading days as 0 once listed (NaN before listing)."""
        v = np.where(self.traded, self.value, 0.0)
        v[self.age < 0] = np.nan
        return v

    def adv(self, window: int) -> np.ndarray:
        """Average daily traded value over the trailing ``window`` market days (incl. today)."""
        return self._cache(("adv", window), lambda: rolling_mean(self.value0, window, max(2, window // 2)))

    def median_value(self, window: int) -> np.ndarray:
        return self._cache(("medval", window), lambda: rolling_median(self.value0, window, max(2, window // 2)))

    def traded_fraction(self, window: int) -> np.ndarray:
        """Share of the trailing ``window`` market days on which the stock traded."""
        def make():
            x = self.traded.astype(float)
            x[self.age < 0] = np.nan
            return rolling_mean(x, window, max(2, window // 2))
        return self._cache(("tradedfrac", window), make)

    def lock_fraction(self, window: int) -> np.ndarray:
        """Share of the trailing ``window`` market days closed at a price band."""
        return self._cache(("lockfrac", window),
                           lambda: rolling_mean((self.lock != 0).astype(float), window, max(2, window // 2)))

    def volatility(self, window: int) -> np.ndarray:
        """Daily standard deviation of total returns over the trailing ``window`` market days."""
        return self._cache(("vol", window), lambda: rolling_std(self.ret, window, max(10, window // 3)))

    def half_spread(self, model: str, window: int = 21, fixed_bps: Optional[float] = None,
                    max_bps: float = 250.0) -> np.ndarray:
        """Estimated half bid-ask spread (fraction of price) for every stock and day.

        ``corwin_schultz``  mean of the non-negative two-day estimates over ``window`` days
        ``abdi_ranaldo``    sqrt(4 x mean of the daily products) over ``window`` days
        ``fixed``           the same ``fixed_bps`` for every stock

        The estimate is floored at half a tick (the quoted spread cannot be
        narrower than one tick of Rs 0.05) and capped at ``max_bps``. A stock
        with no estimate gets the day's cross-sectional median.
        """
        def make():
            from .frictions import TICK

            if model == "fixed":
                half = np.full((self.T, self.N), float(fixed_bps) / 1e4)
            else:
                if model == "corwin_schultz":
                    x = np.where(np.isfinite(self.cs2d), np.maximum(self.cs2d, 0.0), np.nan)
                    spread = rolling_mean(x, window, max(5, window // 2))
                elif model == "abdi_ranaldo":
                    m = rolling_mean(self.arprod.astype(float), window, max(5, window // 2))
                    spread = np.sqrt(np.maximum(4.0 * m, 0.0))
                else:
                    raise ValueError(f"unknown spread model '{model}'")
                half = spread / 2.0
                with warnings.catch_warnings():  # a day with no estimate at all is expected early on
                    warnings.simplefilter("ignore", RuntimeWarning)
                    median = np.nanmedian(half, axis=1)
                median = np.where(np.isfinite(median), median, 0.0)
                half = np.where(np.isfinite(half), half, median[:, None])
            with np.errstate(divide="ignore", invalid="ignore"):
                floor = 0.5 * TICK / self.close_ff
            half = np.maximum(half, np.where(np.isfinite(floor), floor, 0.0))
            return np.minimum(half, max_bps / 1e4)
        return self._cache(("half_spread", model, window, fixed_bps, max_bps), make)

    def _cache(self, key, make):
        store = self.__dict__.setdefault("_derived", {})
        if key not in store:
            store[key] = make()
        return store[key]

    # ------------------------------------------------------------------ misc
    def truncate(self, end) -> "MarketData":
        """A copy that ends at ``end`` - used by the no-look-ahead test."""
        k = self.loc(end) + 1
        fields = {
            "close": self.close[:k], "open": self.open[:k], "vwap": self.vwap[:k], "value": self.value[:k],
            "ret": self.ret[:k], "tri": self.tri[:k], "pri": self.pri[:k], "mcap": self.mcap[:k],
            "series": self.series[:k], "lock": self.lock[:k], "traded": self.traded[:k],
            "cs2d": self.cs2d[:k], "arprod": self.arprod[:k],
        }
        rf = pd.Series(self.rf_daily[:k], index=self.dates[:k])
        rf.attrs["missing"] = self.rf_missing
        # Lists dated after the cut are removed too, so the copy holds nothing from the future.
        cut = self.dates[k - 1]
        lists = self.size_lists[self.size_lists["effective"] <= cut] if len(self.size_lists) else self.size_lists
        members = self.index_members[self.index_members["as_of"] <= cut] if len(self.index_members) else self.index_members
        return MarketData(self.dates[:k], self.sids, fields, self.securities.reset_index(),
                          self.indices.iloc[:k], rf, self.data_kind, lists, members, self.list_ages)

    def describe(self) -> str:
        return (f"MarketData[{self.data_kind}]: {self.T} trading days "
                f"({self.dates[0].date()} to {self.dates[-1].date()}), {self.N} companies, "
                f"{self.indices.shape[1]} index series, risk-free {'MISSING' if self.rf_missing else 'ok'}")
