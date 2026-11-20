"""Fill the website's ``results`` from the ONE-TIME test run.  Run from the repository root:

    python site/build_site_data.py

What it reads (all written by ``python run.py freeze`` and ``python run.py test``):

    config/frozen_strategy.yaml                  the frozen parameters
    results/research/frozen.json                 fingerprint and time of the freeze
    results/research/test_access_log.csv         every time the test period was run
    results/test/run_manifest.json               what the test run used
    results/test/tables/01_performance_summary.csv    CAGR, volatility, Sharpe, worst fall
    results/test/tables/07_turnover.csv          annual one-way turnover
    results/test/tables/08_costs_by_component.csv     cost drag (TOTAL, % of average NAV a year)
    results/test/tables/18_stress_windows.csv    fund and benchmark return in each stress window
    results/test/tables/21_sharpe_significance.csv    deflated Sharpe ratio and the trial count
    results/test/logs/daily.csv                  daily NAV (for the growth-of-Rs-1 line)
    results/test/logs/benchmarks.csv             daily benchmark returns on the same dates

What it writes: ``site/data.js`` with ``results``, ``meta.generated`` and
``config.trials`` filled in. Every other key and comment is left as it is.

What it refuses to do:
- run before the test period has been run with the frozen parameters;
- use a test run whose parameters differ from the frozen ones;
- carry on silently if the test period was also run with other parameters
  (the page says the figures come from a single frozen run);
- publish figures made from the synthetic test fixture.

It copies numbers; it never rounds, re-runs or adjusts them. The only figures it
calculates are the month-end points of the growth line and the optional
before/after-2020 split, both from the daily files of the same run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULTS_BEGIN = "// ---- results: written by site/build_site_data.py; do not edit by hand ----"
RESULTS_END = "// ---- end of results ----"
SPLIT_DATE = pd.Timestamp("2020-01-01")  # the retail-boom prediction: momentum weaker after 2020


class Refusal(Exception):
    """The test-period output is missing or cannot be trusted: nothing is written."""


# --------------------------------------------------------------------------- #
# Reading the test-run output
# --------------------------------------------------------------------------- #
def _need(path: Path, what: str) -> Path:
    if not path.exists():
        raise Refusal(f"{path} not found ({what}).")
    return path


def _num(x: Any) -> Optional[float]:
    """A float from a CSV cell, or None for blanks and NaN (JSON has no NaN)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _value_table(path: Path) -> Dict[str, Any]:
    """Two-column tables written from a pandas Series: name -> value."""
    t = pd.read_csv(path, index_col=0)
    return t.iloc[:, 0].to_dict()


def check_test_run(results: Path, frozen_yaml: Path, allow_synthetic: bool,
                   accept_other_test_runs: bool) -> Dict[str, Any]:
    """Every check that must pass before any figure is copied. Returns the run's manifest."""
    from momo.config import StrategyConfig

    _need(frozen_yaml, "run `python run.py freeze` first")
    frozen = json.loads(_need(results / "research" / "frozen.json", "run `python run.py freeze` first").read_text())
    fp = frozen["fingerprint"]
    if StrategyConfig.load(frozen_yaml).fingerprint() != fp:
        raise Refusal(f"{frozen_yaml} no longer matches the frozen fingerprint {fp}: it was edited after freezing.")

    log_path = _need(results / "research" / "test_access_log.csv",
                     "the test period has not been run yet: `python run.py test --strategy config/frozen_strategy.yaml`")
    log = pd.read_csv(log_path)
    if log.empty:
        raise Refusal("the test period has not been run yet (the test access log is empty).")
    mine = log[log["fingerprint"] == fp]
    if mine.empty or not mine["matches_frozen_config"].astype(str).str.lower().eq("true").any():
        raise Refusal(f"the test period has not been run with the frozen parameters ({fp}).")
    others = sorted(set(log["fingerprint"]) - {fp})
    if others and not accept_other_test_runs:
        raise Refusal(
            f"the test period was also run with {len(others)} other parameter set(s): {', '.join(others)}. "
            "The site says its figures come from a single frozen run, so change that wording in site/app.js "
            "and disclose the other runs, then re-run this script with --accept-other-test-runs.")

    manifest = json.loads(_need(results / "test" / "run_manifest.json", "the test report pack").read_text())
    if manifest.get("label") != "test":
        raise Refusal(f"{results / 'test'} is not a test-period report pack (label {manifest.get('label')!r}).")
    if manifest.get("config_fingerprint") != fp:
        raise Refusal(f"results/test was made with parameters {manifest.get('config_fingerprint')}, "
                      f"not the frozen ones ({fp}). Run the test with config/frozen_strategy.yaml.")
    if manifest.get("is_example_config"):
        raise Refusal("results/test was made with an example configuration, not the group's strategy.")
    if str(manifest.get("written_at", "")) < str(frozen.get("frozen_at", "")):
        raise Refusal("results/test was written before the parameters were frozen.")
    if manifest.get("data_kind") != "real" and not allow_synthetic:
        raise Refusal(f"results/test was made from {manifest.get('data_kind')!r} data, not the real NSE data.")
    return manifest


def _cagr(start_value: float, end_value: float, start: pd.Timestamp, end: pd.Timestamp) -> Optional[float]:
    """Same convention as analytics.performance_summary: calendar days / 365.25."""
    years = (end - start).days / 365.25
    if years <= 0 or start_value <= 0:
        return None
    return _num((end_value / start_value) ** (1.0 / years) - 1.0)


def _month_label(day: pd.Timestamp) -> str:
    return day.strftime("%b %Y")


def _split_2020(strategy: pd.Series, benchmark: pd.Series) -> Optional[Dict[str, Any]]:
    """Yearly return before and after 1 Jan 2020, from the same daily growth series."""
    before = strategy.index[strategy.index < SPLIT_DATE]
    after = strategy.index[strategy.index >= SPLIT_DATE]
    if len(before) < 2 or len(after) < 2:
        return None
    first, cut, last = strategy.index[0], before[-1], strategy.index[-1]

    def part(a: pd.Timestamp, b: pd.Timestamp, label: str) -> Dict[str, Any]:
        return {"label": label,
                "strategy": _cagr(strategy[a], strategy[b], a, b),
                "benchmark": _cagr(benchmark[a], benchmark[b], a, b)}

    return {"before": part(first, cut, f"{_month_label(first)} – {_month_label(cut)}"),
            "after": part(cut, last, f"{_month_label(after[0])} – {_month_label(last)}")}


def _pretty_index_name(name: str) -> str:
    """'NIFTY MIDSMALLCAP 400 TRI' -> 'Nifty Midsmallcap 400 TRI'."""
    keep_upper = {"TRI", "PR", "NSE", "BSE"}
    return " ".join(w if (w in keep_upper or w[:1].isdigit()) else w.capitalize() for w in name.split())


def build_results(results: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """The ``results`` object in the shape documented in site/data.js, plus the trial count."""
    tdir, ldir = results / "test" / "tables", results / "test" / "logs"
    cfg = manifest["config"]
    primary = str(cfg["benchmark"]["primary"]).strip().upper()

    perf = pd.read_csv(_need(tdir / "01_performance_summary.csv", "test report pack"), index_col=0)
    bcol = f"benchmark: {primary}"
    if "net" not in perf.columns or bcol not in perf.columns:
        raise Refusal(f"01_performance_summary.csv has no 'net' or '{bcol}' column.")
    turnover = _value_table(_need(tdir / "07_turnover.csv", "test report pack"))
    costs = pd.read_csv(_need(tdir / "08_costs_by_component.csv", "test report pack"), index_col=0)
    sig = _value_table(_need(tdir / "21_sharpe_significance.csv", "test report pack"))

    def metrics(col: str) -> Dict[str, Optional[float]]:
        return {"cagr": _num(perf.at["cagr", col]), "vol": _num(perf.at["annual_volatility", col]),
                "sharpe": _num(perf.at["sharpe", col]), "maxDrawdown": _num(perf.at["max_drawdown", col])}

    strat = metrics("net")
    strat["turnover"] = _num(turnover.get("annual_one_way_turnover"))
    strat["costDrag"] = _num(costs.at["TOTAL", "pct_of_avg_nav_per_year"]) if "TOTAL" in costs.index else None

    stress: List[Dict[str, Any]] = []
    spath = tdir / "18_stress_windows.csv"
    if spath.exists():
        sw = pd.read_csv(spath)
        bret = f"{primary} return"
        for _, row in sw.iterrows():
            s = _num(row.get("strategy_return"))
            if s is None:  # window outside the test period
                continue
            stress.append({"name": str(row["window"]), "strategy": s,
                           "benchmark": _num(row.get(bret)) if bret in sw.columns else None})

    daily = pd.read_csv(_need(ldir / "daily.csv", "test report pack"), index_col=0, parse_dates=True)
    bench = pd.read_csv(_need(ldir / "benchmarks.csv", "test report pack (made by the current code)"),
                        index_col=0, parse_dates=True)
    if primary not in bench.columns:
        raise Refusal(f"logs/benchmarks.csv has no column {primary!r}.")
    nav = daily["nav"].astype(float)
    growth = nav / nav.iloc[0]
    bgrowth = (1.0 + bench[primary].reindex(nav.index).fillna(0.0)).cumprod()  # as in the report pack
    # month-end points, plus the first day so both lines start at Rs 1
    month_ends = growth.groupby(growth.index.to_period("M")).tail(1).index
    points = growth.index[:1].union(month_ends)

    trials = _num(sig.get("trials"))
    out = {
        "period": f"{_month_label(nav.index[0])} – {_month_label(nav.index[-1])}",
        "benchmarkName": _pretty_index_name(primary),
        "series": {"dates": [d.strftime("%Y-%m-%d") for d in points],
                   "strategy": [_num(v) for v in growth[points]],
                   "benchmark": [_num(v) for v in bgrowth[points]]},
        "metrics": {"strategy": strat, "benchmark": metrics(bcol)},
        "stress": stress,
        "deflatedSharpe": _num(sig.get("deflated_sharpe_probability")),
        "split2020": _split_2020(growth, bgrowth),
    }
    return {"results": out, "trials": int(trials) if trials is not None else None}


# --------------------------------------------------------------------------- #
# Writing data.js
# --------------------------------------------------------------------------- #
def _replace_once(text: str, pattern: str, repl: str, what: str) -> str:
    new, n = re.subn(pattern, lambda _m: repl, text, count=0, flags=re.S)
    if n != 1:
        raise Refusal(f"could not find exactly one {what} in data.js (found {n}); was its layout changed?")
    return new


def write_data_js(src: Path, dst: Path, results: Dict[str, Any], trials: Optional[int], generated: str) -> None:
    text = src.read_text(encoding="utf-8")
    body = json.dumps(results, indent=2, ensure_ascii=False).replace("\n", "\n  ")
    text = _replace_once(text, re.escape(RESULTS_BEGIN) + r".*?" + re.escape(RESULTS_END),
                         f"{RESULTS_BEGIN}\n  results: {body}\n  {RESULTS_END}", "results block")
    text = _replace_once(text, r'generated: (?:null|"[^"\n]*")', f"generated: {json.dumps(generated)}", "meta.generated")
    if trials is not None:
        text = _replace_once(text, r"trials: \d+", f"trials: {trials}", "config.trials")
    dst.write_text(text, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results", default=str(ROOT / "results"), help="the --out folder of the runs (default: results)")
    p.add_argument("--frozen", default=str(ROOT / "config" / "frozen_strategy.yaml"))
    p.add_argument("--data-js", default=str(ROOT / "site" / "data.js"), help="the file to read")
    p.add_argument("--out", help="the file to write (default: the same as --data-js)")
    p.add_argument("--accept-other-test-runs", action="store_true",
                   help="go ahead although the test period was also run with other parameters (disclose them)")
    p.add_argument("--allow-synthetic", action="store_true",
                   help="for testing this script only: accept fixture output; needs --out away from site/data.js")
    a = p.parse_args(argv)
    src = Path(a.data_js)
    dst = Path(a.out) if a.out else src
    try:
        if a.allow_synthetic and dst.resolve() == (ROOT / "site" / "data.js").resolve():
            raise Refusal("--allow-synthetic writes only to a scratch file: give --out somewhere other than site/data.js.")
        manifest = check_test_run(Path(a.results), Path(a.frozen), a.allow_synthetic, a.accept_other_test_runs)
        built = build_results(Path(a.results), manifest)
        generated = str(manifest.get("written_at", ""))[:10] or dt.date.today().isoformat()
        write_data_js(src, dst, built["results"], built["trials"], generated)
    except Refusal as exc:
        print(f"Not written: {exc}", file=sys.stderr)
        return 1
    r = built["results"]
    print(f"Wrote {dst}: test period {r['period']}, {len(r['series']['dates'])} monthly points, "
          f"{len(r['stress'])} stress window(s), trials = {built['trials']}, generated = {generated}.")
    print("Open the site and check the Results slide against results/test/tables before publishing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
