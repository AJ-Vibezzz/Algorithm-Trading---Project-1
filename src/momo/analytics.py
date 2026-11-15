"""Post-trade analysis: every table the project brief asks for.

    performance_summary      return, volatility, Sharpe, Sortino, drawdown and its duration
    relative_summary         versus a benchmark: alpha, beta, tracking error, information ratio
    calendar_year_table      performance by year
    monthly_table            month-by-month returns
    regime_table             performance by market regime (direction, trend, volatility, drawdown)
    turnover_stats           how much the portfolio trades
    cost_stats               costs paid, by component and by year
    trade_stats              hit rate and pay-off of completed holdings
    blocked_signal_summary   how many signals each limit blocked
    attribution              P&L by stock, sector, size segment or year
    implementation_shortfall paper vs gross vs net
    capacity_sweep           performance as the fund gets bigger

These functions only *compute*. What the numbers mean, and what follows from
them, is the group's analysis to write.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .backtest import BacktestResult, RunOptions
from .costs import COMPONENTS
from .market import MarketData
from .risk import drawdown_series, max_drawdown_stats, var_es

ANNUAL_DAYS = 252


# --------------------------------------------------------------------------- #
# Headline performance
# --------------------------------------------------------------------------- #
def _monthly(returns: pd.Series) -> pd.Series:
    """Compound daily returns into calendar months."""
    return (1.0 + returns).groupby(returns.index.to_period("M")).prod() - 1.0


def performance_summary(nav: pd.Series, rf_daily: Optional[pd.Series] = None) -> Dict[str, object]:
    """Standard performance statistics from a daily NAV series.

    Sharpe and Sortino use returns in excess of the risk-free rate and are
    annualised with sqrt(252). Sortino divides by downside deviation: the root
    mean square of negative excess returns (Sortino and Price 1994).
    """
    r = nav.pct_change().dropna()
    if len(r) < 2:
        return {}
    rf = rf_daily.reindex(r.index).fillna(0.0) if rf_daily is not None else pd.Series(0.0, index=r.index)
    ex = r - rf
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    total = nav.iloc[-1] / nav.iloc[0] - 1.0
    cagr = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else np.nan
    vol = r.std(ddof=1) * np.sqrt(ANNUAL_DAYS)
    sharpe = ex.mean() / ex.std(ddof=1) * np.sqrt(ANNUAL_DAYS) if ex.std(ddof=1) > 0 else np.nan
    downside = np.sqrt((np.minimum(ex, 0.0) ** 2).mean())
    sortino = ex.mean() / downside * np.sqrt(ANNUAL_DAYS) if downside > 0 else np.nan
    dd = max_drawdown_stats(nav)
    m = _monthly(r)
    var95, _ = var_es(r.to_numpy(), 0.95)
    var99, _ = var_es(r.to_numpy(), 0.99)
    _, es975 = var_es(r.to_numpy(), 0.975)
    # Standard error of the Sharpe ratio under i.i.d. returns (Lo 2002), annualised.
    sr_d = ex.mean() / ex.std(ddof=1) if ex.std(ddof=1) > 0 else np.nan
    se = np.sqrt((1.0 + 0.5 * sr_d ** 2) / len(ex)) * np.sqrt(ANNUAL_DAYS) if np.isfinite(sr_d) else np.nan
    return {
        "start": nav.index[0].date(), "end": nav.index[-1].date(), "years": round(years, 2),
        "total_return": total, "cagr": cagr, "annual_volatility": vol,
        "sharpe": sharpe, "sharpe_std_error": se, "sortino": sortino,
        "max_drawdown": dd["max_drawdown"],
        "drawdown_peak": dd["peak"].date() if dd["peak"] is not None else None,
        "drawdown_trough": dd["trough"].date() if dd["trough"] is not None else None,
        "drawdown_recovered": dd["recovery"].date() if dd["recovery"] is not None else "not recovered",
        "days_peak_to_trough": dd["days_peak_to_trough"],
        "days_trough_to_recovery": dd["days_to_recover"],
        "longest_underwater_trading_days": dd["longest_underwater_days"],
        "calmar": cagr / abs(dd["max_drawdown"]) if dd["max_drawdown"] < 0 else np.nan,
        "skewness_daily": float(r.skew()), "excess_kurtosis_daily": float(r.kurt()),
        "best_day": float(r.max()), "worst_day": float(r.min()),
        "best_month": float(m.max()), "worst_month": float(m.min()),
        "hit_rate_months": float((m > 0).mean()), "hit_rate_days": float((r > 0).mean()),
        "avg_up_month": float(m[m > 0].mean()) if (m > 0).any() else np.nan,
        "avg_down_month": float(m[m <= 0].mean()) if (m <= 0).any() else np.nan,
        "realised_var_95_1d": var95, "realised_var_99_1d": var99, "realised_es_975_1d": es975,
    }


def relative_summary(nav: pd.Series, bench_ret: pd.Series, rf_daily: Optional[pd.Series] = None) -> Dict[str, float]:
    """Performance relative to one benchmark (daily data)."""
    r = nav.pct_change().dropna()
    b = bench_ret.reindex(r.index)
    ok = b.notna()
    r, b = r[ok], b[ok]
    if len(r) < 20:
        return {}
    rf = rf_daily.reindex(r.index).fillna(0.0) if rf_daily is not None else pd.Series(0.0, index=r.index)
    active = r - b
    years = len(r) / ANNUAL_DAYS
    cagr_s = (1 + r).prod() ** (1 / years) - 1
    cagr_b = (1 + b).prod() ** (1 / years) - 1
    te = active.std(ddof=1) * np.sqrt(ANNUAL_DAYS)
    beta = np.cov(r - rf, b - rf)[0, 1] / (b - rf).var(ddof=1)
    alpha = ((r - rf).mean() - beta * (b - rf).mean()) * ANNUAL_DAYS
    ms, mb = _monthly(r), _monthly(b)
    up, down = mb > 0, mb < 0
    return {
        "benchmark_cagr": cagr_b, "excess_cagr": cagr_s - cagr_b, "tracking_error": te,
        "information_ratio": active.mean() * ANNUAL_DAYS / te if te > 0 else np.nan,
        "beta": beta, "alpha_annual": alpha, "correlation": float(np.corrcoef(r, b)[0, 1]),
        "up_capture": float(ms[up].mean() / mb[up].mean()) if up.any() else np.nan,
        "down_capture": float(ms[down].mean() / mb[down].mean()) if down.any() else np.nan,
        "share_of_months_ahead": float((ms > mb).mean()),
        "benchmark_max_drawdown": float(drawdown_series((1 + b).cumprod()).min()),
    }


def calendar_year_table(returns: pd.Series, benchmarks: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Return, volatility and worst drawdown for each calendar year (partial years included)."""
    rows = []
    for year, r in returns.groupby(returns.index.year):
        row = {"year": year, "days": len(r), "return": (1 + r).prod() - 1,
               "volatility": r.std(ddof=1) * np.sqrt(ANNUAL_DAYS) if len(r) > 2 else np.nan,
               "max_drawdown": drawdown_series((1 + r).cumprod()).min()}
        if benchmarks is not None:
            for name in benchmarks.columns:
                b = benchmarks[name].reindex(r.index).fillna(0.0)
                row[name] = (1 + b).prod() - 1
        rows.append(row)
    return pd.DataFrame(rows).set_index("year")


def monthly_table(returns: pd.Series) -> pd.DataFrame:
    """Rows = years, columns = months 1-12 plus the full-year return."""
    m = _monthly(returns)
    table = pd.DataFrame({"year": m.index.year, "month": m.index.month, "r": m.to_numpy()}).pivot(
        index="year", columns="month", values="r")
    table["year"] = (1 + returns).groupby(returns.index.year).prod() - 1
    return table


def regime_table(returns: pd.Series, bench_ret: pd.Series, rf_daily: Optional[pd.Series] = None) -> pd.DataFrame:
    """Performance split by the state of the market.

    Each day is classified using benchmark information up to the *previous*
    day, so the regimes are ones an investor could have identified at the time:

    ``trend``       benchmark above / below its 200-day moving average
    ``volatility``  low / mid / high tercile of 60-day benchmark volatility
    ``drawdown``    benchmark more than 10% below its peak, or not
    ``direction``   benchmark up or down on the day (descriptive only - not knowable in advance)
    """
    r = returns.copy()
    b = bench_ret.reindex(r.index).fillna(0.0)
    rf = rf_daily.reindex(r.index).fillna(0.0) if rf_daily is not None else pd.Series(0.0, index=r.index)
    level = (1 + b).cumprod()
    ma = level.rolling(200, min_periods=100).mean()
    vol = b.rolling(60, min_periods=30).std()
    dd = level / level.cummax() - 1
    labels = {
        "trend": pd.Series(np.where(level > ma, "above 200d average", "below 200d average"), index=r.index).where(ma.notna()).shift(1),
        "volatility": pd.qcut(vol, 3, labels=["low vol", "mid vol", "high vol"]).astype(object).shift(1),
        "drawdown": pd.Series(np.where(dd < -0.10, "benchmark >10% off peak", "benchmark near peak"), index=r.index).shift(1),
        "direction": pd.Series(np.where(b > 0, "benchmark up day", "benchmark down day"), index=r.index),
    }
    rows = []
    for regime, lab in labels.items():
        for state, idx in lab.dropna().groupby(lab.dropna()).groups.items():
            rs, bs, ex = r.loc[idx], b.loc[idx], (r - rf).loc[idx]
            rows.append({
                "regime": regime, "state": state, "days": len(rs), "share_of_days": len(rs) / len(r),
                "strategy_ann_return": rs.mean() * ANNUAL_DAYS, "benchmark_ann_return": bs.mean() * ANNUAL_DAYS,
                "strategy_ann_vol": rs.std(ddof=1) * np.sqrt(ANNUAL_DAYS),
                "strategy_sharpe": ex.mean() / ex.std(ddof=1) * np.sqrt(ANNUAL_DAYS) if ex.std(ddof=1) > 0 else np.nan,
                "share_of_days_ahead": float((rs > bs).mean()),
            })
    return pd.DataFrame(rows).set_index(["regime", "state"])


# --------------------------------------------------------------------------- #
# Trading activity and costs
# --------------------------------------------------------------------------- #
def turnover_stats(result: BacktestResult) -> Dict[str, float]:
    """One-way turnover = (buys + sells) / 2, as a multiple of average NAV per year."""
    d = result.daily
    years = max(len(d) / ANNUAL_DAYS, 1e-9)
    avg_nav = d["nav"].mean()
    traded = d["buy_value"].sum() + d["sell_value"].sum()
    one_way = traded / 2.0 / avg_nav / years
    return {
        "annual_one_way_turnover": one_way,
        "implied_average_holding_months": 12.0 / one_way if one_way > 0 else np.nan,
        "total_traded_value": traded,
        "trading_days_with_fills": int(((d["buy_value"] + d["sell_value"]) > 0).sum()),
        "average_positions": float(d["n_positions"].mean()),
        "average_exposure": float(d["exposure"].mean()),
        "average_cash_weight": float((d["cash"] / d["nav"]).mean()),
    }


def turnover_by_year(result: BacktestResult) -> pd.DataFrame:
    d = result.daily
    g = d.groupby(d.index.year)
    out = pd.DataFrame({
        "one_way_turnover": (g["buy_value"].sum() + g["sell_value"].sum()) / 2.0 / g["nav"].mean(),
        "cost_pct_of_avg_nav": g["cost_total"].sum() / g["nav"].mean(),
        "avg_positions": g["n_positions"].mean(),
        "avg_exposure": g["exposure"].mean(),
    })
    out.index.name = "year"
    return out


def cost_stats(result: BacktestResult) -> pd.DataFrame:
    """Costs paid by component: rupees, basis points of traded value, and % of average NAV per year."""
    d = result.daily
    years = max(len(d) / ANNUAL_DAYS, 1e-9)
    traded = d["buy_value"].sum() + d["sell_value"].sum()
    rows = []
    for c in COMPONENTS:
        amount = d[f"cost_{c}"].sum()
        rows.append({"component": c, "rupees": amount,
                     "bps_of_traded_value": 1e4 * amount / traded if traded > 0 else np.nan,
                     "pct_of_avg_nav_per_year": amount / d["nav"].mean() / years})
    total = d["cost_total"].sum()
    rows.append({"component": "TOTAL", "rupees": total,
                 "bps_of_traded_value": 1e4 * total / traded if traded > 0 else np.nan,
                 "pct_of_avg_nav_per_year": total / d["nav"].mean() / years})
    out = pd.DataFrame(rows).set_index("component")
    out["share_of_total"] = out["rupees"] / total if total > 0 else np.nan
    return out


def trade_stats(result: BacktestResult, md: MarketData) -> Dict[str, object]:
    """Hit rate and pay-off of completed holdings.

    A "holding" runs from the day a stock enters the book to the day it leaves.
    Its net P&L is the sum of its daily P&L less its costs; its return is that
    P&L over the money put in (total purchases during the holding).
    """
    pnl = result.pnl
    if pnl.empty:
        return {"holdings": pd.DataFrame(), "summary": {}}
    pos = np.searchsorted(md.dates.values, pnl["date"].to_numpy().astype("datetime64[ns]"))
    df = pnl.assign(row=pos).sort_values(["sid", "row"])
    new_spell = (df["sid"] != df["sid"].shift()) | (df["row"] - df["row"].shift() > 1)
    df["holding"] = new_spell.cumsum()
    g = df.groupby("holding")
    holdings = g.agg(sid=("sid", "first"), entry=("date", "min"), exit=("date", "max"),
                     days=("row", lambda s: int(s.max() - s.min() + 1)),
                     pnl_gross=("pnl_gross", "sum"), cost=("cost", "sum"))
    holdings["pnl_net"] = holdings["pnl_gross"] - holdings["cost"]
    buys = result.trades[result.trades["side"] == "BUY"]
    if len(buys):
        b = buys.assign(row=np.searchsorted(md.dates.values, buys["date"].to_numpy().astype("datetime64[ns]")))
        key = df[["sid", "row", "holding"]]
        bought = b.merge(key, on=["sid", "row"], how="left").groupby("holding")["value"].sum()
        holdings["invested"] = bought.reindex(holdings.index)
    else:
        holdings["invested"] = np.nan
    holdings["return_net"] = holdings["pnl_net"] / holdings["invested"]
    symbol_of = dict(zip(md.sids, md.symbols))
    sector_of = dict(zip(md.sids, md.sector))
    holdings["symbol"] = holdings["sid"].map(symbol_of)
    holdings["sector"] = holdings["sid"].map(sector_of)
    last_day = result.daily.index[-1]
    holdings["closed"] = holdings["exit"] < last_day
    done = holdings[holdings["closed"]]
    wins, losses = done[done["pnl_net"] > 0], done[done["pnl_net"] <= 0]
    summary = {
        "completed_holdings": int(len(done)), "open_at_end": int((~holdings["closed"]).sum()),
        "hit_rate": float(len(wins) / len(done)) if len(done) else np.nan,
        "average_winner_return": float(wins["return_net"].mean()) if len(wins) else np.nan,
        "average_loser_return": float(losses["return_net"].mean()) if len(losses) else np.nan,
        "payoff_ratio": float(wins["pnl_net"].mean() / abs(losses["pnl_net"].mean())) if len(wins) and len(losses) else np.nan,
        "profit_factor": float(wins["pnl_net"].sum() / abs(losses["pnl_net"].sum())) if len(losses) and losses["pnl_net"].sum() != 0 else np.nan,
        "median_holding_trading_days": float(done["days"].median()) if len(done) else np.nan,
        "share_of_pnl_from_top_10_holdings": float(done["pnl_net"].nlargest(10).sum() / done["pnl_net"].sum()) if len(done) and done["pnl_net"].sum() != 0 else np.nan,
    }
    return {"holdings": holdings.sort_values("pnl_net", ascending=False), "summary": summary}


def blocked_signal_summary(result: BacktestResult) -> Dict[str, pd.DataFrame]:
    """How many signals the limits blocked - at the screening stage and at execution."""
    out: Dict[str, pd.DataFrame] = {}
    dec = result.decisions
    n_dec = max(len(dec), 1)

    # 1. top-ranked stocks removed before portfolio construction
    scr = result.screened
    if len(scr):
        by_reason = scr.groupby("reason").size().rename("signals_removed").to_frame()
        by_reason["per_rebalance"] = by_reason["signals_removed"] / n_dec
        slots = float(dec["n_target"].sum()) if "n_target" in dec else np.nan
        by_reason["share_of_target_slots"] = by_reason["signals_removed"] / slots if slots else np.nan
        out["screening"] = by_reason.sort_values("signals_removed", ascending=False)
        out["screening_by_year"] = scr.groupby([scr["date"].dt.year, "reason"]).size().unstack(fill_value=0)
    else:
        out["screening"] = pd.DataFrame(columns=["signals_removed", "per_rebalance", "share_of_target_slots"])

    # 2. what happened to the orders that were placed
    o = result.orders
    if len(o):
        o = o.assign(fill_ratio=(o["filled_value"] / o["wanted_value"]).clip(upper=1.0))
        status = o.groupby(["side", "status"]).agg(orders=("order_id", "size"), wanted_value=("wanted_value", "sum"),
                                                   filled_value=("filled_value", "sum"))
        status["value_fill_ratio"] = status["filled_value"] / status["wanted_value"]
        out["orders"] = status
        filled = o[o["status"] == "filled"]
        out["speed"] = pd.DataFrame({
            "orders": [len(o)],
            "filled_completely": [len(filled)],
            "filled_on_first_day": [int((filled["fill_days"] == 1).sum())],
            "needed_several_days": [int((filled["fill_days"] > 1).sum())],
            "average_days_to_fill": [float(filled["fill_days"].mean()) if len(filled) else np.nan],
            "not_completed": [int((o["status"] != "filled").sum())],
            "value_fill_ratio": [float(o["filled_value"].sum() / o["wanted_value"].sum())],
        }).T.rename(columns={0: "value"})

    # 3. order-days that fell short, by cause
    b = result.blocks
    if len(b):
        cause = b.groupby(["side", "reason"]).agg(order_days=("order_id", "size"), orders=("order_id", "nunique"),
                                                  shortfall_value=("wanted_value", "sum"))
        cause["shortfall_value"] -= b.groupby(["side", "reason"])["filled_value"].sum()
        out["execution"] = cause.sort_values("order_days", ascending=False)
    else:
        out["execution"] = pd.DataFrame(columns=["order_days", "orders", "shortfall_value"])
    return out


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #
def attribution(result: BacktestResult, md: MarketData, by: str = "sector",
                segment_of: Optional[Dict[int, str]] = None) -> pd.DataFrame:
    """P&L split by ``sector``, ``stock``, ``year`` or size ``segment``.

    ``contribution`` is the sum over days of (P&L / previous day's NAV): the
    part of the cumulative arithmetic return that came from that bucket.
    Costs are attributed to the stock whose trade incurred them.
    """
    pnl = result.pnl.copy()
    if pnl.empty:
        return pd.DataFrame()
    nav_prev = result.daily["nav"].shift(1).fillna(result.meta["capital"])
    pnl["nav_prev"] = pnl["date"].map(nav_prev)
    pnl["net"] = pnl["pnl_gross"] - pnl["cost"]
    if by == "sector":
        pnl["bucket"] = pnl["sid"].map(dict(zip(md.sids, md.sector)))
    elif by == "stock":
        pnl["bucket"] = pnl["sid"].map(dict(zip(md.sids, md.symbols)))
    elif by == "year":
        pnl["bucket"] = pnl["date"].dt.year
    elif by == "segment":
        pnl["bucket"] = pnl["sid"].map(segment_of or {}).fillna("unknown")
    else:
        raise ValueError("by must be sector, stock, year or segment")
    g = pnl.groupby("bucket")
    out = pd.DataFrame({
        "pnl_gross": g["pnl_gross"].sum(), "cost": g["cost"].sum(), "pnl_net": g["net"].sum(),
        "contribution_gross": g.apply(lambda x: (x["pnl_gross"] / x["nav_prev"]).sum()),
        "contribution_net": g.apply(lambda x: (x["net"] / x["nav_prev"]).sum()),
        "stocks": g["sid"].nunique(),
    })
    total = out["pnl_net"].sum()
    out["share_of_net_pnl"] = out["pnl_net"] / total if total != 0 else np.nan
    return out.sort_values("pnl_net", ascending=False)


def pnl_bridge(result: BacktestResult) -> pd.Series:
    """From stock-picking P&L to the change in NAV: gross P&L - costs + interest (+ hedge)."""
    d = result.daily
    return pd.Series({
        "stock P&L before costs": result.pnl["pnl_gross"].sum(),
        "trading costs": -result.pnl["cost"].sum(),
        "interest on cash": d["interest"].sum(),
        "futures hedge P&L": d["hedge_pnl"].sum(),
        "hedge costs": -(d["cost_total"].sum() - result.pnl["cost"].sum()),
        "change in NAV": d["nav"].iloc[-1] - result.meta["capital"],
    })


def implementation_shortfall(variants: Dict[str, BacktestResult], rf_daily: Optional[pd.Series] = None) -> pd.DataFrame:
    """Paper -> gross -> net: what the liquidity limits and the costs each take away."""
    rows = {}
    for name in ("paper", "gross", "net"):
        if name in variants:
            s = performance_summary(variants[name].nav, rf_daily)
            rows[name] = {"cagr": s["cagr"], "annual_volatility": s["annual_volatility"], "sharpe": s["sharpe"],
                          "max_drawdown": s["max_drawdown"],
                          "one_way_turnover": turnover_stats(variants[name])["annual_one_way_turnover"]}
    out = pd.DataFrame(rows).T
    if {"paper", "gross", "net"} <= set(out.index):
        out.loc["cost of liquidity limits (paper - gross)", "cagr"] = out.loc["paper", "cagr"] - out.loc["gross", "cagr"]
        out.loc["cost of trading (gross - net)", "cagr"] = out.loc["gross", "cagr"] - out.loc["net", "cagr"]
    return out


def capacity_sweep(ws, cfg, start, end, aums: Iterable[float], rf_daily: Optional[pd.Series] = None) -> pd.DataFrame:
    """Re-run the net back-test at different fund sizes.

    As assets grow, each order is a larger share of daily volume: more of it
    is capped, it takes longer to fill, and impact costs rise with the square
    root of size. The table shows where returns and fill rates give way; where
    to draw the capacity line is a judgement for the group.
    """
    from .pipeline import run_backtest  # local import to avoid a cycle

    rows = []
    for aum in aums:
        res = run_backtest(ws, cfg, start, end, RunOptions(f"aum_{aum:.0f}", capital=float(aum)))
        s = performance_summary(res.nav, rf_daily)
        c = cost_stats(res)
        o = res.orders
        filled = o[o["status"] == "filled"]
        rows.append({
            "fund_size_cr": aum / 1e7, "net_cagr": s["cagr"], "sharpe": s["sharpe"], "max_drawdown": s["max_drawdown"],
            "cost_pct_nav_per_year": c.loc["TOTAL", "pct_of_avg_nav_per_year"],
            "impact_bps_of_traded": c.loc["impact", "bps_of_traded_value"],
            "value_fill_ratio": o["filled_value"].sum() / o["wanted_value"].sum() if len(o) else np.nan,
            "avg_days_to_fill": filled["fill_days"].mean() if len(filled) else np.nan,
            "orders_not_completed": float((o["status"] != "filled").mean()) if len(o) else np.nan,
            "avg_exposure": res.daily["exposure"].mean(),
        })
    return pd.DataFrame(rows).set_index("fund_size_cr")
