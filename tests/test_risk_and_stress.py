"""Risk measures, the drawdown controller, VaR back-tests and stress tests."""

import numpy as np
import pandas as pd
import pytest

from momo import stress
from momo.config import DrawdownCfg
from momo.pipeline import benchmark_returns
from momo.risk import (DrawdownController, christoffersen_independence, concentration, kupiec_pof,
                       liquidity_profile, max_drawdown_stats, var_es)


def test_var_and_es_on_a_normal_sample():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0, 0.01, 200_000)
    for method in ("historical", "parametric", "cornish_fisher"):
        var, es = var_es(r, 0.99, method)
        assert var == pytest.approx(0.01 * 2.326, rel=0.03), method
        assert es == pytest.approx(0.01 * 2.665, rel=0.03), method
        assert es > var
    var10, _ = var_es(r, 0.99, "parametric", horizon_days=10)
    var1, _ = var_es(r, 0.99, "parametric")
    assert var10 == pytest.approx(var1 * np.sqrt(10))
    assert np.isnan(var_es(r[:10], 0.99)[0]), "too little history must not produce a number"


def test_fat_tails_raise_cornish_fisher_var_above_gaussian():
    rng = np.random.default_rng(1)
    r = rng.standard_t(3, 100_000) * 0.01
    assert var_es(r, 0.99, "cornish_fisher")[0] > var_es(r, 0.99, "parametric")[0]


def test_drawdown_statistics():
    dates = pd.bdate_range("2024-01-01", periods=8)
    nav = pd.Series([100, 110, 99, 88, 95, 110, 121, 120], index=dates, dtype=float)
    s = max_drawdown_stats(nav)
    assert s["max_drawdown"] == pytest.approx(88 / 110 - 1)
    assert s["peak"] == dates[1] and s["trough"] == dates[3] and s["recovery"] == dates[5]
    assert s["longest_underwater_days"] == 3
    never = max_drawdown_stats(pd.Series([100, 90, 95], index=dates[:3], dtype=float))
    assert never["recovery"] is None


def test_drawdown_controller_tiers_cooloff_and_rebase():
    c = DrawdownController(DrawdownCfg(soft_limit=0.10, soft_exposure=0.5, hard_limit=0.20, hard_exposure=0.0,
                                       cooloff_days=3), 100.0)
    assert c.update(105) is None and c.multiplier == 1.0
    assert c.update(94) == "soft_limit_breached" and c.multiplier == 0.5  # -10.5% from 105
    assert c.update(93) is None, "staying in the soft tier is not a new event"
    assert c.update(96) == "soft_limit_released" and c.multiplier == 1.0
    assert c.update(83) == "hard_limit_breached" and c.multiplier == 0.0  # -21%
    assert c.update(83) is None and c.update(83) is None
    assert c.update(83) == "rebase_after_cooloff" and c.multiplier == 1.0 and c.hwm == 83
    assert c.update(80) is None, "after re-basing, -3.6% is not a breach"


def test_var_backtest_statistics():
    ok = kupiec_pof(1000, 10, 0.99)
    assert ok["expected"] == pytest.approx(10) and ok["p_value"] > 0.9
    assert kupiec_pof(1000, 40, 0.99)["p_value"] < 0.001, "four times too many breaches must be rejected"
    clustered = np.zeros(1000, int)
    clustered[100:110] = 1
    spread = np.zeros(1000, int)
    spread[::100] = 1
    assert christoffersen_independence(clustered)["p_value"] < 0.001
    assert christoffersen_independence(spread)["p_value"] > 0.1


def test_concentration_and_liquidity_profile():
    c = concentration(np.array([0.25, 0.25, 0.25, 0.25, 0.0]))
    assert c["n"] == 4 and c["effective_n"] == pytest.approx(4.0) and c["max_weight"] == 0.25
    prof = liquidity_profile(np.array([100.0, 100.0]), np.array([1000.0, 100.0]), 0.1)
    # stock 1 sells 100 a day (done in 1 day); stock 2 sells 10 a day (10 days)
    assert prof["sellable_1d"] == pytest.approx(110 / 200) and prof["sellable_10d"] == pytest.approx(1.0)


def test_var_backtest_and_limit_monitor_on_the_fixture(ws, cfg, net):
    vb = stress.daily_var_backtest(net, ws.md, cfg)
    s = vb["summary"]
    assert s["days_tested"] > 600 and 0.005 < s["average_var"] < 0.08
    assert s["kupiec_observed"] == int(vb["series"]["breach"].sum())
    breaches = stress.limit_monitor(net, ws.md, cfg, vb["series"])
    assert set(breaches["limit"]) <= {"drawdown_soft", "drawdown_hard", "var", "expected_shortfall",
                                      "stock_weight", "sector_weight", "sellable_in_5d"}
    summary = stress.limit_summary(breaches, len(net.daily))
    assert (summary["share_of_days"] <= 1).all()


def test_stress_tables(ws, cfg, net):
    md = ws.md
    bench = benchmark_returns(ws, cfg, net)
    # a window inside the back-test
    class W:
        name, start, end = "inside", pd.Timestamp("2022-09-01"), pd.Timestamp("2022-11-30")
    win = stress.window_stress(net, md, bench, [W])
    r = net.returns.loc[W.start:W.end]
    assert win.loc["inside", "strategy_return"] == pytest.approx((1 + r).prod() - 1)
    assert win.loc["inside", "strategy_max_drawdown"] <= 0

    worst = stress.worst_periods(net.returns.iloc[1:], horizons=(1, 21), top=3)
    one_day = worst.loc[1]
    assert one_day["return"].iloc[0] == pytest.approx(net.returns.iloc[1:].min())
    assert one_day["return"].is_monotonic_increasing

    w_last = stress._weights_on(net, md, net.daily.index[-1])
    replay = stress.replay_stress(md, w_last, cfg.stress.windows[0].start, cfg.stress.windows[0].end,
                                  md.index_return("NIFTY 500"), "crash")
    assert -0.6 < replay["book_return"] < 0.2 and 0 <= replay["coverage_own_history"] <= 1
    assert replay["book_max_drawdown"] <= 0

    from momo.signals import market_return
    shocks = stress.market_shock_table(md, w_last, md.loc(net.daily.index[-1]), market_return(md), [-0.1, -0.2], 1e8)
    assert shocks.loc[-0.2, "estimated_return"] == pytest.approx(2 * shocks.loc[-0.1, "estimated_return"])
    assert shocks["estimated_return"].max() < 0


def test_liquidity_stress_gets_worse_with_haircuts(ws, cfg, net):
    from momo.costs import CostModel
    md = ws.md
    last = net.daily.index[-1]
    cm = CostModel(ws.schedule, cfg.costs, md.dates)
    table = stress.liquidity_stress(md, cfg, cm, stress._weights_on(net, md, last), md.loc(last), float(net.nav.iloc[-1]))
    base, hit = table.loc[(0.0, 1.0)], table.loc[(0.5, 3.0)]
    assert hit["days_to_sell_90pct"] == pytest.approx(2 * base["days_to_sell_90pct"])
    assert hit["liquidation_cost_pct_nav"] > base["liquidation_cost_pct_nav"]
    assert hit["sellable_5d"] <= base["sellable_5d"]


def test_drawdown_episodes_are_dated_correctly():
    dates = pd.bdate_range("2024-01-01", periods=12)
    level = pd.Series([100, 110, 99, 88, 95, 110, 121, 115, 96, 100, 90, 95], index=dates, dtype=float)
    ep = stress.drawdown_episodes(level, min_depth=0.15)
    assert len(ep) == 2
    first, second = ep.iloc[0], ep.iloc[1]
    assert (first["peak"], first["trough"], first["recovered"]) == (dates[1].date(), dates[3].date(), dates[5].date())
    assert first["depth"] == pytest.approx(88 / 110 - 1)
    assert second["peak"] == dates[6].date() and second["trough"] == dates[10].date() and second["recovered"] is None
    assert len(stress.drawdown_episodes(level, min_depth=0.30)) == 0
