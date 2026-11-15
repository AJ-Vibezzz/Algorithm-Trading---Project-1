"""Stress tests and the daily limit monitor.

Five complementary views of "what could go wrong":

``window_stress``      how the strategy actually behaved in named historical
                       episodes inside the back-test (the brief suggests March
                       2020, the 2018 small-cap correction and a momentum crash).
``worst_periods``      the worst 1-day / 1-week / 1-month / 1-quarter stretches
                       found in the data itself - for the strategy, a benchmark
                       or the momentum factor. This identifies "momentum crash"
                       episodes from evidence rather than by hand-picking dates.
``replay_stress``      take one day's holdings (say, the latest) and replay a
                       historical episode on them: what would that crash do to
                       *this* book, and how much of it would be locked at the
                       lower circuit and unsellable?
``market_shock_table`` an instantaneous market fall applied through the
                       portfolio's beta.
``liquidity_stress``   how long the book takes to sell, and what it costs, when
                       volumes dry up and spreads widen.

Plus ``daily_var_backtest`` (is the VaR model any good?) and ``limit_monitor``
(every day any written limit was breached).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .backtest import BacktestResult
from .config import StrategyConfig
from .costs import CostModel
from .market import MarketData
from .risk import (betas, christoffersen_independence, drawdown_series, fixed_weight_returns, kupiec_pof,
                   liquidity_profile, var_es)


def _weights_on(result: BacktestResult, md: MarketData, date) -> np.ndarray:
    """Portfolio weights (fraction of NAV) on ``date``, aligned to ``md.sids``."""
    pos = result.positions[result.positions["date"] == pd.Timestamp(date)]
    w = np.zeros(md.N)
    if len(pos):
        w[np.searchsorted(md.sids, pos["sid"].to_numpy())] = pos["weight"].to_numpy()
    return w


def _compound(r: pd.Series) -> float:
    return float((1.0 + r.fillna(0.0)).prod() - 1.0) if len(r) else np.nan


# --------------------------------------------------------------------------- #
# Historical episodes inside the back-test
# --------------------------------------------------------------------------- #
def window_stress(result: BacktestResult, md: MarketData, benchmarks: pd.DataFrame, windows: Iterable) -> pd.DataFrame:
    """Strategy and benchmark behaviour in each named window (``name``, ``start``, ``end``)."""
    r = result.returns
    rows = []
    lock_long = None
    if len(result.positions):
        p = result.positions
        ti = np.searchsorted(md.dates.values, p["date"].to_numpy().astype("datetime64[ns]"))
        ci = np.searchsorted(md.sids, p["sid"].to_numpy())
        lock_long = p.assign(locked_down=md.lock[ti, ci] == -1)
    for w in windows:
        start, end = pd.Timestamp(w.start), pd.Timestamp(w.end)
        inside = r.loc[start:end]
        row: Dict[str, object] = {"window": w.name, "start": start.date(), "end": end.date(), "days": len(inside)}
        if len(inside) == 0:
            row["note"] = "outside the back-test period - use replay_stress"
            for name in benchmarks.columns:
                row[f"{name} return"] = np.nan
            rows.append(row)
            continue
        nav = (1.0 + inside).cumprod()
        row.update({
            "strategy_return": _compound(inside),
            "strategy_max_drawdown": float(drawdown_series(pd.concat([pd.Series([1.0]), nav])).min()),
            "strategy_worst_day": float(inside.min()),
            "average_exposure": float(result.daily["exposure"].loc[start:end].mean()),
        })
        for name in benchmarks.columns:
            b = benchmarks[name].loc[start:end]
            row[f"{name} return"] = _compound(b)
        if lock_long is not None:
            sub = lock_long[(lock_long["date"] >= start) & (lock_long["date"] <= end)]
            by_day = sub.groupby("date").apply(lambda x: x.loc[x["locked_down"], "weight"].sum())
            row["holding_days_locked_at_lower_band"] = int(sub["locked_down"].sum())
            row["max_share_of_book_locked_down"] = float(by_day.max()) if len(by_day) else 0.0
        b = result.blocks
        if len(b):
            sells = b[(b["date"] >= start) & (b["date"] <= end) & (b["side"] == "SELL")]
            row["sell_order_days_blocked"] = int(len(sells))
        wo = result.writeoffs
        row["writeoffs"] = int(((wo["date"] >= start) & (wo["date"] <= end)).sum()) if len(wo) else 0
        ev = result.risk_events
        row["risk_events"] = int(((ev["date"] >= start) & (ev["date"] <= end)).sum()) if len(ev) else 0
        rows.append(row)
    return pd.DataFrame(rows).set_index("window") if rows else pd.DataFrame()


def worst_periods(returns: pd.Series, horizons=(1, 5, 21, 63), top: int = 5) -> pd.DataFrame:
    """The ``top`` worst non-overlapping windows of each length (in trading days)."""
    r = returns.dropna()
    log = np.log1p(r)
    rows = []
    for h in horizons:
        roll = np.expm1(log.rolling(h).sum()).dropna()
        taken = np.zeros(len(r), bool)
        pos_of = {d: i for i, d in enumerate(r.index)}
        found = 0
        for end_date, value in roll.sort_values().items():
            i1 = pos_of[end_date]
            i0 = i1 - h + 1
            if taken[i0:i1 + 1].any():
                continue
            taken[i0:i1 + 1] = True
            rows.append({"horizon_days": h, "rank": found + 1, "start": r.index[i0].date(),
                         "end": end_date.date(), "return": float(value)})
            found += 1
            if found >= top:
                break
    return pd.DataFrame(rows).set_index(["horizon_days", "rank"]) if rows else pd.DataFrame()


def episode_table(episodes: pd.DataFrame, series: Dict[str, pd.Series]) -> pd.DataFrame:
    """Returns of several series over the episodes found by ``worst_periods``.

    Typical use: find the worst months of the momentum factor, then tabulate
    what the strategy and the benchmark did in those same months.
    """
    out = episodes.copy()
    for name, r in series.items():
        out[name] = [_compound(r.loc[pd.Timestamp(s):pd.Timestamp(e)]) for s, e in zip(out["start"], out["end"])]
    return out


# --------------------------------------------------------------------------- #
# Replay an episode on a given book
# --------------------------------------------------------------------------- #
def replay_stress(md: MarketData, weights: np.ndarray, start, end, proxy_ret: Optional[np.ndarray] = None,
                  name: str = "") -> Dict[str, object]:
    """Hold ``weights`` (fraction of NAV; the rest is cash) through [start, end], no trading.

    A stock that was not yet listed at the start of the episode is given the
    proxy return (the benchmark) instead; ``coverage`` says how much of the
    book has its own history, so the reader can judge the result.
    """
    r0 = md.first_on_or_after(start)
    r1 = md.loc(end)
    cols = np.where(weights > 0)[0]
    if r1 <= r0 or len(cols) == 0:
        return {"window": name, "note": "no data for this window"}
    own = md.first_row[cols] < r0
    rets = np.nan_to_num(md.ret[r0:r1 + 1][:, cols])
    if proxy_ret is not None:
        proxy = np.nan_to_num(proxy_ret[r0:r1 + 1])
        rets[:, ~own] = proxy[:, None]
    else:
        rets[:, ~own] = 0.0
    growth = np.cumprod(1.0 + rets, axis=0)
    w = weights[cols]
    value = growth @ w + (1.0 - w.sum())
    path = pd.Series(np.concatenate([[1.0], value]))
    daily = path.pct_change().dropna()
    locked = (md.lock[r0:r1 + 1][:, cols] == -1) & own[None, :]
    locked_share = (locked * (growth * w)).sum(axis=1) / value
    return {
        "window": name, "start": md.dates[r0].date(), "end": md.dates[r1].date(),
        "book_return": float(value[-1] - 1.0),
        "book_max_drawdown": float(drawdown_series(path).min()),
        "book_worst_day": float(daily.min()),
        "coverage_own_history": float(w[own].sum() / w.sum()),
        "max_share_locked_at_lower_band": float(locked_share.max()),
        "days_with_any_holding_locked_down": int(locked.any(axis=1).sum()),
        "invested_fraction": float(w.sum()),
    }


# --------------------------------------------------------------------------- #
# Hypothetical shocks
# --------------------------------------------------------------------------- #
def market_shock_table(md: MarketData, weights: np.ndarray, t: int, benchmark_ret: np.ndarray,
                       shocks: Iterable[float], nav: float, beta_lookback: int = 252) -> pd.DataFrame:
    """Loss from an instantaneous market move, transmitted through the book's beta.

    This is a first-order estimate: it ignores stock-specific moves and assumes
    no trading during the shock. Small caps have historically fallen more than
    their measured beta implies in sharp sell-offs, so read it as a floor.
    """
    cols = np.where(weights > 0)[0]
    b = betas(md, t, benchmark_ret, beta_lookback, cols)
    beta_p = float(b @ weights[cols]) if len(cols) else 0.0
    rows = [{"market_shock": s, "portfolio_beta_x_exposure": beta_p, "estimated_return": beta_p * s,
             "estimated_pnl_inr": beta_p * s * nav} for s in shocks]
    return pd.DataFrame(rows).set_index("market_shock")


def liquidity_stress(md: MarketData, cfg: StrategyConfig, cost_model: CostModel, weights: np.ndarray, t: int,
                     nav: float) -> pd.DataFrame:
    """Time and cost to liquidate the book when volume falls and spreads widen.

    Each scenario cuts every stock's average daily volume by a haircut and
    multiplies its bid-ask spread. The book is sold at the participation cap.
    """
    ex = cfg.execution
    cols = np.where(weights > 0)[0]
    value = weights[cols] * nav
    adv = np.nan_to_num(md.adv(ex.adv_window_days)[t, cols])
    vol = md.volatility(cfg.costs.impact_vol_window_days)[t, cols]
    vol = np.where(np.isfinite(vol), vol, np.nanmedian(vol) if np.isfinite(vol).any() else 0.03)
    hs = md.half_spread(cfg.costs.spread_model, cfg.costs.spread_window_days, cfg.costs.fixed_half_spread_bps,
                        cfg.costs.max_half_spread_bps)[t, cols]
    sell_bps = cost_model.per_side_statutory_bps(t)["sell_bps"] / 1e4
    haircuts = [0.0] + list(cfg.stress.adv_haircuts)
    mults = [1.0] + list(cfg.stress.spread_multipliers)
    rows = []
    for h in haircuts:
        for m in mults:
            cap = ex.participation_cap * adv * (1.0 - h)
            with np.errstate(divide="ignore", invalid="ignore"):
                days = np.where(cap > 0, value / cap, np.inf)
            prof = liquidity_profile(value, adv * (1.0 - h), ex.participation_cap)
            order = np.argsort(days)
            cum = np.cumsum(value[order]) / value.sum() if value.sum() > 0 else np.array([])
            def days_for(share: float) -> float:
                if len(cum) == 0:
                    return 0.0
                k = int(np.searchsorted(cum, share - 1e-12))
                return float(days[order][min(k, len(cum) - 1)])
            # every daily slice trades at the participation cap, so impact per rupee is constant
            rate = hs * m + cost_model.k * vol * np.sqrt(ex.participation_cap) + sell_bps
            rows.append({
                "adv_haircut": h, "spread_multiplier": m,
                "days_to_sell_50pct": days_for(0.5), "days_to_sell_90pct": days_for(0.9),
                "days_to_sell_all": float(days.max()) if len(days) else 0.0,
                **prof,
                "liquidation_cost_pct_nav": float((value * rate).sum() / nav),
            })
    return pd.DataFrame(rows).set_index(["adv_haircut", "spread_multiplier"])


# --------------------------------------------------------------------------- #
# VaR back-test and limit monitor
# --------------------------------------------------------------------------- #
def daily_var_backtest(result: BacktestResult, md: MarketData, cfg: StrategyConfig) -> Dict[str, object]:
    """Each day's ex-ante VaR / ES of the actual book against the next day's return.

    The VaR for day t+1 is computed at the close of day t from that day's
    holdings (historical simulation over ``risk.var.lookback_days``). A breach
    is a next-day loss larger than the VaR. Kupiec's test asks whether the
    number of breaches matches the confidence level; Christoffersen's asks
    whether they cluster.
    """
    v = cfg.risk.var
    dates = result.daily.index
    r = result.returns
    pos = result.positions
    out = pd.DataFrame(index=dates, columns=["var", "es", "next_day_return", "breach"], dtype=float)
    if len(pos) == 0:
        return {"series": out, "summary": {}}
    ci = np.searchsorted(md.sids, pos["sid"].to_numpy())
    groups = pos.assign(col=ci).groupby("date")
    for i, d in enumerate(dates[:-1]):
        if d not in groups.groups:
            continue
        g = groups.get_group(d)
        w = np.zeros(md.N)
        w[g["col"].to_numpy()] = g["weight"].to_numpy()
        t = md.loc(d)
        hist = fixed_weight_returns(md, t, w, v.lookback_days)
        var, _ = var_es(hist, v.confidence, v.method, 1)
        _, es = var_es(hist, cfg.risk.es.confidence, v.method, 1)
        nxt = r.iloc[i + 1]
        out.iloc[i] = [var, es, nxt, float(nxt < -var) if np.isfinite(var) else np.nan]
    valid = out.dropna(subset=["var"])
    breaches = valid["breach"].to_numpy().astype(int)
    summary = {
        "days_tested": int(len(valid)), "confidence": v.confidence, "method": v.method,
        "average_var": float(valid["var"].mean()) if len(valid) else np.nan,
        "max_var": float(valid["var"].max()) if len(valid) else np.nan,
        "average_es": float(valid["es"].mean()) if len(valid) else np.nan,
        "var_limit": v.limit_pct_nav, "es_limit": cfg.risk.es.limit_pct_nav,
        "days_var_above_limit": int((valid["var"] > v.limit_pct_nav).sum()),
        "days_es_above_limit": int((valid["es"] > cfg.risk.es.limit_pct_nav).sum()),
        "average_loss_on_breach_days": float(-valid.loc[valid["breach"] == 1, "next_day_return"].mean())
        if breaches.sum() else np.nan,
        **{f"kupiec_{k}": val for k, val in kupiec_pof(len(valid), int(breaches.sum()), v.confidence).items()},
        **{f"christoffersen_{k}": val for k, val in christoffersen_independence(breaches).items()},
    }
    return {"series": out, "summary": summary}


def limit_monitor(result: BacktestResult, md: MarketData, cfg: StrategyConfig,
                  var_series: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Every (day, limit) on which a written limit was exceeded.

    Position and sector weights can drift above their caps between rebalances
    as prices move; those days are listed so the policy can say what tolerance
    is acceptable and who signs off on it.
    """
    rk, pf, ex = cfg.risk, cfg.portfolio, cfg.execution
    rows: List[dict] = []

    def add(dates, values, name, limit):
        for d, val in zip(dates, values):
            rows.append({"date": d, "limit": name, "value": float(val), "threshold": float(limit)})

    dd = drawdown_series(result.nav)
    soft, hard = dd[dd <= -rk.drawdown.soft_limit], dd[dd <= -rk.drawdown.hard_limit]
    add(soft.index, soft.to_numpy(), "drawdown_soft", -rk.drawdown.soft_limit)
    add(hard.index, hard.to_numpy(), "drawdown_hard", -rk.drawdown.hard_limit)
    if var_series is not None and len(var_series):
        v = var_series["var"].dropna()
        e = var_series["es"].dropna()
        add(v[v > rk.var.limit_pct_nav].index, v[v > rk.var.limit_pct_nav], "var", rk.var.limit_pct_nav)
        add(e[e > rk.es.limit_pct_nav].index, e[e > rk.es.limit_pct_nav], "expected_shortfall", rk.es.limit_pct_nav)
    pos = result.positions
    if len(pos):
        tol = 1e-9
        mx = pos.groupby("date")["weight"].max()
        add(mx[mx > pf.max_weight_per_stock + tol].index, mx[mx > pf.max_weight_per_stock + tol],
            "stock_weight", pf.max_weight_per_stock)
        sector = pos["sid"].map(dict(zip(md.sids, md.sector)))
        sw = pos.assign(sector=sector).groupby(["date", "sector"])["weight"].sum().groupby("date").max()
        add(sw[sw > pf.max_weight_per_sector + tol].index, sw[sw > pf.max_weight_per_sector + tol],
            "sector_weight", pf.max_weight_per_sector)
        lim = rk.liquidity.min_sellable_fraction
        if lim is not None:
            adv = md.adv(ex.adv_window_days)
            ti = np.searchsorted(md.dates.values, pos["date"].to_numpy().astype("datetime64[ns]"))
            ci = np.searchsorted(md.sids, pos["sid"].to_numpy())
            cap = rk.liquidity.horizon_days * ex.participation_cap * np.nan_to_num(adv[ti, ci])
            p2 = pos.assign(sellable=np.minimum(pos["value"].to_numpy(), cap))
            frac = p2.groupby("date")["sellable"].sum() / p2.groupby("date")["value"].sum()
            add(frac[frac < lim].index, frac[frac < lim], f"sellable_in_{rk.liquidity.horizon_days}d", lim)
    out = pd.DataFrame(rows, columns=["date", "limit", "value", "threshold"])
    return out.sort_values(["date", "limit"]).reset_index(drop=True)


def limit_summary(breaches: pd.DataFrame, n_days: int) -> pd.DataFrame:
    """Days in breach, and the worst reading, for each limit."""
    if breaches.empty:
        return pd.DataFrame(columns=["days_in_breach", "share_of_days", "worst_value", "threshold", "first", "last"])
    g = breaches.groupby("limit")
    worst = g.apply(lambda x: x.loc[(x["value"] - x["threshold"]).abs().idxmax(), "value"])
    return pd.DataFrame({
        "days_in_breach": g.size(), "share_of_days": g.size() / n_days, "worst_value": worst,
        "threshold": g["threshold"].first(), "first": g["date"].min(), "last": g["date"].max(),
    })
