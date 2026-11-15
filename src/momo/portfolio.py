"""Portfolio construction: from scores to target weights.

Steps on each decision date
---------------------------
1. **Rank** the eligible, scoreable stocks by momentum score.
2. **Select** with an entry/exit buffer: new names must rank in the top ``N``;
   names already held are kept until they fall below ``exit_rank_multiple x N``.
   The buffer cuts turnover (and so costs) at the boundary of the ranking -
   the "buy/hold spread" idea of Novy-Marx and Velikov (2016, 2019).
3. **Limit sector crowding** while selecting: a candidate is skipped when its
   sector already holds as many names as the sector weight limit allows.
4. **Size** the positions: equal weight, inverse volatility, rank-weighted, or
   minimum variance (Ledoit-Wolf shrinkage covariance).
5. **Cap** each stock and each sector; weight that cannot be placed stays in cash.
6. **Blend tranches**: with a holding period of K rebalances, the target is the
   average of the last K fresh portfolios - the overlapping-portfolio design of
   Jegadeesh and Titman (1993).

All inputs are known on the decision date. What N, the buffer, the weighting
and the caps should be is the group's decision (config/strategy.yaml).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from .config import PortfolioCfg
from .market import MarketData


@dataclass
class Construction:
    """A fresh target portfolio (weights sum to at most 1, before exposure scaling)."""

    weights: np.ndarray  # aligned to MarketData.sids
    names: np.ndarray  # columns of the selected stocks, best rank first
    n_target: int
    n_scoreable: int
    kept: int  # held names retained through the buffer
    added: int  # new names bought
    blocked_by_sector: List[int] = field(default_factory=list)  # top-ranked names skipped: sector full
    unplaced_weight: float = 0.0  # weight left in cash because of the caps


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def select_names(
    score: np.ndarray,
    eligible: np.ndarray,
    held: np.ndarray,
    sector: np.ndarray,
    cfg: PortfolioCfg,
    banned: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, int, int, int, List[int]]:
    """Choose the names to hold. Returns (names, n_target, n_scoreable, kept, blocked_by_sector).

    ``held``    columns currently in the (previous) target portfolio
    ``banned``  bool mask of stocks that may not be bought now (stop-loss cool-down)
    """
    ok = eligible & np.isfinite(score)
    if banned is not None:
        ok &= ~banned
    cols = np.where(ok)[0]
    n_scoreable = len(cols)
    if n_scoreable == 0:
        return np.array([], int), 0, 0, 0, []
    order = cols[np.argsort(-score[cols], kind="stable")]  # best first
    rank = {int(c): k + 1 for k, c in enumerate(order)}

    if cfg.selection == "top_n":
        n_target = min(int(cfg.n_holdings), n_scoreable)
    else:
        n_target = max(1, math.ceil(cfg.top_fraction * n_scoreable))
    exit_rank = math.floor(cfg.exit_rank_multiple * n_target)

    # 1. keep what we hold while it still ranks inside the exit threshold
    keep = sorted((int(c) for c in held if int(c) in rank and rank[int(c)] <= exit_rank), key=rank.get)[:n_target]
    chosen = list(keep)
    in_chosen = set(chosen)

    # 2. fill the remaining slots from the top of the ranking, respecting the sector limit
    max_per_sector = max(1, math.floor(cfg.max_weight_per_sector * n_target + 1e-9))
    count: Dict[str, int] = {}
    for c in chosen:
        count[sector[c]] = count.get(sector[c], 0) + 1
    blocked: List[int] = []
    for c in order:
        if len(chosen) >= n_target:
            break
        c = int(c)
        if c in in_chosen:
            continue
        if count.get(sector[c], 0) >= max_per_sector:
            blocked.append(c)
            continue
        chosen.append(c)
        in_chosen.add(c)
        count[sector[c]] = count.get(sector[c], 0) + 1

    chosen.sort(key=rank.get)
    return np.array(chosen, int), n_target, n_scoreable, len(keep), blocked


# --------------------------------------------------------------------------- #
# Sizing
# --------------------------------------------------------------------------- #
def _min_variance(returns: np.ndarray, cap: float) -> Optional[np.ndarray]:
    """Long-only minimum-variance weights with a per-stock cap (None if it cannot be solved)."""
    from scipy.optimize import minimize
    from sklearn.covariance import LedoitWolf

    n = returns.shape[1]
    if returns.shape[0] < 30 or n < 2:
        return None
    cov = LedoitWolf().fit(np.nan_to_num(returns)).covariance_
    total = min(1.0, n * cap)
    x0 = np.full(n, total / n)
    res = minimize(
        lambda w: float(w @ cov @ w), x0, jac=lambda w: 2.0 * cov @ w, method="SLSQP",
        bounds=[(0.0, cap)] * n, constraints=[{"type": "eq", "fun": lambda w: w.sum() - total}],
        options={"maxiter": 300, "ftol": 1e-12},
    )
    if not res.success or not np.all(np.isfinite(res.x)):
        return None
    w = np.clip(res.x, 0.0, cap)
    return w / w.sum() if w.sum() > 0 else None


def raw_weights(names: np.ndarray, score: np.ndarray, md: MarketData, t: int, cfg: PortfolioCfg) -> np.ndarray:
    """Uncapped weights for the selected names (sum to 1), in the order of ``names``."""
    n = len(names)
    if n == 0:
        return np.array([])
    if cfg.weighting == "equal":
        w = np.ones(n)
    elif cfg.weighting == "inverse_vol":
        vol = md.volatility(cfg.weighting_lookback_days)[t, names]
        fallback = np.nanmedian(vol) if np.isfinite(vol).any() else 1.0
        vol = np.where(np.isfinite(vol) & (vol > 0), vol, fallback)
        w = 1.0 / vol
    elif cfg.weighting == "score":
        # rank weights: the best name gets n units, the worst 1 - no extra parameter to tune
        w = np.arange(n, 0, -1, dtype=float)
    elif cfg.weighting == "min_variance":
        lo = max(0, t - cfg.weighting_lookback_days + 1)
        w = _min_variance(md.ret[lo:t + 1][:, names], cfg.max_weight_per_stock)
        if w is None:  # too little history or the optimiser failed: fall back, do not guess
            vol = md.volatility(cfg.weighting_lookback_days)[t, names]
            vol = np.where(np.isfinite(vol) & (vol > 0), vol, np.nanmedian(vol) if np.isfinite(vol).any() else 1.0)
            w = 1.0 / vol
    else:
        raise ValueError(f"unknown weighting '{cfg.weighting}'")
    return w / w.sum()


def apply_caps(
    w: np.ndarray, groups: np.ndarray, stock_cap: float, group_cap: float, max_iter: int = 500
) -> Tuple[np.ndarray, float]:
    """Enforce a per-stock cap and a per-group (sector) cap by "water-filling".

    Weight above a cap is handed, pro rata, to names that still have room. A
    stock or sector that has hit its cap stops receiving. Whatever cannot be
    placed is returned as ``unplaced`` and stays in cash.

    Returns ``(capped_weights, unplaced)`` with ``capped.sum() + unplaced == w.sum()``.
    """
    w = np.asarray(w, float).copy()
    total = w.sum()
    if total <= 0 or len(w) == 0:
        return w, 0.0
    eps = 1e-12
    stock_full = np.zeros(len(w), bool)
    group_full: Dict[object, bool] = {}
    labels = np.asarray(groups, dtype=object)
    unique = list(dict.fromkeys(labels.tolist()))
    members = {g: np.where(labels == g)[0] for g in unique}
    unplaced = 0.0

    def give(excess: float) -> float:
        """Hand ``excess`` to names with room; return what could not be placed."""
        room = ~stock_full & np.array([not group_full.get(g, False) for g in labels])
        if not room.any() or w[room].sum() <= eps:
            if room.any():  # receivers exist but hold nothing yet: share equally
                w[room] += excess / room.sum()
                return 0.0
            return excess
        w[room] += excess * w[room] / w[room].sum()
        return 0.0

    for _ in range(max_iter):
        changed = False
        over = w > stock_cap + eps
        if over.any():
            excess = float((w[over] - stock_cap).sum())
            w[over] = stock_cap
            stock_full |= over
            unplaced += give(excess)
            changed = True
        for g in unique:
            idx = members[g]
            gw = w[idx].sum()
            if gw > group_cap + eps:
                w[idx] *= group_cap / gw
                group_full[g] = True
                unplaced += give(float(gw - group_cap))
                changed = True
        if not changed:
            break
    return w, unplaced


# --------------------------------------------------------------------------- #
# One fresh portfolio
# --------------------------------------------------------------------------- #
def construct(
    md: MarketData,
    t: int,
    score: np.ndarray,
    eligible: np.ndarray,
    held: np.ndarray,
    cfg: PortfolioCfg,
    banned: Optional[np.ndarray] = None,
) -> Construction:
    """Build the fresh target portfolio for decision row ``t``."""
    names, n_target, n_scoreable, kept, blocked = select_names(score, eligible, held, md.sector, cfg, banned)
    weights = np.zeros(md.N)
    unplaced = 0.0
    if len(names):
        w = raw_weights(names, score, md, t, cfg)
        w, unplaced = apply_caps(w, md.sector[names], cfg.max_weight_per_stock, cfg.max_weight_per_sector)
        weights[names] = w
    return Construction(
        weights=weights, names=names, n_target=n_target, n_scoreable=n_scoreable,
        kept=kept, added=len(names) - kept, blocked_by_sector=blocked, unplaced_weight=float(unplaced),
    )


# --------------------------------------------------------------------------- #
# Overlapping tranches (holding period of K rebalances)
# --------------------------------------------------------------------------- #
def tranche_count(cfg: PortfolioCfg) -> int:
    """How many overlapping portfolios a holding period of ``holding_months`` implies."""
    per_month = {"monthly": 1.0, "quarterly": 1.0 / 3.0, "weekly": 52.0 / 12.0}[cfg.rebalance]
    return max(1, int(round(cfg.holding_months * per_month)))


class TrancheBook:
    """Keeps the last K fresh portfolios; the live target is their average."""

    def __init__(self, k: int, stock_cap: float, sector_cap: float, sector: np.ndarray):
        self.k = k
        self.stock_cap, self.sector_cap, self.sector = stock_cap, sector_cap, sector
        self.book: Deque[np.ndarray] = deque(maxlen=k)

    def add(self, fresh: np.ndarray) -> None:
        self.book.append(fresh.copy())

    def drop_names(self, gone: np.ndarray) -> None:
        """Remove stocks that are no longer investable from the older tranches.

        Their weight is re-spread over the tranche's remaining names (subject to
        the same caps), so an old tranche does not silently drift into cash.
        """
        for i, w in enumerate(self.book):
            if not (w[gone] > 0).any():
                continue
            before = w.sum()
            w = w.copy()
            w[gone] = 0.0
            live = np.where(w > 0)[0]
            if len(live) and w.sum() > 0:
                scaled, _ = apply_caps(w[live] / w[live].sum() * before, self.sector[live],
                                       self.stock_cap, self.sector_cap)
                w[live] = scaled
            self.book[i] = w

    def blended(self) -> np.ndarray:
        return np.mean(np.vstack(self.book), axis=0)

    @property
    def last_names(self) -> np.ndarray:
        """Columns held in the most recent fresh portfolio (input to the exit buffer)."""
        return np.where(self.book[-1] > 0)[0] if self.book else np.array([], int)
