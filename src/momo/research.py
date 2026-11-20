"""Out-of-sample discipline: parameter search on the training period, logged.

The project brief asks to "fix parameters on a training period and report the
untouched test period separately" and to "say how many parameter combinations
you tried". This module makes both mechanical, so the answer is a fact read
from a log rather than a recollection:

* ``grid_search`` runs parameter combinations **on the training period only**
  and appends every run to a registry (``results/research/trials.csv``).
  The number of combinations tried is the number of distinct parameter sets
  in that file.
* ``freeze`` records the chosen parameter set (its fingerprint, when it was
  frozen and how many trials preceded it).
* ``evaluate_test`` runs the test period and writes an access log
  (``test_access_log.csv``). It warns if the config is not the frozen one or
  if the test period has been looked at before.

Because the best of N trials looks good partly by luck, ``deflated_sharpe``
reports the probability that the chosen strategy's true Sharpe ratio is
positive after allowing for the number of trials (Bailey and Lopez de Prado
2014).

Choosing the grid and choosing the winner (and why) are the group's decisions.
The tools rank; they do not pick.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml
from scipy import stats

from .analytics import cost_stats, performance_summary, turnover_stats
from .backtest import BacktestResult, RunOptions
from .config import ConfigError, StrategyConfig

EULER_GAMMA = 0.5772156649015329
TRIAL_METRICS = ["cagr", "annual_volatility", "sharpe", "sortino", "max_drawdown", "calmar",
                 "one_way_turnover", "cost_pct_nav_per_year", "avg_positions", "sharpe_daily", "n_days"]


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
class TrialRegistry:
    """Append-only record of every back-test run during research."""

    def __init__(self, folder: str | Path = "results/research"):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.trials_path = self.folder / "trials.csv"
        self.access_path = self.folder / "test_access_log.csv"
        self.frozen_path = self.folder / "frozen.json"

    def trials(self) -> pd.DataFrame:
        return pd.read_csv(self.trials_path) if self.trials_path.exists() else pd.DataFrame()

    def n_parameter_sets(self) -> int:
        """Distinct parameter sets evaluated on the training period so far."""
        t = self.trials()
        return int(t["fingerprint"].nunique()) if len(t) else 0

    def record(self, row: Dict[str, Any]) -> None:
        frame = pd.DataFrame([row])
        frame.to_csv(self.trials_path, mode="a", header=not self.trials_path.exists(), index=False)

    def frozen(self) -> Optional[Dict[str, Any]]:
        return json.loads(self.frozen_path.read_text()) if self.frozen_path.exists() else None

    def test_accesses(self) -> pd.DataFrame:
        return pd.read_csv(self.access_path) if self.access_path.exists() else pd.DataFrame()


def _metrics(result: BacktestResult, rf_daily: Optional[pd.Series]) -> Dict[str, float]:
    s = performance_summary(result.nav, rf_daily)
    r = result.returns
    rf = rf_daily.reindex(r.index).fillna(0.0) if rf_daily is not None else 0.0
    ex = (r - rf).iloc[1:]
    return {
        "cagr": s["cagr"], "annual_volatility": s["annual_volatility"], "sharpe": s["sharpe"],
        "sortino": s["sortino"], "max_drawdown": s["max_drawdown"], "calmar": s["calmar"],
        "one_way_turnover": turnover_stats(result)["annual_one_way_turnover"],
        "cost_pct_nav_per_year": cost_stats(result).loc["TOTAL", "pct_of_avg_nav_per_year"],
        "avg_positions": float(result.daily["n_positions"].mean()),
        "sharpe_daily": float(ex.mean() / ex.std(ddof=1)) if ex.std(ddof=1) > 0 else np.nan,
        "n_days": int(len(ex)),
    }


# --------------------------------------------------------------------------- #
# Search on the training period
# --------------------------------------------------------------------------- #
def expand_grid(grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """{"a": [1, 2], "b": [3]} -> [{"a": 1, "b": 3}, {"a": 2, "b": 3}]"""
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def grid_search(ws, cfg: StrategyConfig, registry: TrialRegistry, grid: Optional[Dict[str, List[Any]]] = None,
                note: str = "", verbose: bool = True) -> pd.DataFrame:
    """Run every combination in ``grid`` on the TRAINING period and log each one.

    ``grid`` maps dotted config paths to lists of values, for example
    ``{"signal.components.0.lookback_months": [6, 9, 12], "portfolio.n_holdings": [20, 30]}``.
    Defaults to ``cfg.research.grid``. Returns one row per combination.
    """
    from .pipeline import run_backtest  # local import to avoid a cycle

    grid = cfg.research.grid if grid is None else grid
    combos = expand_grid(grid)
    sm = cfg.sample
    rf = pd.Series(ws.md.rf_daily, index=ws.md.dates)
    rows = []
    for i, overrides in enumerate(combos, start=1):
        trial = cfg.with_overrides(overrides)
        row: Dict[str, Any] = {
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "fingerprint": trial.fingerprint(), "period": "train",
            "start": sm.train_start.isoformat(), "end": sm.train_end.isoformat(),
            "data_kind": ws.md.data_kind, "note": note,
            "overrides": json.dumps(overrides, sort_keys=True, default=str),
        }
        try:
            trial.validate()
            res = run_backtest(ws, trial, sm.train_start, sm.train_end, RunOptions("net"), validate=False)
            row.update(_metrics(res, rf))
            row["error"] = ""
        except ConfigError as exc:  # an invalid combination is still a trial that was attempted
            row.update({m: np.nan for m in TRIAL_METRICS})
            row["error"] = str(exc).splitlines()[0][:200]
        registry.record(row)
        rows.append({**overrides, **{k: row[k] for k in TRIAL_METRICS}, "fingerprint": row["fingerprint"],
                     "error": row["error"]})
        if verbose:
            print(f"  [{i}/{len(combos)}] {overrides}  ->  Sharpe {row['sharpe']:.2f}, "
                  f"CAGR {row['cagr']:.1%}, max DD {row['max_drawdown']:.1%}" if not row["error"]
                  else f"  [{i}/{len(combos)}] {overrides}  ->  skipped: {row['error']}")
    if verbose:
        print(f"Parameter sets tried on the training period so far: {registry.n_parameter_sets()}")
    return pd.DataFrame(rows)


def record_training_run(registry: TrialRegistry, ws, cfg: StrategyConfig, result: BacktestResult,
                        note: str = "") -> int:
    """Log one training-period run (the `train` command) as a trial. Returns the distinct sets so far."""
    sm = cfg.sample
    rf = pd.Series(ws.md.rf_daily, index=ws.md.dates)
    row: Dict[str, Any] = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "fingerprint": cfg.fingerprint(), "period": "train",
        "start": sm.train_start.isoformat(), "end": sm.train_end.isoformat(),
        "data_kind": ws.md.data_kind, "note": note, "overrides": "{}",
    }
    row.update(_metrics(result, rf))
    row["error"] = ""
    registry.record(row)
    return registry.n_parameter_sets()


def sensitivity_table(trials: pd.DataFrame, row_param: str, col_param: str, metric: str = "sharpe") -> pd.DataFrame:
    """Average ``metric`` for each pair of values of two parameters (a robustness check).

    A good choice sits on a plateau - neighbours perform similarly - not on an
    isolated peak, which is usually luck.
    """
    return trials.pivot_table(index=row_param, columns=col_param, values=metric, aggfunc="mean")


# --------------------------------------------------------------------------- #
# Freeze, then open the lockbox
# --------------------------------------------------------------------------- #
def freeze(cfg: StrategyConfig, registry: TrialRegistry, out_yaml: str | Path = "config/frozen_strategy.yaml") -> Dict[str, Any]:
    """Record the final parameter set before the test period is touched."""
    cfg.validate()
    record = {
        "fingerprint": cfg.fingerprint(),
        "frozen_at": dt.datetime.now().isoformat(timespec="seconds"),
        "parameter_sets_tried_on_train": registry.n_parameter_sets(),
        "test_accesses_before_freeze": int(len(registry.test_accesses())),
        "config_file": str(out_yaml),
    }
    Path(out_yaml).parent.mkdir(parents=True, exist_ok=True)
    with open(out_yaml, "w", encoding="utf-8") as fh:
        fh.write(f"# FROZEN on {record['frozen_at']} - fingerprint {record['fingerprint']}\n"
                 f"# Parameter sets tried on the training period before freezing: "
                 f"{record['parameter_sets_tried_on_train']}\n# Do not edit: the test-period run checks this file.\n")
        yaml.safe_dump(cfg.to_dict(), fh, sort_keys=False)
    registry.frozen_path.write_text(json.dumps(record, indent=1))
    return record


def evaluate_test(ws, cfg: StrategyConfig, registry: TrialRegistry, options: Optional[RunOptions] = None,
                  verbose: bool = True) -> BacktestResult:
    """Run the TEST period and log that it was looked at.

    The run is allowed in any case - the tool cannot know your intentions - but
    the log is honest: it shows whether the parameters were frozen first and
    how many times the test period has been evaluated.
    """
    from .pipeline import run_backtest

    frozen = registry.frozen()
    earlier = registry.test_accesses()
    fp = cfg.fingerprint()
    matches = bool(frozen and frozen["fingerprint"] == fp)
    row = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"), "fingerprint": fp,
        "matches_frozen_config": matches, "parameter_sets_tried_on_train": registry.n_parameter_sets(),
        "earlier_test_accesses": int(len(earlier)), "data_kind": ws.md.data_kind,
        "label": (options.label if options else "net"),
    }
    pd.DataFrame([row]).to_csv(registry.access_path, mode="a", header=not registry.access_path.exists(), index=False)
    if verbose:
        if not frozen:
            print("WARNING: no frozen configuration on record. Freeze your parameters (research.freeze) "
                  "before looking at the test period.")
        elif not matches:
            print("WARNING: this configuration differs from the frozen one "
                  f"({fp} vs {frozen['fingerprint']}). The test period is no longer untouched.")
        distinct = set(earlier["fingerprint"]) - {fp} if len(earlier) else set()
        if distinct:
            print(f"WARNING: the test period was already evaluated with {len(distinct)} other parameter set(s). "
                  "Disclose this in the report.")
    return run_backtest(ws, cfg, cfg.sample.test_start, cfg.sample.test_end, options or RunOptions("net"))


# --------------------------------------------------------------------------- #
# Is the Sharpe ratio real?
# --------------------------------------------------------------------------- #
def probabilistic_sharpe(returns: pd.Series, benchmark_sharpe: float = 0.0) -> float:
    """P(true Sharpe > ``benchmark_sharpe``) given sample length, skewness and kurtosis.

    Both Sharpe ratios are per period (per day for daily returns), not annualised.
    """
    r = pd.Series(returns).dropna()
    n = len(r)
    if n < 30 or r.std(ddof=1) == 0:
        return np.nan
    sr = r.mean() / r.std(ddof=1)
    skew, kurt = stats.skew(r), stats.kurtosis(r, fisher=False)
    denom = np.sqrt(max(1e-12, 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2))
    return float(stats.norm.cdf((sr - benchmark_sharpe) * np.sqrt(n - 1) / denom))


def deflated_sharpe(returns: pd.Series, n_trials: int, trial_sharpes: Optional[np.ndarray] = None) -> Dict[str, float]:
    """Deflated Sharpe ratio (Bailey and Lopez de Prado 2014).

    With ``n_trials`` strategies tried, the best one is expected to show a
    positive Sharpe ratio even if none has any skill. That expected maximum is
    used as the hurdle in the probabilistic Sharpe ratio.

    ``returns``        excess returns of the chosen strategy (per period)
    ``trial_sharpes``  per-period Sharpe ratios of all the trials (their spread
                       sets the hurdle). If absent, the hurdle uses the sampling
                       variance of a single Sharpe ratio, 1/(n-1).
    """
    r = pd.Series(returns).dropna()
    n = len(r)
    if n < 30:
        return {"deflated_sharpe_probability": np.nan}
    sr = r.mean() / r.std(ddof=1)
    if trial_sharpes is not None and np.isfinite(trial_sharpes).sum() > 1:
        var_sr = float(np.nanvar(trial_sharpes, ddof=1))
    else:
        var_sr = 1.0 / (n - 1)
    k = max(int(n_trials), 1)
    if k > 1:
        hurdle = np.sqrt(var_sr) * ((1 - EULER_GAMMA) * stats.norm.ppf(1 - 1.0 / k)
                                    + EULER_GAMMA * stats.norm.ppf(1 - 1.0 / (k * np.e)))
    else:
        hurdle = 0.0
    return {
        "sharpe_per_period": float(sr), "sharpe_annualised": float(sr * np.sqrt(252)),
        "trials": k, "expected_max_sharpe_under_no_skill_annualised": float(hurdle * np.sqrt(252)),
        "probabilistic_sharpe_vs_zero": probabilistic_sharpe(r, 0.0),
        "deflated_sharpe_probability": probabilistic_sharpe(r, hurdle),
    }
