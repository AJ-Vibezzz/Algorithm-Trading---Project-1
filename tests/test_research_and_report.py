"""Train/test discipline (trial log, freeze, lockbox) and the report pack."""

import numpy as np
import pandas as pd
import pytest

from momo.analytics import (attribution, blocked_signal_summary, cost_stats, performance_summary, pnl_bridge,
                            trade_stats, turnover_stats)
from momo.factors import build_factor_proxies, factor_regression
from momo.pipeline import run_variants
from momo.report import build_report_pack
from momo.research import (TrialRegistry, deflated_sharpe, evaluate_test, expand_grid, freeze, grid_search,
                           probabilistic_sharpe)


def test_expand_grid():
    assert expand_grid({}) == [{}]
    assert len(expand_grid({"a": [1, 2, 3], "b": [4, 5]})) == 6


def test_every_trial_is_logged_and_counted(ws, cfg, tmp_path):
    reg = TrialRegistry(tmp_path / "research")
    grid = {"portfolio.n_holdings": [15, 25], "signal.components.0.lookback_months": [6, 12]}
    table = grid_search(ws, cfg, reg, grid, verbose=False)
    assert len(table) == 4 and reg.n_parameter_sets() == 4
    grid_search(ws, cfg, reg, {"portfolio.n_holdings": [15, 35]}, verbose=False)
    assert reg.n_parameter_sets() == 5, "a repeated parameter set is not a new one; a new value is"
    trials = reg.trials()
    assert len(trials) == 6 and (trials["period"] == "train").all()
    assert trials["end"].max() <= cfg.sample.train_end.isoformat(), "the search must never touch the test period"


def test_invalid_combination_is_recorded_not_hidden(ws, cfg, tmp_path):
    reg = TrialRegistry(tmp_path / "research")
    table = grid_search(ws, cfg, reg, {"signal.components.0.skip_months": [1, 12]}, verbose=False)
    assert table["error"].iloc[1] != "" and np.isnan(table["sharpe"].iloc[1])
    assert reg.n_parameter_sets() == 2


def test_lockbox_logs_test_period_access(ws, cfg, tmp_path, capsys):
    reg = TrialRegistry(tmp_path / "research")
    grid_search(ws, cfg, reg, {"portfolio.n_holdings": [15, 20]}, verbose=False)

    evaluate_test(ws, cfg, reg)  # before freezing
    assert "no frozen configuration" in capsys.readouterr().out
    record = freeze(cfg, reg, tmp_path / "frozen.yaml")
    assert record["parameter_sets_tried_on_train"] == 2 and record["test_accesses_before_freeze"] == 1

    res = evaluate_test(ws, cfg, reg)
    assert "WARNING" not in capsys.readouterr().out
    assert res.daily.index[0] >= pd.Timestamp(cfg.sample.test_start)

    other = cfg.with_overrides({"portfolio.n_holdings": 15})
    evaluate_test(ws, other, reg)
    out = capsys.readouterr().out
    assert "differs from the frozen one" in out and "already evaluated" in out
    log = reg.test_accesses()
    assert log["matches_frozen_config"].tolist() == [False, True, False]
    assert log["earlier_test_accesses"].tolist() == [0, 1, 2]

    from momo.config import StrategyConfig
    assert StrategyConfig.load(tmp_path / "frozen.yaml").fingerprint() == cfg.fingerprint()


def test_deflated_sharpe_falls_as_trials_rise():
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0006, 0.01, 1500))
    one = deflated_sharpe(r, 1)
    many = deflated_sharpe(r, 200)
    assert one["deflated_sharpe_probability"] == pytest.approx(probabilistic_sharpe(r, 0.0))
    assert many["deflated_sharpe_probability"] < one["deflated_sharpe_probability"]
    assert many["expected_max_sharpe_under_no_skill_annualised"] > 0
    assert probabilistic_sharpe(pd.Series(rng.normal(0.0, 0.01, 1500))) < 0.95


def test_performance_summary_on_a_known_series():
    dates = pd.bdate_range("2023-01-02", periods=253)
    nav = pd.Series(100.0 * 1.0004 ** np.arange(253), index=dates)
    s = performance_summary(nav)
    assert s["total_return"] == pytest.approx(1.0004 ** 252 - 1)
    assert s["max_drawdown"] == 0.0 and s["hit_rate_days"] == 1.0
    assert s["annual_volatility"] == pytest.approx(0.0, abs=1e-9)


def test_post_trade_tables_are_consistent(ws, net):
    md = ws.md
    t = turnover_stats(net)
    assert 0.5 < t["annual_one_way_turnover"] < 12 and 0 < t["average_exposure"] <= 1
    c = cost_stats(net)
    assert c.loc["TOTAL", "rupees"] == pytest.approx(net.daily["cost_total"].sum())
    assert c.drop(index="TOTAL")["share_of_total"].sum() == pytest.approx(1.0)
    assert c.loc["stt", "bps_of_traded_value"] == pytest.approx(10.0, rel=1e-6), "STT is 0.1% of every delivery trade"

    for by in ("sector", "stock", "year"):
        a = attribution(net, md, by)
        assert a["pnl_net"].sum() == pytest.approx((net.pnl["pnl_gross"] - net.pnl["cost"]).sum())
    bridge = pnl_bridge(net)
    assert bridge.drop("change in NAV").sum() == pytest.approx(bridge["change in NAV"], abs=1.0)

    ts = trade_stats(net, md)
    assert 0 < ts["summary"]["hit_rate"] < 1 and ts["summary"]["completed_holdings"] > 50
    assert ts["holdings"]["pnl_net"].sum() == pytest.approx((net.pnl["pnl_gross"] - net.pnl["cost"]).sum())

    blocked = blocked_signal_summary(net)
    assert blocked["execution"]["order_days"].sum() == len(net.blocks)
    assert int(blocked["speed"].loc["orders", "value"]) == len(net.orders)


def test_factor_proxies_and_regression(ws, net):
    f = build_factor_proxies(ws.md)
    assert {"mkt_rf", "smb", "wml", "rf"} <= set(f.columns)
    assert f["wml"].notna().sum() > 500 and abs(f["smb"].mean()) < 0.01
    reg = factor_regression(net.returns.iloc[1:], f, "M")
    assert 0.5 < reg["table"].loc["mkt_rf", "coefficient"] < 1.6, "a long-only equity book has a market beta near one"
    assert 0 < reg["r_squared"] <= 1 and reg["n"] >= 30
    total = sum(v for k, v in reg["annual_contribution"].items() if k != "total_excess_return")
    assert total == pytest.approx(reg["annual_contribution"]["total_excess_return"], abs=1e-9)


def test_report_pack_writes_every_exhibit_and_marks_it_synthetic(ws, cfg, tmp_path):
    variants = run_variants(ws, cfg, cfg.sample.train_start, cfg.sample.train_end)
    pack = build_report_pack(ws, cfg, variants, tmp_path / "pack", "train", verbose=False)
    tables = {p.stem for p in (tmp_path / "pack" / "tables").glob("*.csv")}
    figures = {p.stem for p in (tmp_path / "pack" / "figures").glob("*.png")}
    for must in ["01_performance_summary", "03_calendar_years", "05_regimes", "06_paper_gross_net",
                 "08_costs_by_component", "10_blocked_screening", "10_blocked_execution", "11_attribution_by_sector",
                 "12_factor_regression_daily", "13_var_backtest_summary", "14_limit_breach_summary",
                 "18_stress_windows", "19_liquidity_stress", "21_sharpe_significance"]:
        assert must in tables, f"missing table {must}"
    assert len(figures) >= 14
    assert (tmp_path / "pack" / "SYNTHETIC_NOT_A_RESULT.txt").exists()
    assert pack["manifest"]["data_kind"] == "synthetic" and pack["manifest"]["config_fingerprint"] == cfg.fingerprint()
    summary = pack["01_performance_summary"]
    assert {"net", "gross", "paper"} <= set(summary.columns)


def test_a_training_run_counts_as_a_trial_and_a_rerun_does_not_double_count(ws, cfg, net, tmp_path):
    from momo.research import TrialRegistry, record_training_run
    reg = TrialRegistry(tmp_path / "research")
    assert record_training_run(reg, ws, cfg, net, note="first look") == 1
    assert record_training_run(reg, ws, cfg, net, note="same settings again") == 1
    changed = cfg.with_overrides({"portfolio.n_holdings": 25})
    assert record_training_run(reg, ws, changed, net) == 2
    assert len(reg.trials()) == 3 and set(reg.trials()["period"]) == {"train"}
