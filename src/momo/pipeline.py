"""Glue: load everything once (``Workspace``), then run back-tests against it.

Typical use::

    ws  = Workspace.load("config/data.yaml", "config/statutory_charges.yaml")
    cfg = StrategyConfig.load("config/strategy.yaml")
    res = run_backtest(ws, cfg, cfg.sample.train_start, cfg.sample.train_end)

The workspace caches universe snapshots, so a parameter search that changes
only the signal or portfolio settings does not rebuild the universe each time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .backtest import Backtester, BacktestResult, RunOptions
from .config import ConfigError, DataConfig, StrategyConfig
from .costs import StatutorySchedule
from .data.external import (external_inventory, load_delistings, load_factors, load_index_constituents,
                            load_surveillance)
from .market import MarketData
from .signals import warmup_days
from .strategy import MomentumStrategy, decision_rows
from .universe import Universe, build_universe, universe_benchmark_returns


@dataclass
class Workspace:
    """Everything that does not depend on the strategy parameters."""

    data_cfg: DataConfig
    md: MarketData
    schedule: StatutorySchedule
    surveillance: pd.DataFrame
    constituents: pd.DataFrame
    delisting_returns: Dict[str, float]
    factors: Dict[str, pd.DataFrame]
    _universe_cache: Dict[str, Universe] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, data_yaml: str | Path = "config/data.yaml",
             statutory_yaml: str | Path = "config/statutory_charges.yaml",
             data_cfg: Optional[DataConfig] = None, verbose: bool = True) -> "Workspace":
        data_cfg = data_cfg or DataConfig.load(data_yaml)
        md = MarketData.load(data_cfg)
        schedule = StatutorySchedule.load(statutory_yaml)
        dl = load_delistings(data_cfg)
        ws = cls(
            data_cfg=data_cfg, md=md, schedule=schedule,
            surveillance=load_surveillance(data_cfg),
            constituents=load_index_constituents(data_cfg),
            delisting_returns=dict(zip(dl["symbol"], dl["terminal_return"])) if len(dl) else {},
            factors=load_factors(data_cfg, verbose=False),
        )
        if verbose:
            print(md.describe())
            if md.data_kind == "synthetic":
                print("  *** SYNTHETIC TEST FIXTURE - outputs are not back-test results ***")
            if md.rf_missing:
                print("  WARNING: no risk-free rate supplied (data/external/risk_free.csv) - zero is assumed.")
            missing = external_inventory(data_cfg)
            missing = missing[(~missing["present"]) & (missing["status"] != "optional")]
            for row in missing.itertuples():
                if row.file != "risk_free.csv":
                    print(f"  note: data/external/{row.file} not supplied - {row.if_absent}")
            unverified = schedule.unverified()
            if unverified:
                print(f"  note: {len(unverified)} statutory-charge entries are not yet verified against a source "
                      "(config/statutory_charges.yaml, checked: no).")
        return ws

    def universe(self, cfg: StrategyConfig, rows: List[int]) -> Universe:
        """Universe snapshots for these decision rows (cached by universe settings)."""
        key = json.dumps(cfg.to_dict()["universe"], sort_keys=True, default=str)
        uni = self._universe_cache.setdefault(key, Universe())
        todo = [t for t in rows if t not in uni.snapshots]
        if todo:
            fresh = build_universe(self.md, cfg.universe, todo, self.surveillance, self.constituents)
            uni.snapshots.update(fresh.snapshots)
        return uni


def check_history(md: MarketData, cfg: StrategyConfig, rows: List[int]) -> None:
    """Fail early, with a useful message, if the data starts too late for the signal."""
    if not rows:
        raise ConfigError("No rebalance dates fall inside the requested period - check the sample dates "
                          f"against the data ({md.dates[0].date()} to {md.dates[-1].date()}).")
    need = warmup_days(cfg.signal)
    if rows[0] < need:
        earliest = md.dates[min(need, md.T - 1)].date()
        raise ConfigError(
            f"The first decision date is {md.dates[rows[0]].date()}, but the signal needs {need} trading days "
            f"of history and the data starts {md.dates[0].date()}. Start the sample on or after {earliest}, "
            "or download earlier data."
        )


def run_backtest(ws: Workspace, cfg: StrategyConfig, start, end,
                 options: Optional[RunOptions] = None, validate: bool = True) -> BacktestResult:
    """Run one back-test over [start, end]. Decisions are taken on period-ends inside the window."""
    if validate:
        cfg.validate()
    md = ws.md
    rows = decision_rows(md, cfg, start, end)
    check_history(md, cfg, rows)
    universe = ws.universe(cfg, rows)
    strategy = MomentumStrategy(md, cfg, universe)
    engine = Backtester(md, cfg, strategy, ws.schedule, rows, md.loc(end), options, ws.delisting_returns)
    result = engine.run()
    result.meta["universe_table"] = universe.table().loc[lambda d: d.index.isin(md.dates[rows])]
    result.meta["universe_returns"] = universe_benchmark_returns(md, universe, rows)
    result.meta["rows"] = rows
    return result


def run_variants(ws: Workspace, cfg: StrategyConfig, start, end) -> Dict[str, BacktestResult]:
    """The same strategy three ways, to separate the effect of each layer of realism.

    ``paper``  no costs, no liquidity caps, no circuit blocks  (what a naive back-test reports)
    ``gross``  liquidity caps and circuit blocks, but no costs
    ``net``    everything - this is the result to report
    """
    return {
        "paper": run_backtest(ws, cfg, start, end, RunOptions("paper", costs=False, frictions=False)),
        "gross": run_backtest(ws, cfg, start, end, RunOptions("gross", costs=False, frictions=True)),
        "net": run_backtest(ws, cfg, start, end, RunOptions("net", costs=True, frictions=True)),
    }


def benchmark_returns(ws: Workspace, cfg: StrategyConfig, result: BacktestResult) -> pd.DataFrame:
    """Daily returns of the benchmarks named in the config, aligned to the result's dates.

    ``UNIVERSE_CAP`` and ``UNIVERSE_EQUAL`` are the eligible universe itself
    (built in ``universe.py``); any other name is looked up among the index series.
    """
    md = ws.md
    names = [cfg.benchmark.primary] + list(cfg.benchmark.others)
    out = {}
    uni_ret = result.meta["universe_returns"]
    for name in names:
        key = name.strip().upper()
        if key in ("UNIVERSE_CAP", "UNIVERSE_EQUAL"):
            out[key] = uni_ret[key]
        else:
            out[key] = pd.Series(md.index_return(key), index=md.dates)
    frame = pd.DataFrame(out).reindex(result.daily.index)
    frame.iloc[0] = 0.0  # benchmarks start with the strategy's first NAV point
    return frame
