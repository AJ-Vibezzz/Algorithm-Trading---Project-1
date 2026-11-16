"""Build the report pack: every table (CSV) and chart (PNG) for one back-test period.

``build_report_pack`` writes to ``results/<label>/``::

    tables/   one CSV per exhibit - the numbers behind every chart
    figures/  one PNG per chart
    logs/     the net run in full: daily NAV, every order, fill, blocked order-day and position
    run_manifest.json   what was run: config, fingerprint, data, library versions

The pack contains computed exhibits only. It contains no commentary: what the
exhibits show, whether the strategy is acceptable, and what to recommend are
for the group to write (docs/06_report_template.md says where each exhibit goes).
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import subprocess
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from . import analytics as an
from . import plots, stress
from .backtest import BacktestResult
from .config import StrategyConfig
from .costs import CostModel, cost_table
from .factors import factor_regression, get_factors, rolling_betas
from .pipeline import Workspace, benchmark_returns
from .risk import concentration, liquidity_profile

SYNTHETIC_NOTE = (
    "These files were produced from the SYNTHETIC TEST FIXTURE (invented data).\n"
    "They exist to show that the code runs. They are NOT back-test results and\n"
    "must not appear in the report or presentation.\n"
)


EXAMPLE_NOTE = (
    "These files were produced with a shipped EXAMPLE configuration, not with a\n"
    "strategy designed by the project group. Use them to check that the code runs\n"
    "on your data. Do not report them as your strategy's results.\n"
)


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:
        return ""


def _segments(ws: Workspace, cfg: StrategyConfig, result: BacktestResult) -> Dict[int, str]:
    """Size segment of each stock at the first decision on which it was in the universe."""
    md = ws.md
    uni = ws.universe(cfg, result.meta["rows"])
    out: Dict[int, str] = {}
    for t in result.meta["rows"]:
        snap = uni[t]
        for c in np.where(snap.in_segment)[0]:
            out.setdefault(int(md.sids[c]), snap.segment[c])
    return out


def build_report_pack(ws: Workspace, cfg: StrategyConfig, variants: Dict[str, BacktestResult],
                      out_dir: str | Path, label: str = "", capacity: Optional[pd.DataFrame] = None,
                      registry=None, verbose: bool = True) -> Dict[str, object]:
    """Compute and save every exhibit for ``variants`` (must contain "net")."""
    md = ws.md
    net = variants["net"]
    out_dir = Path(out_dir)
    tdir, fdir = out_dir / "tables", out_dir / "figures"
    tdir.mkdir(parents=True, exist_ok=True)
    fdir.mkdir(parents=True, exist_ok=True)
    kind = md.data_kind
    if kind == "synthetic":
        (out_dir / "SYNTHETIC_NOT_A_RESULT.txt").write_text(SYNTHETIC_NOTE)
    if cfg.meta.is_example:
        (out_dir / "EXAMPLE_CONFIG_NOT_YOUR_STRATEGY.txt").write_text(EXAMPLE_NOTE)
    pack: Dict[str, object] = {}

    def table(name: str, df) -> None:
        frame = df.to_frame() if isinstance(df, pd.Series) else df
        frame.to_csv(tdir / f"{name}.csv")
        pack[name] = frame

    def figure(name: str, fig) -> None:
        plots.save(fig, fdir / f"{name}.png", kind, cfg.meta.is_example)

    rf = pd.Series(md.rf_daily, index=md.dates)
    bench = benchmark_returns(ws, cfg, net)
    primary = bench.columns[0]
    r = net.returns
    last_day = net.daily.index[-1]
    t_last = md.loc(last_day)
    nav_last = float(net.nav.iloc[-1])

    # ---- 1. headline performance -------------------------------------------------------------------
    summary = {name: an.performance_summary(res.nav, rf) for name, res in variants.items()}
    for name in bench.columns:
        summary[f"benchmark: {name}"] = an.performance_summary((1.0 + bench[name].fillna(0.0)).cumprod(), rf)
    table("01_performance_summary", pd.DataFrame(summary))
    table("02_relative_to_benchmarks", pd.DataFrame({n: an.relative_summary(net.nav, bench[n], rf) for n in bench.columns}))
    years = an.calendar_year_table(r, bench)
    table("03_calendar_years", years)
    monthly = an.monthly_table(r)
    table("04_monthly_returns", monthly)
    regime = an.regime_table(r, bench[primary], rf)
    table("05_regimes", regime)
    figure("01_equity_and_drawdown", plots.equity_and_drawdown(net.nav, bench))
    figure("02_rolling_return", plots.rolling_return(r, bench[primary], bench_label=primary))
    figure("03_monthly_returns", plots.monthly_heatmap(monthly))
    figure("04_calendar_years", plots.calendar_years(years, primary))
    figure("05_regimes", plots.regimes(regime, primary))

    # ---- 2. gross vs net, turnover, costs -------------------------------------------------------------
    table("06_paper_gross_net", an.implementation_shortfall(variants, rf))
    table("07_turnover", pd.Series(an.turnover_stats(net), name="value"))
    table("07_turnover_by_year", an.turnover_by_year(net))
    costs = an.cost_stats(net)
    table("08_costs_by_component", costs)
    cm = CostModel(ws.schedule, cfg.costs, md.dates)
    table("08_cost_per_side_schedule", cost_table(cm, net.daily.index).set_index("from"))
    figure("06_paper_gross_net", plots.variants({k: v.nav for k, v in variants.items()}))
    figure("07_exposure_and_holdings", plots.exposure(net.daily))
    if costs.loc["TOTAL", "rupees"] > 0:
        figure("08_costs_by_component", plots.cost_breakdown(costs))

    # ---- 3. trades and blocked signals ----------------------------------------------------------------------
    ts = an.trade_stats(net, md)
    table("09_trade_stats", pd.Series(ts["summary"], name="value"))
    if len(ts["holdings"]):
        table("09_holdings", ts["holdings"])
    blocked = an.blocked_signal_summary(net)
    for key, frame in blocked.items():
        table(f"10_blocked_{key}", frame)
    table("10_universe_by_date", net.meta["universe_table"])
    figure("09_blocked_signals", plots.blocked_signals(blocked["screening"], blocked["execution"]))

    # ---- 4. attribution ----------------------------------------------------------------------------------------
    by_sector = an.attribution(net, md, "sector")
    table("11_attribution_by_sector", by_sector)
    table("11_attribution_by_stock", an.attribution(net, md, "stock"))
    table("11_attribution_by_year", an.attribution(net, md, "year"))
    table("11_attribution_by_segment", an.attribution(net, md, "segment", _segments(ws, cfg, net)))
    table("11_pnl_bridge", an.pnl_bridge(net).rename("rupees"))
    if len(by_sector):
        figure("10_attribution_by_sector", plots.attribution_bars(by_sector))

    # ---- 5. factor exposures ----------------------------------------------------------------------------------------
    factors = get_factors(md, ws.factors)
    for freq, tag in (("M", "monthly"), ("D", "daily")):
        reg = factor_regression(r.iloc[1:], factors, freq)
        pack[f"factor_regression_{tag}"] = reg
        if len(reg["table"]):
            table(f"12_factor_regression_{tag}", reg["table"])
            meta = {k: v for k, v in reg.items() if k not in ("table", "annual_contribution")}
            table(f"12_factor_regression_{tag}_meta", pd.Series(meta, name="value"))
            table(f"12_factor_attribution_{tag}", pd.Series(reg["annual_contribution"], name="annual_contribution"))
    best = pack["factor_regression_monthly"] if len(pack["factor_regression_monthly"]["table"]) else pack["factor_regression_daily"]
    if len(best["table"]):
        figure("11_factor_exposures", plots.factor_loadings(best["table"], best["alpha_annual"], best["r_squared"],
                                                            best["frequency"]))
    rb = rolling_betas(r, factors)
    if rb.notna().any().any():
        table("12_rolling_factor_betas", rb.dropna(how="all"))
        figure("12_rolling_factor_betas", plots.rolling_factor_betas(rb))

    # ---- 6. risk: VaR back-test, limits, concentration, liquidity ------------------------------------------------------
    vb = stress.daily_var_backtest(net, md, cfg)
    table("13_var_backtest_summary", pd.Series(vb["summary"], name="value"))
    table("13_var_backtest_series", vb["series"].dropna(how="all"))
    if vb["series"]["var"].notna().any():
        figure("13_var_backtest", plots.var_backtest(vb["series"], cfg.risk.var.limit_pct_nav, cfg.risk.var.confidence))
    breaches = stress.limit_monitor(net, md, cfg, vb["series"])
    table("14_limit_breaches", breaches.set_index("date") if len(breaches) else breaches)
    table("14_limit_breach_summary", stress.limit_summary(breaches, len(net.daily)))
    table("15_risk_events", net.risk_events.set_index("date") if len(net.risk_events) else net.risk_events)
    table("15_writeoffs", net.writeoffs.set_index("date") if len(net.writeoffs) else net.writeoffs)
    table("15_decisions", net.decisions.set_index("date") if len(net.decisions) else net.decisions)

    w_last = stress._weights_on(net, md, last_day)
    pos = net.positions
    if len(pos):
        sector = pos["sid"].map(dict(zip(md.sids, md.sector)))
        sw = pos.assign(sector=sector).groupby(["date", "sector"])["weight"].sum().unstack(fill_value=0.0)
        sw = sw.reindex(net.daily.index).fillna(0.0)
        # Companies without a sector label cannot be held to the sector limit: show them apart.
        sw = sw.rename(columns={"UNKNOWN": "(no sector label - limit not applied)"})
        table("16_sector_weights_avg_peak", pd.DataFrame({"average": sw.mean(), "peak": sw.max()}))
        figure("14_sector_weights", plots.sector_weights(sw.mean(), sw.max(), cfg.portfolio.max_weight_per_sector))
        conc = pos.groupby("date")["weight"].apply(lambda w: pd.Series(concentration(w.to_numpy()))).unstack()
        table("16_concentration", conc.describe().T[["mean", "min", "max"]])
    cols = np.where(w_last > 0)[0]
    prof = liquidity_profile(w_last[cols] * nav_last, np.nan_to_num(md.adv(cfg.execution.adv_window_days)[t_last, cols]),
                             cfg.execution.participation_cap)
    table("17_liquidity_profile_last_day", pd.Series(prof, name=str(last_day.date())))
    figure("15_liquidity_profile", plots.liquidity_profile(prof, cfg.risk.liquidity.min_sellable_fraction,
                                                           cfg.risk.liquidity.horizon_days, str(last_day.date())))

    # ---- 7. stress tests ------------------------------------------------------------------------------------------------
    win = stress.window_stress(net, md, bench, cfg.stress.windows)
    if len(win):
        table("18_stress_windows", win)
        if "strategy_return" in win.columns and win["strategy_return"].notna().any():
            figure("16_stress_windows", plots.stress_windows(win, f"{primary} return"))
    table("18_worst_periods_strategy", stress.worst_periods(r.iloc[1:]))
    table("18_worst_periods_benchmark", stress.worst_periods(bench[primary].iloc[1:]))
    if "wml" in factors.columns and factors["wml"].reindex(r.index).notna().sum() > 60:
        wml = factors["wml"].reindex(r.index).dropna()
        crashes = stress.worst_periods(wml, horizons=(21, 63))
        table("18_momentum_factor_worst_periods", stress.episode_table(
            crashes, {"strategy": r, primary: bench[primary]}).rename(columns={"return": "momentum_factor"}))
    proxy = md.index_return(primary) if primary in md.indices.columns else None
    replay = [stress.replay_stress(md, w_last, w.start, w.end, proxy, w.name) for w in cfg.stress.windows]
    if replay:
        table("19_replay_latest_book", pd.DataFrame(replay).set_index("window"))
    strat_bench = md.index_return(cfg.risk.beta.benchmark) if cfg.risk.beta.benchmark else None
    if strat_bench is None:
        from .signals import market_return
        strat_bench = market_return(md)
    if cfg.stress.market_shocks:
        table("19_market_shocks", stress.market_shock_table(md, w_last, t_last, strat_bench,
                                                            cfg.stress.market_shocks, nav_last))
    table("19_liquidity_stress", stress.liquidity_stress(md, cfg, cm, w_last, t_last, nav_last))

    # ---- 8. capacity ------------------------------------------------------------------------------------------------------
    if capacity is not None and len(capacity):
        table("20_capacity", capacity)
        figure("17_capacity", plots.capacity(capacity))

    # ---- raw logs of the net run, so any number in the pack can be traced to trades and positions -------
    ldir = out_dir / "logs"
    ldir.mkdir(exist_ok=True)
    net.daily.to_csv(ldir / "daily.csv")
    for name in ("trades", "orders", "blocks", "positions", "pnl", "screened"):
        getattr(net, name).to_csv(ldir / f"{name}.csv", index=False)

    # ---- 9. is the Sharpe ratio distinguishable from luck, given how many settings were tried? ------------
    from .research import deflated_sharpe

    excess = (r - rf.reindex(r.index).fillna(0.0)).iloc[1:]
    n_trials, trial_sharpes = 1, None
    if registry is not None:
        trials = registry.trials()
        if len(trials):
            trials = trials[trials["data_kind"] == kind] if "data_kind" in trials.columns else trials
            n_trials = max(1, int(trials["fingerprint"].nunique()))
            trial_sharpes = trials.drop_duplicates("fingerprint")["sharpe_daily"].to_numpy(float)
    table("21_sharpe_significance", pd.Series(deflated_sharpe(excess, n_trials, trial_sharpes), name="value"))

    manifest = {
        "label": label, "written_at": dt.datetime.now().isoformat(timespec="seconds"),
        "data_kind": kind, "period": [str(net.daily.index[0].date()), str(last_day.date())],
        "config_fingerprint": cfg.fingerprint(), "config": cfg.to_dict(),
        "is_example_config": bool(cfg.meta.is_example),
        "data": {"first_day": str(md.dates[0].date()), "last_day": str(md.dates[-1].date()),
                 "trading_days": md.T, "companies": md.N, "risk_free_supplied": not md.rf_missing,
                 "factor_source": factors.attrs.get("source", ""), "hml_source": factors.attrs.get("hml_source", "none")},
        "statutory_entries_not_verified": ws.schedule.unverified(),
        "versions": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__},
        "git_commit": _git_commit(),
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    pack["manifest"] = manifest
    if verbose:
        n_t, n_f = len(list(tdir.glob("*.csv"))), len(list(fdir.glob("*.png")))
        print(f"Report pack written to {out_dir}: {n_t} tables, {n_f} figures"
              + ("   *** SYNTHETIC FIXTURE - NOT RESULTS ***" if kind == "synthetic" else "")
              + ("   *** EXAMPLE CONFIG - NOT YOUR STRATEGY ***" if cfg.meta.is_example else ""))
    return pack
