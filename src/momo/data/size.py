"""Point-in-time size information for the years before the daily market-cap file.

The exchange's daily market-capitalisation file (inside the PR zip) starts in
January 2024. For earlier years the exchange published two other things, and
this module turns them into tables keyed by company id:

``size_lists``
    The "market capitalisation - all companies" lists (31 March 2019 onwards,
    once or twice a year): every listed company's rank and, from 2020, its
    market capitalisation on the as-on date. The group's own AMFI lists (see
    ``external.load_amfi_lists``) are stored in the same table.

``index_members``
    The members of the Nifty 50 and the Nifty Next 50 at each month-end
    (2008 onwards) - together the Nifty 100, a dated list of the large caps.

and it fills the panel's daily ``mcap`` column for the days on which the
exchange published no daily figure:

    market cap on day t  =  market cap in the latest list in force
                            x  price index on t / price index on the list date

The price index is adjusted for splits and bonuses, so the estimate tracks
"shares on the list date x today's price". It ignores shares issued or bought
back since the list date - at most about a year - and that is its error.

Nothing here looks forward: a list is used only from its ``effective`` date
(as-on date plus the publication lag in ``config/data.yaml``).
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import DataConfig
from ..utils import to_ns

SIZE_LIST_COLUMNS = ["source", "as_of", "effective", "sid", "symbol", "rank", "mcap"]
INDEX_MEMBER_COLUMNS = ["as_of", "index_name", "sid", "symbol"]
MCAP_SRC_NONE, MCAP_SRC_DAILY, MCAP_SRC_LIST = 0, 1, 2
ANCHOR_TOLERANCE_DAYS = 10  # a list value is anchored to a price at most this many days before the as-on date


# --------------------------------------------------------------------------- #
# Symbols -> company ids, as at a date
# --------------------------------------------------------------------------- #
def symbol_spans(panel: pd.DataFrame) -> pd.DataFrame:
    """First and last trading date of every (company, symbol) pair in the panel."""
    spans = panel.groupby(["sid", "symbol"], sort=False)["date"].agg(first="min", last="max").reset_index()
    return to_ns(spans)


def isin_spans(panel: pd.DataFrame) -> pd.DataFrame:
    """First and last trading date of every (company, ISIN) pair in the panel."""
    has = panel[panel["isin"] != ""]
    spans = has.groupby(["sid", "isin"], sort=False)["date"].agg(first="min", last="max").reset_index()
    return to_ns(spans)


def map_asof(spans: pd.DataFrame, keys: pd.Series, as_of: pd.Timestamp, key: str = "symbol",
             tolerance_days: int = 45) -> np.ndarray:
    """Company id of each key (symbol or ISIN) as at ``as_of``; -1 where there is none.

    A key belongs to the company that traded under it on ``as_of``. If no
    company did, the one whose use of the key ended or began closest to
    ``as_of`` is taken, provided that is within ``tolerance_days`` - which
    covers a stock that was suspended on the day, and keeps a symbol that was
    later re-used by another company apart from its earlier owner.
    """
    as_of = pd.Timestamp(as_of)
    before = (spans["first"] - as_of).dt.days.clip(lower=0)
    after = (as_of - spans["last"]).dt.days.clip(lower=0)
    gap = (before + after).to_numpy()
    ok = gap <= tolerance_days
    cand = spans.loc[ok, ["sid", key, "last"]].assign(gap=gap[ok])
    cand = cand.sort_values(["gap", "last"], ascending=[True, False]).drop_duplicates(key)
    lookup = dict(zip(cand[key], cand["sid"]))
    return np.array([lookup.get(k, -1) for k in keys.to_numpy()], dtype=np.int64)


# --------------------------------------------------------------------------- #
# Lists -> tables keyed by company id
# --------------------------------------------------------------------------- #
def _rank_within(df: pd.DataFrame) -> pd.Series:
    """Rank inside one list after mapping: by market cap where published, else by the list's own order."""
    if df["mcap"].notna().any():
        ranked = df["mcap"].rank(ascending=False, method="first")
        return ranked.where(df["mcap"].notna())  # "not traded" rows carry no value and stay unranked
    return df["rank"].rank(method="first")


def build_size_lists(cfg: DataConfig, panel: pd.DataFrame, exchange_lists: pd.DataFrame,
                     amfi_lists: Optional[pd.DataFrame] = None, stats: Optional[dict] = None) -> pd.DataFrame:
    """Map the exchange's (and the group's AMFI) lists to company ids -> ``SIZE_LIST_COLUMNS``."""
    lag = pd.Timedelta(days=int(cfg.size_list_lag_days))
    frames = []
    report: Dict[str, dict] = {}

    if exchange_lists is not None and len(exchange_lists):
        spans = symbol_spans(panel)
        for as_of, part in exchange_lists.groupby("as_of", sort=True):
            sid = map_asof(spans, part["symbol"], as_of, "symbol")
            got = part.assign(sid=sid)
            got = got[got["sid"] >= 0].drop_duplicates("sid")
            got = got.assign(rank=_rank_within(got))
            got = got[got["rank"].notna()]
            frames.append(got.assign(source="nse_list")[["source", "as_of", "sid", "symbol", "rank", "mcap"]])
            report[f"nse_list {pd.Timestamp(as_of).date()}"] = {
                "rows_in_file": int(len(part)), "matched_to_panel": int((sid >= 0).sum()),
                "ranked": int(len(got)), "has_values": bool(got["mcap"].notna().any()),
            }

    if amfi_lists is not None and len(amfi_lists):
        spans = isin_spans(panel)
        for as_of, part in amfi_lists.groupby("as_of", sort=True):
            # generous tolerance: an AMFI list covers six months and ISINs change on splits
            sid = map_asof(spans, part["isin"], as_of, "isin", tolerance_days=200)
            got = part.assign(sid=sid)
            got = got[got["sid"] >= 0].drop_duplicates("sid")
            got = got.assign(rank=_rank_within(got))
            got = got[got["rank"].notna()]
            frames.append(got.assign(source="amfi")[["source", "as_of", "sid", "symbol", "rank", "mcap"]])
            report[f"amfi {pd.Timestamp(as_of).date()}"] = {
                "rows_in_file": int(len(part)), "matched_to_panel": int((sid >= 0).sum()),
                "ranked": int(len(got)), "has_values": bool(got["mcap"].notna().any()),
            }

    if stats is not None:
        stats["size_lists"] = report
    if not frames:
        return pd.DataFrame(columns=SIZE_LIST_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["effective"] = out["as_of"] + lag
    out["sid"] = out["sid"].astype(np.int64)
    return to_ns(out[SIZE_LIST_COLUMNS].sort_values(["source", "as_of", "rank"]).reset_index(drop=True))


def build_index_members(panel: pd.DataFrame, index_weights: pd.DataFrame,
                        stats: Optional[dict] = None) -> pd.DataFrame:
    """Map the monthly index member files to company ids -> ``INDEX_MEMBER_COLUMNS``."""
    if index_weights is None or index_weights.empty:
        return pd.DataFrame(columns=INDEX_MEMBER_COLUMNS)
    spans = symbol_spans(panel)
    frames = []
    unmatched = 0
    for as_of, part in index_weights.groupby("as_of", sort=True):
        sid = map_asof(spans, part["symbol"], as_of, "symbol")
        unmatched += int((sid < 0).sum())
        got = part.assign(sid=sid)
        frames.append(got.loc[got["sid"] >= 0, INDEX_MEMBER_COLUMNS])
    out = pd.concat(frames, ignore_index=True).drop_duplicates(["as_of", "index_name", "sid"])
    out["sid"] = out["sid"].astype(np.int64)
    if stats is not None:
        stats["index_member_files"] = int(index_weights["as_of"].nunique())
        stats["index_member_rows_not_matched"] = unmatched  # e.g. DVR shares, which are not in the panel
    return to_ns(out.sort_values(["as_of", "index_name", "symbol"]).reset_index(drop=True))


# --------------------------------------------------------------------------- #
# Daily market cap before the daily file
# --------------------------------------------------------------------------- #
def estimate_mcap(panel: pd.DataFrame, size_lists: pd.DataFrame, max_age_days: int,
                  stats: Optional[dict] = None) -> Tuple[np.ndarray, np.ndarray]:
    """Daily market cap for every panel row, and where each figure came from.

    ``panel`` needs ``sid``, ``date``, ``close``, ``pri`` and ``shares`` (the
    shares outstanding published in the exchange's daily file; NaN before it
    starts). Returns ``(mcap, source)`` aligned to the rows of ``panel``, with
    source 1 = daily file (shares x close), 2 = latest list in force carried
    forward with the price index, 0 = no figure.
    """
    panel = panel.reset_index(drop=True)  # row position = index label from here on
    n = len(panel)
    published = (panel["close"] * panel["shares"]).to_numpy(float)
    mcap = published.copy()
    src = np.where(np.isfinite(published), MCAP_SRC_DAILY, MCAP_SRC_NONE).astype(np.int8)

    lists = size_lists[(size_lists["source"] == "nse_list") & size_lists["mcap"].notna()] if len(size_lists) else size_lists
    if lists is None or lists.empty:
        return mcap, src

    # 1. the price index on the list's as-on date (the last trade on or before it)
    px = to_ns(panel[["sid", "date", "pri"]]).sort_values("date")
    anchors = pd.merge_asof(
        to_ns(lists[["sid", "as_of", "effective", "mcap"]]).sort_values("as_of"), px.rename(columns={"pri": "pri_anchor"}),
        left_on="as_of", right_on="date", by="sid", direction="backward",
        tolerance=pd.Timedelta(days=ANCHOR_TOLERANCE_DAYS),
    ).dropna(subset=["pri_anchor"])
    anchors = anchors[anchors["pri_anchor"] > 0][["sid", "effective", "mcap", "pri_anchor"]]
    if anchors.empty:
        return mcap, src

    # 2. for every panel row, the latest list in force (effective on or before the row's date)
    rows = px.assign(row=px.index.to_numpy())
    rows = pd.merge_asof(
        rows, anchors.sort_values("effective"), left_on="date", right_on="effective", by="sid",
        direction="backward", tolerance=pd.Timedelta(days=int(max_age_days)),
    )
    est = (rows["mcap"] * rows["pri"] / rows["pri_anchor"]).to_numpy(float)
    est_full = np.full(n, np.nan)
    est_full[rows["row"].to_numpy()] = est

    use = ~np.isfinite(published) & np.isfinite(est_full) & (est_full > 0)
    mcap[use] = est_full[use]
    src[use] = MCAP_SRC_LIST

    if stats is not None:
        both = np.isfinite(published) & np.isfinite(est_full) & (est_full > 0) & (published > 0)
        stats["mcap_rows_from_daily_file"] = int((src == MCAP_SRC_DAILY).sum())
        stats["mcap_rows_from_lists"] = int((src == MCAP_SRC_LIST).sum())
        stats["mcap_rows_without_a_figure"] = int((src == MCAP_SRC_NONE).sum())
        if both.any():
            # Where both exist the daily file is used; the comparison shows how good the estimate is.
            ratio = est_full[both] / published[both]
            stats["list_estimate_vs_daily_file"] = {
                "rows_compared": int(both.sum()),
                "median_ratio": round(float(np.median(ratio)), 4),
                "share_within_5pct": round(float(np.mean(np.abs(ratio - 1.0) <= 0.05)), 4),
                "share_within_20pct": round(float(np.mean(np.abs(ratio - 1.0) <= 0.20)), 4),
            }
    return mcap, src
