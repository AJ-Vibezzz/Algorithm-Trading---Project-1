"""Momentum signals, computed only from data known on the decision date.

Every signal is a function of row ``t`` (the decision date) and earlier rows.
The four building blocks, each from the published literature (references in
docs/02_literature_review.md):

``return``
    Cumulative total return from ``lookback_months`` ago to ``skip_months``
    ago. With 12 and 1 this is the classic "12-1" momentum of Jegadeesh and
    Titman (1993); the skipped month avoids the short-term reversal documented
    by Jegadeesh (1990) and Lehmann (1990).

``risk_adjusted_return``
    The same return divided by the stock's annualised volatility - the
    "momentum ratio" used in the NSE Indices momentum-index methodology.

``residual``
    Momentum in the part of the return not explained by the market (Blitz,
    Huij and Martens, 2011): regress the stock on the market over a long
    window, then sum the residuals over the formation window and divide by
    their standard deviation.

``high_52w``
    Nearness to the highest price in the look-back window (George and Hwang,
    2004), on a split-adjusted price index.

Components are winsorised, turned into cross-sectional z-scores and averaged
with the weights given in the config. Optional screens can then remove stocks
(negative absolute momentum, the most volatile names, or "lumpy" momentum in
the sense of Da, Gurun and Warachka, 2014).

Which blocks to use, with what look-back and skip, is the group's decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

from .config import SignalCfg, SignalComponent
from .market import MarketData

ANNUAL_DAYS = 252


@dataclass
class SignalResult:
    score: np.ndarray  # composite z-score per company; NaN = cannot be scored
    raw: Dict[str, np.ndarray] = field(default_factory=dict)  # raw value of each component
    volatility: Optional[np.ndarray] = None  # annualised, as used by the screens
    dropped: Dict[str, int] = field(default_factory=dict)  # stocks removed, by reason


# --------------------------------------------------------------------------- #
# Shared pieces
# --------------------------------------------------------------------------- #
def _cum_traded(md: MarketData) -> np.ndarray:
    """(T+1, N) running count of traded days, so any window's count is one subtraction."""
    def make():
        out = np.zeros((md.T + 1, md.N), np.int32)
        np.cumsum(md.traded, axis=0, out=out[1:])
        return out
    return md._cache(("cum_traded",), make)


def _traded_fraction(md: MarketData, i0: int, i1: int) -> np.ndarray:
    """Share of the market days in (i0, i1] on which each stock traded."""
    cum = _cum_traded(md)
    return (cum[i1 + 1] - cum[i0 + 1]) / max(1, i1 - i0)


def market_return(md: MarketData) -> np.ndarray:
    """Cap-weighted daily return of all traded stocks, using yesterday's market caps."""
    def make():
        w = np.vstack([np.full((1, md.N), np.nan), md.mcap_ff[:-1]])
        ok = np.isfinite(md.ret) & np.isfinite(w)
        num = np.where(ok, md.ret * w, 0.0).sum(axis=1)
        den = np.where(ok, w, 0.0).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, num / den, np.nan)
    return md._cache(("market_return",), make)


def _window(t: int, comp: SignalComponent, days_per_month: int) -> tuple[int, int]:
    """Rows (i0, i1]: the formation window of one component."""
    return t - comp.lookback_months * days_per_month, t - comp.skip_months * days_per_month


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #
def _past_return(md: MarketData, i0: int, i1: int) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return md.tri_ff[i1] / md.tri_ff[i0] - 1.0


def _residual_momentum(md: MarketData, t: int, i0: int, i1: int, est_days: int, min_frac: float) -> np.ndarray:
    """Sum of market-model residuals over (i0, i1], scaled by their standard deviation."""
    w0 = t - est_days + 1  # first row of the estimation window
    if w0 < 1 or i0 + 1 < w0:
        return np.full(md.N, np.nan)
    y = md.ret[w0:t + 1]
    x = market_return(md)[w0:t + 1]
    ok = np.isfinite(y) & np.isfinite(x)[:, None]
    n = ok.sum(axis=0).astype(float)
    y0 = np.where(ok, y, 0.0)
    xb = np.where(ok, x[:, None], 0.0)
    sx, sy = xb.sum(axis=0), y0.sum(axis=0)
    sxx, sxy = (xb * xb).sum(axis=0), (xb * y0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        beta = (sxy - sx * sy / n) / (sxx - sx * sx / n)
        alpha = (sy - beta * sx) / n
        resid = np.where(ok, y - alpha - beta * x[:, None], np.nan)
        form = resid[i0 + 1 - w0:i1 + 1 - w0]  # residuals inside the formation window
        n_form = np.isfinite(form).sum(axis=0)
        score = np.nansum(form, axis=0) / (np.nanstd(form, axis=0, ddof=1) * np.sqrt(n_form))
    enough = (n >= min_frac * est_days) & (n_form >= min_frac * (i1 - i0))
    return np.where(enough, score, np.nan)


def _high_nearness(md: MarketData, i0: int, i1: int) -> np.ndarray:
    window = md.pri_ff[i0 + 1:i1 + 1]
    with np.errstate(invalid="ignore", divide="ignore"):
        top = np.nanmax(np.where(np.isfinite(window), window, -np.inf), axis=0)
        near = md.pri_ff[i1] / top
    return np.where(np.isfinite(md.pri_ff[i0 + 1]) & (top > 0), near, np.nan)


def information_discreteness(md: MarketData, i0: int, i1: int) -> np.ndarray:
    """Da, Gurun and Warachka (2014): sign(return) x (share of down days - share of up days).

    Low values mean the past return arrived as many small moves in the same
    direction ("continuous information"), which the paper links to stronger
    and more persistent momentum; high values mean a few large jumps.
    """
    r = md.ret[i0 + 1:i1 + 1]
    n = np.isfinite(r).sum(axis=0).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        up = (r > 0).sum(axis=0) / n
        down = (r < 0).sum(axis=0) / n
    return np.sign(_past_return(md, i0, i1)) * (down - up)


def component_raw(md: MarketData, t: int, comp: SignalComponent, cfg: SignalCfg) -> np.ndarray:
    """Raw value of one component for every company on row ``t`` (NaN = not computable)."""
    i0, i1 = _window(t, comp, cfg.days_per_month)
    if i0 < 0 or i1 <= i0:
        return np.full(md.N, np.nan)
    traded_enough = _traded_fraction(md, i0, i1) >= cfg.min_history_fraction
    if comp.kind == "return":
        raw = _past_return(md, i0, i1)
    elif comp.kind == "risk_adjusted_return":
        vol = md.volatility(cfg.volatility_lookback_days)[t] * np.sqrt(ANNUAL_DAYS)
        with np.errstate(invalid="ignore", divide="ignore"):
            raw = _past_return(md, i0, i1) / vol
    elif comp.kind == "residual":
        raw = _residual_momentum(md, t, i0, i1, comp.residual_estimation_months * cfg.days_per_month,
                                 cfg.min_history_fraction)
    elif comp.kind == "high_52w":
        raw = _high_nearness(md, i0, i1)
    else:  # config validation should have caught this
        raise ValueError(f"unknown signal component '{comp.kind}'")
    return np.where(traded_enough & np.isfinite(raw), raw, np.nan)


# --------------------------------------------------------------------------- #
# Composite score
# --------------------------------------------------------------------------- #
def _zscore(x: np.ndarray, reference: np.ndarray, winsor: float) -> np.ndarray:
    """Winsorise and standardise ``x`` using the cross-section in ``reference``.

    The clip levels, mean and standard deviation come from the eligible stocks
    only, but are applied to every stock, so that a stock removed by a filter
    can still be told how it *would* have ranked.
    """
    ref = x[reference & np.isfinite(x)]
    if len(ref) < 5:
        return np.full(x.shape, np.nan)
    lo, hi = np.quantile(ref, [winsor, 1.0 - winsor])
    clipped = np.clip(x, lo, hi)
    core = np.clip(ref, lo, hi)
    sd = core.std()
    if not sd > 0:
        return np.full(x.shape, np.nan)
    return (clipped - core.mean()) / sd


def compute_signal(
    md: MarketData,
    t: int,
    cfg: SignalCfg,
    reference: np.ndarray,
    score_for: Optional[np.ndarray] = None,
) -> SignalResult:
    """Composite momentum score on decision row ``t``.

    ``reference``  the eligible universe: defines the cross-section for z-scores
                   and the population the screens are applied to.
    ``score_for``  stocks to score (default: ``reference``). Pass the wider
                   "in segment" set to learn how filtered-out stocks would rank.
    """
    score_for = reference if score_for is None else score_for
    total_w = sum(c.weight for c in cfg.components)
    score = np.zeros(md.N)
    raw: Dict[str, np.ndarray] = {}
    for k, comp in enumerate(cfg.components):
        x = component_raw(md, t, comp, cfg)
        raw[f"{k}:{comp.kind}_{comp.lookback_months}_{comp.skip_months}"] = x
        score += comp.weight / total_w * _zscore(x, reference, cfg.winsorize_pct)  # NaN propagates
    score[~score_for] = np.nan

    dropped = {"no_history": int((score_for & reference & ~np.isfinite(score)).sum())}
    vol = md.volatility(cfg.volatility_lookback_days)[t] * np.sqrt(ANNUAL_DAYS)
    f = cfg.filters

    if f.require_positive_momentum:
        # absolute-momentum screen on the first return-type component
        for key, comp in zip(raw, cfg.components):
            if comp.kind in ("return", "risk_adjusted_return", "residual"):
                bad = np.isfinite(score) & ~(raw[key] > 0)
                dropped["not_positive"] = int((bad & reference).sum())
                score[bad] = np.nan
                break

    if f.max_volatility_percentile is not None:
        ref_vol = vol[reference & np.isfinite(vol)]
        if len(ref_vol) >= 5:
            cut = np.quantile(ref_vol, f.max_volatility_percentile)
            bad = np.isfinite(score) & ~(vol <= cut)
            dropped["too_volatile"] = int((bad & reference).sum())
            score[bad] = np.nan

    if f.max_information_discreteness is not None and cfg.components:
        i0, i1 = _window(t, cfg.components[0], cfg.days_per_month)
        if i0 >= 0:
            ident = information_discreteness(md, i0, i1)
            bad = np.isfinite(score) & ~(ident <= f.max_information_discreteness)
            dropped["lumpy_momentum"] = int((bad & reference).sum())
            score[bad] = np.nan

    return SignalResult(score=score, raw=raw, volatility=vol, dropped=dropped)


def warmup_days(cfg: SignalCfg) -> int:
    """Market days of history a stock needs before the signal can be computed."""
    need = 0
    for c in cfg.components:
        months = c.residual_estimation_months if c.kind == "residual" else c.lookback_months
        need = max(need, months * cfg.days_per_month)
    return max(need, cfg.volatility_lookback_days)
