"""One permanent id per company, across renames and ISIN changes.

Why this matters
----------------
NSE identifies a stock by its *symbol*, which changes when a company is renamed,
and by its *ISIN*, which changes when the face value changes (a split or
consolidation). If either change were treated as "old stock died, new stock
born", the back-test would (a) lose the price history a momentum signal needs
and (b) record a spurious delisting. Conversely, a symbol can be *re-used*
years later by an unrelated company, and that must NOT be linked.

Method (a graph problem solved with union-find)
-----------------------------------------------
Each distinct (symbol, ISIN) pair seen in the price files is a node, with the
first and last date it traded. Two nodes are the same company when:

1. they share an ISIN (an ISIN identifies exactly one security); or
2. they share a symbol and the second starts no more than
   ``reuse_gap_days`` after the first ends (an ISIN change on a split); or
3. NSE's own symbol-change list links the old symbol to the new one around
   that date (a rename, useful where the old files carry no ISIN).

The connected components are the companies. Each gets an integer ``sid``.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path halving
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def assign_security_ids(
    prices: pd.DataFrame,
    symbol_changes: Optional[pd.DataFrame] = None,
    reuse_gap_days: int = 180,
    rename_window_days: int = 20,
) -> pd.DataFrame:
    """Return ``prices`` with a ``sid`` column (int), one per company.

    ``prices`` needs columns ``date``, ``symbol``, ``isin`` (blank ISIN allowed).
    """
    nodes = (
        prices.groupby(["symbol", "isin"], sort=False)["date"]
        .agg(first="min", last="max", n="size")
        .reset_index()
        .sort_values(["first", "symbol", "isin"])
        .reset_index(drop=True)
    )
    uf = _UnionFind(len(nodes))
    gap = pd.Timedelta(days=reuse_gap_days)

    # Rule 1: same ISIN.
    with_isin = nodes[nodes["isin"] != ""]
    for _, idx in with_isin.groupby("isin").groups.items():
        idx = list(idx)
        for other in idx[1:]:
            uf.union(idx[0], other)

    # Rule 2: same symbol, continuous in time (allowing a short gap).
    for _, grp in nodes.groupby("symbol"):
        grp = grp.sort_values("first")
        ids, firsts, lasts = grp.index.to_list(), grp["first"].to_list(), grp["last"].to_list()
        running_last = lasts[0]
        for k in range(1, len(ids)):
            if firsts[k] - running_last <= gap:
                uf.union(ids[k - 1], ids[k])
            running_last = max(running_last, lasts[k])

    # Rule 3: exchange-published renames.
    if symbol_changes is not None and len(symbol_changes):
        window = pd.Timedelta(days=rename_window_days)
        by_symbol = {s: g for s, g in nodes.groupby("symbol")}
        for old, new, when in symbol_changes[["old_symbol", "new_symbol", "change_date"]].itertuples(index=False):
            g_old, g_new = by_symbol.get(old), by_symbol.get(new)
            if g_old is None or g_new is None:
                continue
            before = g_old[g_old["first"] <= when + window]
            after = g_new[g_new["last"] >= when - window]
            if before.empty or after.empty:
                continue
            a = before["last"].idxmax()  # the old symbol's most recent stretch
            b = after["first"].idxmin()  # the new symbol's earliest stretch
            if nodes.at[b, "first"] - nodes.at[a, "last"] <= gap:
                uf.union(a, b)

    roots = np.array([uf.find(i) for i in range(len(nodes))])
    # Number companies in order of first appearance so ids are stable and readable.
    first_by_root = nodes.groupby(roots)["first"].min().sort_values(kind="stable")
    sid_of_root: Dict[int, int] = {root: k for k, root in enumerate(first_by_root.index)}
    nodes["sid"] = [sid_of_root[r] for r in roots]

    out = prices.merge(nodes[["symbol", "isin", "sid"]], on=["symbol", "isin"], how="left")
    out["sid"] = out["sid"].astype("int64")
    return out


def resolve_duplicates(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """At most one row per (sid, date): keep the most-traded line, report the rest.

    Duplicates arise when one company trades under two lines on the same day
    (for example an old and a new symbol overlapping for a session).
    """
    dup = prices.duplicated(["sid", "date"], keep=False)
    if not dup.any():
        return prices, prices.iloc[0:0]
    ordered = prices.sort_values(["sid", "date", "value"], ascending=[True, True, False])
    kept = ordered.drop_duplicates(["sid", "date"], keep="first")
    dropped = ordered[ordered.duplicated(["sid", "date"], keep="first")]
    return kept.sort_values(["sid", "date"]).reset_index(drop=True), dropped


def security_master(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per company: lifespan and every symbol / ISIN it has carried."""
    panel = panel.sort_values(["sid", "date"])
    g = panel.groupby("sid")
    master = g.agg(
        first_date=("date", "min"),
        last_date=("date", "max"),
        n_days=("date", "size"),
        symbol=("symbol", "last"),
        isin=("isin", "last"),
        last_series=("series", "last"),
    )
    master["symbols"] = g["symbol"].agg(lambda s: "|".join(pd.unique(s)))
    master["isins"] = g["isin"].agg(lambda s: "|".join(x for x in pd.unique(s) if x))
    return master.reset_index()
