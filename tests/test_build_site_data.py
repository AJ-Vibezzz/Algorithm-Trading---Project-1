"""site/build_site_data.py: fills the site's results only from a frozen, logged test run."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from momo.config import StrategyConfig

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("build_site_data", ROOT / "site" / "build_site_data.py")
bsd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bsd)

BENCH = "NIFTY MIDSMALLCAP 400 TRI"


def _make_run(tmp: Path, start="2018-01-31", end="2021-12-31", data_kind="real") -> dict:
    """A minimal frozen config, research log and test pack with the real file names and layouts."""
    cfg = StrategyConfig.load(ROOT / "tests" / "fixtures" / "fixture_strategy.yaml")
    d = cfg.to_dict()
    d["benchmark"]["primary"] = BENCH
    frozen_yaml = tmp / "frozen_strategy.yaml"
    import yaml
    frozen_yaml.write_text(yaml.safe_dump(d, sort_keys=False))
    fp = StrategyConfig.load(frozen_yaml).fingerprint()

    res = tmp / "results"
    (res / "research").mkdir(parents=True)
    (res / "research" / "frozen.json").write_text(json.dumps({"fingerprint": fp, "frozen_at": "2026-11-01T10:00:00"}))
    pd.DataFrame([{"timestamp": "2026-11-01T11:00:00", "fingerprint": fp, "matches_frozen_config": True,
                   "parameter_sets_tried_on_train": 12, "earlier_test_accesses": 0, "data_kind": data_kind,
                   "label": "net"}]).to_csv(res / "research" / "test_access_log.csv", index=False)

    test = res / "test"
    (test / "tables").mkdir(parents=True)
    (test / "logs").mkdir()
    (test / "run_manifest.json").write_text(json.dumps({
        "label": "test", "written_at": "2026-11-01T11:05:00", "data_kind": data_kind,
        "config_fingerprint": fp, "config": d, "is_example_config": False}))

    days = pd.bdate_range(start, end)
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0005, 0.01, len(days)), index=days)
    r.iloc[0] = 0.0
    nav = 5e8 * (1 + r).cumprod()
    pd.DataFrame({"nav": nav, "cash": 0.0}).to_csv(test / "logs" / "daily.csv")
    pd.DataFrame({BENCH: r * 0.8}).to_csv(test / "logs" / "benchmarks.csv")

    perf = pd.DataFrame({"net": [0.12, 0.18, 0.55, -0.21], f"benchmark: {BENCH}": [0.10, 0.17, 0.48, -0.19]},
                        index=["cagr", "annual_volatility", "sharpe", "max_drawdown"])
    perf.to_csv(test / "tables" / "01_performance_summary.csv")
    pd.Series({"annual_one_way_turnover": 2.7}, name="value").to_frame().to_csv(test / "tables" / "07_turnover.csv")
    pd.DataFrame({"component": ["brokerage", "TOTAL"], "rupees": [1.0, 2.0],
                  "pct_of_avg_nav_per_year": [0.005, 0.0123]}).to_csv(test / "tables" / "08_costs_by_component.csv",
                                                                       index=False)
    pd.DataFrame({"window": ["Covid crash", "before the test"], "start": ["2020-01-14", "2013-01-01"],
                  "strategy_return": [-0.31, np.nan], f"{BENCH} return": [-0.36, np.nan]}
                 ).to_csv(test / "tables" / "18_stress_windows.csv", index=False)
    pd.Series({"trials": 12.0, "deflated_sharpe_probability": 0.71}, name="value").to_frame().to_csv(
        test / "tables" / "21_sharpe_significance.csv")

    data_js = tmp / "data.js"
    shutil.copy(ROOT / "site" / "data.js", data_js)
    return {"res": res, "frozen": frozen_yaml, "data_js": data_js, "fp": fp}


def _args(run, *extra):
    return ["--results", str(run["res"]), "--frozen", str(run["frozen"]), "--data-js", str(run["data_js"]), *extra]


def _results(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"results: (\{.*\})\n\s*// ---- end of results ----", text, re.S)
    assert m, "results block not found"
    return json.loads(m.group(1))


def test_site_data_js_has_null_results_and_markers():
    text = (ROOT / "site" / "data.js").read_text(encoding="utf-8")
    assert bsd.RESULTS_BEGIN in text and bsd.RESULTS_END in text
    assert re.search(r"results: null\n", text)


def test_fills_results_from_the_test_pack(tmp_path):
    run = _make_run(tmp_path)
    before = run["data_js"].read_text(encoding="utf-8")
    assert bsd.main(_args(run)) == 0
    after = run["data_js"].read_text(encoding="utf-8")
    r = _results(run["data_js"])

    assert r["period"] == "Jan 2018 – Dec 2021"
    assert r["benchmarkName"] == "Nifty Midsmallcap 400 TRI"
    assert r["metrics"]["strategy"] == {"cagr": 0.12, "vol": 0.18, "sharpe": 0.55, "maxDrawdown": -0.21,
                                        "turnover": 2.7, "costDrag": 0.0123}  # copied, not rounded
    assert r["metrics"]["benchmark"]["sharpe"] == 0.48
    assert r["stress"] == [{"name": "Covid crash", "strategy": -0.31, "benchmark": -0.36}]
    assert r["deflatedSharpe"] == 0.71
    s = r["series"]
    assert len(s["dates"]) == len(s["strategy"]) == len(s["benchmark"]) == 48
    assert s["strategy"][0] == 1.0 and s["benchmark"][0] == 1.0
    assert r["split2020"]["before"]["label"] == "Jan 2018 – Dec 2019"
    assert r["split2020"]["after"]["label"] == "Jan 2020 – Dec 2021"

    assert 'generated: "2026-11-01"' in after and "trials: 12 " in after
    # everything outside the results block, generated and trials is untouched
    strip = lambda t: re.sub(r"results: .*?\n\s*// ---- end", "", re.sub(r"generated: \S+|trials: \d+", "", t), flags=re.S)
    assert strip(before) == strip(after)
    # running it again gives the same file
    assert bsd.main(_args(run)) == 0
    assert run["data_js"].read_text(encoding="utf-8") == after


def test_no_split_when_the_test_does_not_cross_2020(tmp_path):
    run = _make_run(tmp_path, start="2021-01-29", end="2022-12-30")
    assert bsd.main(_args(run)) == 0
    assert _results(run["data_js"])["split2020"] is None


def test_refuses_before_the_test_run(tmp_path):
    run = _make_run(tmp_path)
    (run["res"] / "research" / "test_access_log.csv").unlink()
    original = run["data_js"].read_text(encoding="utf-8")
    assert bsd.main(_args(run)) == 1
    assert run["data_js"].read_text(encoding="utf-8") == original


def test_refuses_a_test_pack_made_with_other_parameters(tmp_path):
    run = _make_run(tmp_path)
    m = json.loads((run["res"] / "test" / "run_manifest.json").read_text())
    m["config_fingerprint"] = "000000000000"
    (run["res"] / "test" / "run_manifest.json").write_text(json.dumps(m))
    assert bsd.main(_args(run)) == 1


def test_refuses_a_frozen_file_edited_after_freezing(tmp_path):
    run = _make_run(tmp_path)
    run["frozen"].write_text(run["frozen"].read_text().replace("n_holdings: 20", "n_holdings: 25"))
    assert StrategyConfig.load(run["frozen"]).fingerprint() != run["fp"]
    assert bsd.main(_args(run)) == 1


def test_other_test_runs_must_be_acknowledged(tmp_path):
    run = _make_run(tmp_path)
    log = pd.read_csv(run["res"] / "research" / "test_access_log.csv")
    extra = log.assign(fingerprint="abcdefabcdef", matches_frozen_config=False)
    pd.concat([log, extra]).to_csv(run["res"] / "research" / "test_access_log.csv", index=False)
    assert bsd.main(_args(run)) == 1
    assert bsd.main(_args(run, "--accept-other-test-runs")) == 0


def test_refuses_synthetic_output_unless_testing(tmp_path):
    run = _make_run(tmp_path, data_kind="synthetic")
    assert bsd.main(_args(run)) == 1
    assert bsd.main(_args(run, "--allow-synthetic", "--out", str(ROOT / "site" / "data.js"))) == 1
    assert bsd.main(_args(run, "--allow-synthetic", "--out", str(tmp_path / "scratch.js"))) == 0
