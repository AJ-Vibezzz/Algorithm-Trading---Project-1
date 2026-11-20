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

import warnings
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd

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


BROAD_INDICES = ("NIFTY 500", "NIFTY 200", "NIFTY 100", "NIFTY 50")  # fallbacks for the market return
MARKET_CAP_COVERAGE = 0.8  # share of the day's traded stocks that must have a market cap


def market_return(md: MarketData) -> np.ndarray:
    """Daily return of the whole market, from data known the day before.

    The cap-weighted return of all traded stocks (yesterday's market caps) is
    used whenever at least ``MARKET_CAP_COVERAGE`` of the day's stocks have a
    market cap. The exchange publishes market caps only from 2020 (see
    ``data/size.py``), so for earlier days the return of the broadest exchange
    index in the data is used instead, and where there is no index either (the
    index file starts in 2012) the traded-value-weighted return of all stocks.
    ``market_return_sources`` reports which was used when.
    """
    return _market_return(md)[0]


def market_return_sources(md: MarketData) -> pd.DataFrame:
    """Which market proxy was used over which dates: one row per unbroken run of days."""
    labels = np.array(["cap_weighted", "exchange_index", "traded_value_weighted", "none"], dtype=object)
    code = _market_return(md)[1]
    run = np.concatenate([[0], np.cumsum(code[1:] != code[:-1])])
    out = pd.DataFrame({"date": md.dates, "source": labels[code], "run": run}).groupby("run").agg(
        source=("source", "first"), first_day=("date", "first"), last_day=("date", "last"), days=("date", "size"))
    out.attrs["index_used"] = _market_return(md)[2]
    return out.reset_index(drop=True)


def _market_return(md: MarketData):
    def make():
        has_ret = np.isfinite(md.ret)

        def weighted(w_today: np.ndarray):
            w = np.vstack([np.full((1, md.N), np.nan), w_today[:-1]])  # yesterday's weights
            ok = has_ret & np.isfinite(w) & (w > 0)
            num = np.where(ok, md.ret * w, 0.0).sum(axis=1)
            den = np.where(ok, w, 0.0).sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                coverage = ok.sum(axis=1) / np.maximum(1, has_ret.sum(axis=1))
                return np.where(den > 0, num / den, np.nan), coverage

        cap, coverage = weighted(md.mcap_ff)
        use_cap = np.isfinite(cap) & (coverage >= MARKET_CAP_COVERAGE)
        index_name = next((n for n in BROAD_INDICES if n in md.indices.columns), None)
        index = md.index_return(index_name) if index_name else np.full(md.T, np.nan)
        value, _ = weighted(md.median_value(126))
        out = np.where(use_cap, cap, np.where(np.isfinite(index), index, value))
        code = np.where(use_cap, 0, np.where(np.isfinite(index), 1, np.where(np.isfinite(value), 2, 3)))
        return out, code, index_name
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
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # stocks with no data in the window give NaN, by design
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
        # Absolute (time-series) momentum, as in Moskowitz, Ooi & Pedersen (2012): the stock's OWN total
        # return over the first momentum component's window must be positive. For a residual component this
        # is deliberately not the residual score - a stock can beat the market while falling.
        for comp in cfg.components:
            if comp.kind in ("return", "risk_adjusted_return", "residual"):
                i0, i1 = _window(t, comp, cfg.days_per_month)
                own = _past_return(md, i0, i1) if (i0 >= 0 and i1 > i0) else np.full(md.N, np.nan)
                bad = np.isfinite(score) & ~(own > 0)
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
