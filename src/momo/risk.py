"""Risk measurement and the rules that act on it.

This module turns the written risk policy into code. It provides

* **VaR and Expected Shortfall** of a portfolio (historical simulation,
  Gaussian, or Cornish-Fisher) and the statistical back-tests of a VaR model
  (Kupiec 1995; Christoffersen 1998);
* **exposure overlays** applied when a portfolio is formed: a VaR limit, an ES
  limit, volatility targeting (Barroso and Santa-Clara 2015; Moreira and Muir
  2017), a market-regime switch and a beta cap. Each returns a multiplier in
  [0, 1]; the tightest one sets the invested fraction;
* a **drawdown controller** with a soft and a hard limit, a cooling-off period
  and re-basing of the high-water mark;
* **concentration and liquidity** measures and a **limit monitor** that lists
  every day a limit was breached.

The numbers in the limits (confidence levels, thresholds, what happens on a
breach, who decides) are policy - the group sets them in config/strategy.yaml
and writes them up in docs/05_risk_policy_template.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from .config import DrawdownCfg, RiskCfg
from .market import MarketData

ANNUAL_DAYS = 252


# --------------------------------------------------------------------------- #
# VaR / Expected Shortfall
# --------------------------------------------------------------------------- #
def var_es(returns: np.ndarray, confidence: float, method: str = "historical", horizon_days: int = 1) -> Tuple[float, float]:
    """Value-at-Risk and Expected Shortfall as positive fractions of portfolio value.

    ``returns`` are daily portfolio returns. VaR at confidence c is the loss
    exceeded on a fraction (1 - c) of days; ES is the average loss on those
    days (the coherent measure of Artzner et al. 1999; Acerbi and Tasche 2002).
    Multi-day horizons use the square-root-of-time rule.
    """
    r = np.asarray(returns, float)
    r = r[np.isfinite(r)]
    if len(r) < 30:
        return np.nan, np.nan
    tail = 1.0 - confidence
    mu, sd = r.mean(), r.std(ddof=1)
    if method == "historical":
        q = np.quantile(r, tail)
        var = -q
        es = -r[r <= q].mean()
    elif method == "parametric":
        z = stats.norm.ppf(tail)
        var = -(mu + z * sd)
        es = -(mu - sd * stats.norm.pdf(z) / tail)
    elif method == "cornish_fisher":
        # Gaussian quantile corrected for skewness and excess kurtosis (Favre and Galeano 2002).
        z = stats.norm.ppf(tail)
        s, k = stats.skew(r), stats.kurtosis(r)
        z_cf = z + (z ** 2 - 1) * s / 6 + (z ** 3 - 3 * z) * k / 24 - (2 * z ** 3 - 5 * z) * s ** 2 / 36
        var = -(mu + z_cf * sd)
        beyond = r[r <= -var]
        normal_ratio = (stats.norm.pdf(z) / tail) / -z
        es = -beyond.mean() if len(beyond) else var * normal_ratio
    else:
        raise ValueError(f"unknown VaR method '{method}'")
    scale = np.sqrt(horizon_days)
    return float(max(var, 0.0) * scale), float(max(es, 0.0) * scale)


def fixed_weight_returns(md: MarketData, t: int, weights: np.ndarray, lookback: int) -> np.ndarray:
    """Daily returns the *current* weights would have earned over the last ``lookback`` days.

    This is historical simulation: hold today's portfolio fixed and replay
    history. A stock that did not trade on a day contributes zero that day
    (so risk is understated for names with thin history - see limitations).
    """
    lo = max(1, t - lookback + 1)
    cols = np.where(weights != 0)[0]
    if len(cols) == 0:
        return np.zeros(t - lo + 1)
    r = np.nan_to_num(md.ret[lo:t + 1][:, cols])
    return r @ weights[cols]


def betas(md: MarketData, t: int, benchmark_ret: np.ndarray, lookback: int, cols: np.ndarray) -> np.ndarray:
    """Trailing market-model beta of each stock in ``cols`` against ``benchmark_ret``."""
    lo = max(1, t - lookback + 1)
    y = md.ret[lo:t + 1][:, cols]
    x = benchmark_ret[lo:t + 1]
    ok = np.isfinite(y) & np.isfinite(x)[:, None]
    n = ok.sum(axis=0).astype(float)
    y0, xb = np.where(ok, y, 0.0), np.where(ok, x[:, None], 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = (xb * y0).sum(0) - xb.sum(0) * y0.sum(0) / n
        var = (xb * xb).sum(0) - xb.sum(0) ** 2 / n
        beta = cov / var
    return np.where((n >= 60) & np.isfinite(beta), beta, 1.0)  # too little history: assume market beta


@dataclass
class ExposureDecision:
    """Why the invested fraction is what it is on a decision date."""

    exposure: float
    multipliers: Dict[str, float] = field(default_factory=dict)
    measures: Dict[str, float] = field(default_factory=dict)

    @property
    def binding(self) -> str:
        return min(self.multipliers, key=self.multipliers.get) if self.multipliers else ""


def exposure_overlays(
    md: MarketData,
    t: int,
    weights: np.ndarray,
    cfg: RiskCfg,
    benchmark_ret: Optional[np.ndarray],
    regime_level: Optional[np.ndarray],
    drawdown_multiplier: float,
    base_exposure: float,
) -> ExposureDecision:
    """Invested fraction for a fresh portfolio with unit weights ``weights``.

    Every overlay yields a multiplier in [0, 1]; the smallest one binds. Risk
    of a long-only portfolio scales with the invested fraction (cash carries no
    market risk), so "VaR at full investment x multiplier <= limit" gives the
    multiplier directly.
    """
    mult: Dict[str, float] = {}
    meas: Dict[str, float] = {}
    total = weights.sum()
    unit = weights / total if total > 0 else weights
    hist = fixed_weight_returns(md, t, unit, max(cfg.var.lookback_days, cfg.vol_target.lookback_days))

    var, _ = var_es(hist[-cfg.var.lookback_days:], cfg.var.confidence, cfg.var.method, cfg.var.horizon_days)
    _, es = var_es(hist[-cfg.var.lookback_days:], cfg.es.confidence, cfg.var.method, cfg.var.horizon_days)
    meas["var_full"], meas["es_full"] = var, es
    if cfg.var.action == "scale":
        if np.isfinite(var) and var > 0:
            mult["var_limit"] = min(1.0, cfg.var.limit_pct_nav / (var * base_exposure))
        if np.isfinite(es) and es > 0:
            mult["es_limit"] = min(1.0, cfg.es.limit_pct_nav / (es * base_exposure))

    vol = float(np.std(hist[-cfg.vol_target.lookback_days:], ddof=1) * np.sqrt(ANNUAL_DAYS)) if len(hist) > 20 else np.nan
    meas["vol_full"] = vol
    if cfg.vol_target.enabled and np.isfinite(vol) and vol > 0:
        raw = cfg.vol_target.annual_vol / (vol * base_exposure)
        mult["vol_target"] = float(np.clip(raw, cfg.vol_target.min_exposure, min(1.0, cfg.vol_target.max_exposure)))

    if benchmark_ret is not None:
        cols = np.where(unit > 0)[0]
        b = betas(md, t, benchmark_ret, cfg.beta.lookback_days, cols)
        beta_p = float(b @ unit[cols]) if len(cols) else 0.0
        meas["beta_full"] = beta_p
        if cfg.beta.max_portfolio_beta is not None and beta_p > 0:
            mult["beta_cap"] = min(1.0, cfg.beta.max_portfolio_beta / (beta_p * base_exposure))

    if cfg.regime_filter.enabled and regime_level is not None:
        lo = max(0, t - cfg.regime_filter.ma_days + 1)
        window = regime_level[lo:t + 1]
        window = window[np.isfinite(window)]
        if len(window) >= cfg.regime_filter.ma_days // 2 and np.isfinite(regime_level[t]):
            above = regime_level[t] >= window.mean()
            meas["regime_above_ma"] = float(above)
            mult["regime"] = 1.0 if above else cfg.regime_filter.exposure_when_below

    mult["drawdown"] = drawdown_multiplier
    exposure = base_exposure * min(mult.values()) if mult else base_exposure
    return ExposureDecision(exposure=float(exposure), multipliers=mult, measures=meas)


# --------------------------------------------------------------------------- #
# Drawdown
# --------------------------------------------------------------------------- #
class DrawdownController:
    """Tiered de-risking on drawdown, with cooling-off and re-basing.

    * drawdown beyond ``soft_limit``  -> exposure multiplier ``soft_exposure``, lifted
      once the drawdown is back inside ``soft_release_level`` (default: the soft limit)
    * drawdown beyond ``hard_limit``  -> multiplier ``hard_exposure`` for
      ``cooloff_days`` trading days; then the high-water mark is reset to the
      current value (otherwise a portfolio sitting in cash could never
      "recover") and normal operation resumes at the next rebalance.

    A step *down* in exposure is acted on the next trading day; a step back
    *up* waits for the next scheduled rebalance, to avoid whipsaw.
    """

    def __init__(self, cfg: DrawdownCfg, start_nav: float):
        self.cfg = cfg
        self.hwm = start_nav
        self.mode = "normal"
        self.days_in_hard = 0
        # Drawdown at which the soft tier is lifted; equal to the soft limit unless set tighter.
        self.release_level = cfg.soft_limit if cfg.soft_release_level is None else cfg.soft_release_level

    @property
    def multiplier(self) -> float:
        return {"normal": 1.0, "soft": self.cfg.soft_exposure, "hard": self.cfg.hard_exposure}[self.mode]

    def update(self, nav: float) -> Optional[str]:
        """Call once per day at the close. Returns the event that occurred, if any."""
        self.hwm = max(self.hwm, nav)
        dd = nav / self.hwm - 1.0
        if self.mode == "hard":
            self.days_in_hard += 1
            if self.days_in_hard >= self.cfg.cooloff_days:
                self.hwm = nav
                self.mode = "normal"
                return "rebase_after_cooloff"
            return None
        if dd <= -self.cfg.hard_limit:
            self.mode, self.days_in_hard = "hard", 0
            return "hard_limit_breached"
        if dd <= -self.cfg.soft_limit:
            if self.mode != "soft":
                self.mode = "soft"
                return "soft_limit_breached"
            return None
        if self.mode == "soft" and dd > -self.release_level:
            self.mode = "normal"
            return "soft_limit_released"
        return None


def drawdown_series(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1.0


def max_drawdown_stats(nav: pd.Series) -> Dict[str, object]:
    """Depth and timing of the worst peak-to-trough fall."""
    dd = drawdown_series(nav)
    if dd.empty or dd.min() == 0:
        return {"max_drawdown": 0.0, "peak": None, "trough": None, "recovery": None,
                "days_peak_to_trough": 0, "days_to_recover": 0, "longest_underwater_days": 0}
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    after = nav.loc[trough:]
    recovered = after[after >= nav.loc[peak]]
    recovery = recovered.index[0] if len(recovered) else None
    under = (dd < 0).astype(int)
    spell = under.groupby((under == 0).cumsum()).cumsum()
    return {
        "max_drawdown": float(dd.min()),
        "peak": peak, "trough": trough, "recovery": recovery,
        "days_peak_to_trough": int((trough - peak).days),
        "days_to_recover": int((recovery - trough).days) if recovery is not None else None,
        "longest_underwater_days": int(spell.max()),  # trading days
    }


# --------------------------------------------------------------------------- #
# VaR model back-tests
# --------------------------------------------------------------------------- #
def kupiec_pof(n_obs: int, n_breaches: int, confidence: float) -> Dict[str, float]:
    """Kupiec (1995) proportion-of-failures test: are there too many / too few VaR breaches?"""
    p = 1.0 - confidence
    if n_obs == 0:
        return {"expected": 0.0, "observed": 0, "lr": np.nan, "p_value": np.nan}
    x, n = n_breaches, n_obs
    phat = x / n
    def loglik(q: float) -> float:
        q = min(max(q, 1e-12), 1 - 1e-12)
        return (n - x) * np.log(1 - q) + x * np.log(q)
    lr = -2.0 * (loglik(p) - loglik(phat))
    return {"expected": p * n, "observed": int(x), "lr": float(lr), "p_value": float(1 - stats.chi2.cdf(lr, 1))}


def christoffersen_independence(breaches: np.ndarray) -> Dict[str, float]:
    """Christoffersen (1998) test: do VaR breaches cluster in time?"""
    b = np.asarray(breaches, int)
    if len(b) < 2:
        return {"lr": np.nan, "p_value": np.nan}
    prev, cur = b[:-1], b[1:]
    n00 = int(((prev == 0) & (cur == 0)).sum()); n01 = int(((prev == 0) & (cur == 1)).sum())
    n10 = int(((prev == 1) & (cur == 0)).sum()); n11 = int(((prev == 1) & (cur == 1)).sum())
    def ll(p: float, zeros: int, ones: int) -> float:
        p = min(max(p, 1e-12), 1 - 1e-12)
        return zeros * np.log(1 - p) + ones * np.log(p)
    pi01 = n01 / max(1, n00 + n01)
    pi11 = n11 / max(1, n10 + n11)
    pi = (n01 + n11) / max(1, n00 + n01 + n10 + n11)
    lr = -2.0 * (ll(pi, n00 + n10, n01 + n11) - ll(pi01, n00, n01) - ll(pi11, n10, n11))
    return {"lr": float(lr), "p_value": float(1 - stats.chi2.cdf(max(lr, 0.0), 1)), "n11": n11}


# --------------------------------------------------------------------------- #
# Concentration and liquidity
# --------------------------------------------------------------------------- #
def concentration(weights: np.ndarray) -> Dict[str, float]:
    w = np.sort(weights[weights > 0])[::-1]
    if len(w) == 0:
        return {"n": 0, "max_weight": 0.0, "top5": 0.0, "top10": 0.0, "hhi": 0.0, "effective_n": 0.0}
    share = w / w.sum()
    hhi = float((share ** 2).sum())
    return {"n": int(len(w)), "max_weight": float(w[0]), "top5": float(w[:5].sum()),
            "top10": float(w[:10].sum()), "hhi": hhi, "effective_n": 1.0 / hhi}


def days_to_liquidate(position_value: np.ndarray, adv: np.ndarray, participation: float) -> np.ndarray:
    """Trading days needed to sell each position at ``participation`` x ADV per day."""
    with np.errstate(divide="ignore", invalid="ignore"):
        days = position_value / (participation * adv)
    return np.where(np.isfinite(days), days, np.inf)


def liquidity_profile(position_value: np.ndarray, adv: np.ndarray, participation: float,
                      horizons=(1, 3, 5, 10, 20)) -> Dict[str, float]:
    """Share of the equity book that can be sold within each horizon."""
    total = position_value.sum()
    if total <= 0:
        return {f"sellable_{h}d": 1.0 for h in horizons}
    daily_cap = participation * np.nan_to_num(adv)
    return {f"sellable_{h}d": float(np.minimum(position_value, h * daily_cap).sum() / total) for h in horizons}
