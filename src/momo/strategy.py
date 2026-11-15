"""The decision made on each rebalance date: universe -> signal -> portfolio -> exposure.

``MomentumStrategy.decide(t, ...)`` is the only place where a trading decision
is taken, and it reads nothing dated after row ``t``. The back-test engine
calls it at the close of each decision day and trades the result on the
following day(s).

It also records, for the report, every "signal" that a limit removed: stocks
that ranked high enough to be bought but were excluded by a universe filter,
by the sector limit, or by a stop-loss cool-down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .config import StrategyConfig
from .market import MarketData
from .portfolio import TrancheBook, construct, tranche_count
from .risk import exposure_overlays
from .signals import compute_signal, market_return, warmup_days
from .universe import Universe
from .utils import month_end_flags, quarter_end_flags, week_end_flags


@dataclass
class Decision:
    row: int
    weights: np.ndarray  # final target weights (fraction of NAV per stock)
    base_weights: np.ndarray  # the same before the drawdown multiplier (used for intra-month de-risking)
    info: Dict[str, object] = field(default_factory=dict)
    screened: List[Dict[str, object]] = field(default_factory=list)


def decision_rows(md: MarketData, cfg: StrategyConfig, start, end) -> List[int]:
    """Rows on which a rebalance decision is taken: period-end trading days in [start, end)."""
    flags = {"monthly": month_end_flags, "quarterly": quarter_end_flags, "weekly": week_end_flags}[
        cfg.portfolio.rebalance](md.dates)
    lo = md.first_on_or_after(start)
    hi = md.loc(end)
    # the last row cannot be a decision day: there is no "t+1" to trade on
    return [int(t) for t in np.where(flags)[0] if lo <= t < hi]


class MomentumStrategy:
    """Stateful only through its tranche book (the last K fresh portfolios)."""

    def __init__(self, md: MarketData, cfg: StrategyConfig, universe: Universe):
        self.md, self.cfg, self.universe = md, cfg, universe
        p = cfg.portfolio
        self.tranches = TrancheBook(tranche_count(p), p.max_weight_per_stock, p.max_weight_per_sector, md.sector)
        self.base_exposure = p.gross_exposure * (1.0 - p.cash_buffer)
        # Benchmark for betas: a named index, else the cap-weighted market built from the panel.
        name = cfg.risk.beta.benchmark
        self.benchmark_ret = md.index_return(name) if name else market_return(md)
        r = cfg.risk.regime_filter
        self.regime_level = md.index_level(r.index) if (r.enabled and r.index) else None
        self.warmup = warmup_days(cfg.signal)

    def decide(self, t: int, drawdown_multiplier: float = 1.0, banned: Optional[np.ndarray] = None) -> Decision:
        md, cfg = self.md, self.cfg
        snap = self.universe[t]
        banned = np.zeros(md.N, bool) if banned is None else banned

        sig = compute_signal(md, t, cfg.signal, reference=snap.eligible, score_for=snap.in_segment)

        # Older tranches shed names that are no longer investable or are in cool-down.
        self.tranches.drop_names(~snap.eligible | banned)
        held = self.tranches.last_names
        fresh = construct(md, t, sig.score, snap.eligible, held, cfg.portfolio, banned)
        self.tranches.add(fresh.weights)
        blended = self.tranches.blended()

        exp = exposure_overlays(md, t, blended, cfg.risk, self.benchmark_ret, self.regime_level,
                                drawdown_multiplier, self.base_exposure)
        mult = dict(exp.multipliers)
        hedge = cfg.risk.hedge
        if hedge.enabled:  # cash must cover futures margin, so less can go into stocks
            beta = max(exp.measures.get("beta_full", 1.0), 0.0)
            mult["hedge_margin"] = 1.0 / (1.0 + hedge.margin_rate * hedge.ratio * beta)
        without_dd = {k: v for k, v in mult.items() if k != "drawdown"}
        base_mult = min(without_dd.values()) if without_dd else 1.0
        final_mult = min(mult.values())
        weights = blended * self.base_exposure * final_mult
        base_weights = blended * self.base_exposure * base_mult

        screened = self._screen_log(t, snap, sig.score, fresh, banned)
        info: Dict[str, object] = {
            "date": md.dates[t],
            "in_segment": int(snap.in_segment.sum()),
            "eligible": int(snap.eligible.sum()),
            "scoreable": fresh.n_scoreable,
            "n_target": fresh.n_target,
            "n_fresh": int(len(fresh.names)),
            "kept": fresh.kept,
            "added": fresh.added,
            "n_blended": int((blended > 0).sum()),
            "blocked_by_filters": sum(1 for s in screened if s["reason"] not in ("sector_cap", "cooldown")),
            "blocked_by_sector_cap": len(fresh.blocked_by_sector),
            "blocked_by_cooldown": sum(1 for s in screened if s["reason"] == "cooldown"),
            "unplaced_weight": fresh.unplaced_weight,
            "exposure": float(weights.sum()),
            "binding_overlay": min(mult, key=mult.get) if final_mult < 1.0 - 1e-12 else "none",
            **{f"mult_{k}": float(v) for k, v in mult.items()},
            **{k: float(v) for k, v in exp.measures.items()},
            **{f"dropped_{k}": v for k, v in sig.dropped.items()},
        }
        return Decision(row=t, weights=weights, base_weights=base_weights, info=info, screened=screened)

    def _screen_log(self, t, snap, score, fresh, banned) -> List[Dict[str, object]]:
        """Which top-ranked stocks were kept out, and by what.

        "Would-be signals" are the best ``n_target`` stocks by score among *all*
        stocks in the chosen size segments, before any tradability filter.
        """
        md = self.md
        out: List[Dict[str, object]] = []
        pool = np.where(snap.in_segment & np.isfinite(score))[0]
        if len(pool) == 0 or fresh.n_target == 0:
            return out
        order = pool[np.argsort(-score[pool], kind="stable")]
        for rank, c in enumerate(order[:fresh.n_target], start=1):
            if not snap.eligible[c]:
                reason = snap.reason[c]
            elif banned[c]:
                reason = "cooldown"
            else:
                continue
            out.append({"date": md.dates[t], "sid": int(md.sids[c]), "symbol": md.symbols[c],
                        "reason": reason, "score_rank": rank})
        for c in fresh.blocked_by_sector:
            out.append({"date": md.dates[t], "sid": int(md.sids[c]), "symbol": md.symbols[c],
                        "reason": "sector_cap", "score_rank": np.nan})
        return out
