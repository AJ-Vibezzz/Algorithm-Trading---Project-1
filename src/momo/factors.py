"""Factor returns and factor-exposure regressions (market, size, value, momentum).

Where the factors come from
---------------------------
1. **Supplied** (preferred): ``data/external/factors/*.csv`` - for example the
   IIM Ahmedabad Indian Fama-French-momentum data library (Agarwalla, Jacob and
   Varma 2013), which provides market, SMB, HML and WML for India.
2. **Built here from the panel** when nothing is supplied - transparent proxies:

   ``mkt_rf``  cap-weighted return of every traded stock, minus the risk-free rate
   ``smb``     small (market-cap ranks 251-750) minus big (ranks 1-100)
   ``wml``     within the 500 largest stocks: top 30% minus bottom 30% by 12-1 month return
   ``hml``     cannot be built without book values. If the exchange's value-index
               series is in the data, "value index minus broad index" is used as
               a labelled proxy; otherwise the factor is omitted.

   Portfolios are formed at each month-end from information known that day,
   cap-weighted, and held to the next month-end.

Regression
----------
Excess strategy return on the factors, by OLS with Newey-West (1987) standard
errors, monthly by default (monthly data are less distorted by thin trading in
small caps than daily data are). ``alpha`` is the average return the factors do
not explain.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from .frictions import rolling_mean
from .market import MarketData
from .signals import market_return
from .utils import month_end_flags

ANNUAL_DAYS = 252
VALUE_PROXY = ("NIFTY500 VALUE 50", "NIFTY 500")  # value index minus broad index, if both exist


def _buy_and_hold(md: MarketData, t0: int, t1: int, cols: np.ndarray, w0: np.ndarray) -> np.ndarray:
    """Daily returns over rows (t0, t1] of a portfolio bought at ``t0`` with weights ``w0``."""
    if len(cols) == 0 or w0.sum() <= 0:
        return np.full(t1 - t0, np.nan)
    growth = np.cumprod(1.0 + np.nan_to_num(md.ret[t0 + 1:t1 + 1][:, cols]), axis=0)
    value = growth @ (w0 / w0.sum())
    prev = np.concatenate([[1.0], value[:-1]])
    return value / prev - 1.0


def build_factor_proxies(md: MarketData, days_per_month: int = 21) -> pd.DataFrame:
    """Daily factor returns built from the panel (see module docstring)."""
    T = md.T
    size = rolling_mean(md.mcap_ff, 126, min_periods=20)
    recent = md.days_since_trade <= 5
    liquid = md.traded_fraction(126) >= 0.8
    smb = np.full(T, np.nan)
    wml = np.full(T, np.nan)
    forms = [int(t) for t in np.where(month_end_flags(md.dates))[0]]
    look, skip = 12 * days_per_month, days_per_month
    for k, t in enumerate(forms[:-1]):
        t_next = forms[k + 1]
        s = np.where(recent[t] & liquid[t], size[t], np.nan)
        order = np.argsort(-np.nan_to_num(s, nan=-np.inf), kind="stable")
        n = int(np.isfinite(s).sum())
        rank = np.full(md.N, np.nan)
        rank[order[:n]] = np.arange(1, n + 1)
        w = np.nan_to_num(md.mcap_ff[t])

        big, small = np.where(rank <= 100)[0], np.where((rank > 250) & (rank <= 750))[0]
        if len(big) >= 10 and len(small) >= 10:
            smb[t + 1:t_next + 1] = (_buy_and_hold(md, t, t_next, small, w[small])
                                     - _buy_and_hold(md, t, t_next, big, w[big]))
        if t - look >= 0:
            with np.errstate(invalid="ignore", divide="ignore"):
                mom = md.tri_ff[t - skip] / md.tri_ff[t - look] - 1.0
            pool = np.where((rank <= 500) & np.isfinite(mom))[0]
            if len(pool) >= 30:
                lo, hi = np.quantile(mom[pool], [0.3, 0.7])
                winners, losers = pool[mom[pool] >= hi], pool[mom[pool] <= lo]
                wml[t + 1:t_next + 1] = (_buy_and_hold(md, t, t_next, winners, w[winners])
                                         - _buy_and_hold(md, t, t_next, losers, w[losers]))
    out = pd.DataFrame({"mkt_rf": market_return(md) - md.rf_daily, "smb": smb, "wml": wml, "rf": md.rf_daily},
                       index=md.dates)
    if all(name in md.indices.columns for name in VALUE_PROXY):
        out["hml"] = md.index_return(VALUE_PROXY[0]) - md.index_return(VALUE_PROXY[1])
        out.attrs["hml_source"] = "proxy: NIFTY500 VALUE 50 minus NIFTY 500"
    out.attrs["source"] = "built from the panel (proxies)"
    return out


def get_factors(md: MarketData, supplied: Optional[Dict[str, pd.DataFrame]] = None) -> pd.DataFrame:
    """Daily factor returns: the supplied library if present, else the proxies built here."""
    if supplied and "daily" in supplied and len(supplied["daily"]):
        f = supplied["daily"].reindex(md.dates)
        if "rf" not in f.columns:
            f["rf"] = md.rf_daily
        f.attrs["source"] = "supplied (data/external/factors)"
        return f
    return build_factor_proxies(md)


def _to_monthly(x: pd.DataFrame) -> pd.DataFrame:
    return (1.0 + x).groupby(x.index.to_period("M")).prod() - 1.0


def factor_regression(returns: pd.Series, factors: pd.DataFrame, freq: str = "M") -> Dict[str, object]:
    """Regress excess strategy returns on the factor returns.

    ``freq`` is "M" (monthly, default) or "D" (daily). Returns the coefficient
    table (with Newey-West t-statistics), annualised alpha and R-squared.
    """
    import statsmodels.api as sm

    names = [c for c in ("mkt_rf", "smb", "hml", "wml") if c in factors.columns]
    f = factors.reindex(returns.index)
    data = pd.concat([returns.rename("strategy"), f[names + (["rf"] if "rf" in f.columns else [])]], axis=1)
    if "rf" not in data.columns:
        data["rf"] = 0.0
    data = data.dropna(subset=["strategy"] + names)
    if freq == "M":
        counts = data["strategy"].groupby(data.index.to_period("M")).size()
        data = _to_monthly(data)
        data = data[counts >= 10]  # drop stub months
        periods = 12
    else:
        periods = ANNUAL_DAYS
    if len(data) < len(names) + 12:
        return {"table": pd.DataFrame(), "alpha_annual": np.nan, "r_squared": np.nan, "n": len(data),
                "note": "too few observations for a factor regression"}
    y = data["strategy"] - data["rf"]
    X = sm.add_constant(data[names])
    lags = int(np.floor(4 * (len(y) / 100.0) ** (2.0 / 9.0)))
    fit = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    table = pd.DataFrame({"coefficient": fit.params, "t_stat": fit.tvalues, "p_value": fit.pvalues})
    table = table.rename(index={"const": "alpha (per period)"})
    mean_f = data[names].mean()
    contrib = {n_: float(fit.params[n_] * mean_f[n_] * periods) for n_ in names}
    return {
        "table": table, "alpha_annual": float(fit.params["const"] * periods),
        "alpha_t_stat": float(fit.tvalues["const"]), "r_squared": float(fit.rsquared), "n": int(len(y)),
        "frequency": "monthly" if freq == "M" else "daily", "newey_west_lags": lags,
        "annual_contribution": {**contrib, "alpha": float(fit.params["const"] * periods),
                                "total_excess_return": float(y.mean() * periods)},
        "factor_source": factors.attrs.get("source", ""), "hml_source": factors.attrs.get("hml_source", ""),
    }


def rolling_betas(returns: pd.Series, factors: pd.DataFrame, window: int = 252) -> pd.DataFrame:
    """Rolling daily factor betas (plain OLS over the trailing ``window`` days)."""
    names = [c for c in ("mkt_rf", "smb", "hml", "wml") if c in factors.columns]
    f = factors.reindex(returns.index)
    rf = f["rf"].fillna(0.0) if "rf" in f.columns else 0.0
    y = (returns - rf).to_numpy()
    X = np.column_stack([np.ones(len(y)), f[names].to_numpy()])
    out = np.full((len(y), len(names)), np.nan)
    ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
    for i in range(window, len(y) + 1):
        m = ok[i - window:i]
        if m.sum() < window * 0.8:
            continue
        coef, *_ = np.linalg.lstsq(X[i - window:i][m], y[i - window:i][m], rcond=None)
        out[i - 1] = coef[1:]
    return pd.DataFrame(out, index=returns.index, columns=names)
