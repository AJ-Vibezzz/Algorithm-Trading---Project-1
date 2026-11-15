"""Command-line interface. Run ``python run.py <command> --help`` from the repository root.

The commands follow the order of the project::

    fixture    make the synthetic test fixture (to try the code without real data)
    download   fetch the public NSE files                     -> data/raw
    build      parse them and build the panel + quality report -> data/processed
    check      is the strategy config complete? what data is missing?
    train      back-test the TRAINING period                  -> results/train
    search     try the parameter grid on the TRAINING period  -> results/research
    freeze     lock the chosen parameters                     -> config/frozen_strategy.yaml
    test       back-test the untouched TEST period            -> results/test
    full       training + test in one run (stress windows)    -> results/full
    liquidity  how liquid each size segment is, year by year (to choose filters and fund size)
    episodes   list the large falls of an index, with dates (to choose stress windows)

Every command prints what it wrote. None of them writes commentary: the
interpretation of the exhibits is the group's work.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import List, Optional

import pandas as pd
import yaml

from .config import ConfigError, DataConfig, StrategyConfig

DEFAULT_DATA = "config/data.yaml"
DEFAULT_STRATEGY = "config/strategy.yaml"
DEFAULT_STATUTORY = "config/statutory_charges.yaml"
FIXTURE_ROOT = "data/_synthetic_fixture"


def _banner(text: str) -> None:
    print("\n" + "=" * 78 + f"\n{text}\n" + "=" * 78)


def _workspace(args):
    from .pipeline import Workspace

    return Workspace.load(args.data_config, args.statutory)


def _strategy(args, validate: bool = True) -> StrategyConfig:
    cfg = StrategyConfig.load(args.strategy)
    if validate:
        cfg.validate()
    if cfg.meta.is_example:
        print("NOTE: this is a shipped EXAMPLE configuration - its outputs are stamped and are not your strategy.")
    return cfg


def _registry(args):
    from .research import TrialRegistry

    return TrialRegistry(Path(args.out) / "research")


# --------------------------------------------------------------------------- #
# Data commands
# --------------------------------------------------------------------------- #
def cmd_fixture(args) -> None:
    from .data.ingest import ingest
    from .data.panel import build_panel
    from .data.quality import data_quality_report
    from .data.synthetic import FixtureSpec, generate_fixture

    root = Path(args.root)
    _banner("SYNTHETIC TEST FIXTURE - invented data, for testing the code only")
    generate_fixture(root, FixtureSpec(n_companies=args.companies, seed=args.seed))
    data_yaml = root / "data.yaml"
    data_yaml.write_text(yaml.safe_dump({"root": str(root), "data_kind": "synthetic"}))
    cfg = DataConfig.load(data_yaml)
    ingest(cfg, progress=not args.quiet)
    build_panel(cfg)
    data_quality_report(cfg, verbose=False)
    print(f"\nFixture ready under {root}. Try the pipeline on it with:\n"
          f"  python run.py train --data-config {data_yaml} --strategy tests/fixtures/fixture_strategy.yaml "
          f"--out results/_fixture\n"
          "Anything it produces is stamped SYNTHETIC and is not a result.")


def cmd_download(args) -> None:
    from .data.download import Downloader, summarise_manifest

    cfg = DataConfig.load(args.data_config)
    start = dt.date.fromisoformat(args.start) if args.start else cfg.download_start
    end = dt.date.fromisoformat(args.end) if args.end else (cfg.download_end or dt.date.today())
    if start is None:
        sys.exit("Set download_start in config/data.yaml (or pass --start YYYY-MM-DD). It must be early enough "
                 "to give the signal its look-back before your training period starts.")
    _banner(f"Downloading NSE files {start} to {end} into {cfg.raw}")
    manifest = Downloader(cfg).run(start, end, args.datasets, retry_missing=args.retry_missing,
                                   progress=not args.quiet)
    print(summarise_manifest(manifest).to_string())
    print("\n'missing' = no file for that date (weekend / holiday). 'blocked' or 'error' = run again later, "
          "or from another network.")


def cmd_build(args) -> None:
    from .data.ingest import ingest
    from .data.panel import build_panel
    from .data.quality import data_quality_report

    cfg = DataConfig.load(args.data_config)
    _banner("Parsing raw files")
    counts = ingest(cfg, force=args.force, progress=not args.quiet)
    print(counts)
    if counts.get("parse_errors"):
        print(f"  {counts['parse_errors']} file(s) could not be parsed - see {cfg.interim / '_parse_errors.csv'}")
    _banner("Building the panel")
    build_panel(cfg)
    _banner("Data-quality report")
    data_quality_report(cfg)


def cmd_check(args) -> None:
    from .costs import StatutorySchedule
    from .data.external import external_inventory

    _banner("Strategy configuration")
    try:
        cfg = StrategyConfig.load(args.strategy)
    except ConfigError as exc:
        sys.exit(str(exc))
    missing = cfg.missing()
    if missing:
        print(f"{len(missing)} design field(s) still to be decided by the group:")
        for m in missing:
            print(f"  - {m}")
    else:
        print("All required design fields are filled in.")
    problems = cfg._range_checks()
    for p in problems:
        print(f"  problem: {p}")
    print(f"Fingerprint of this parameter set: {cfg.fingerprint()}")

    _banner("Files you supply (data/external)")
    data_cfg = DataConfig.load(args.data_config)
    print(external_inventory(data_cfg).to_string(index=False))

    _banner("Statutory charges not yet verified against a source")
    unverified = StatutorySchedule.load(args.statutory).unverified()
    print("\n".join(f"  - {u}" for u in unverified) if unverified else "  none")
    if missing or problems:
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Back-test commands
# --------------------------------------------------------------------------- #
def _run_period(args, start, end, label: str, log_test_access: bool) -> None:
    from .analytics import capacity_sweep
    from .pipeline import run_variants
    from .report import build_report_pack
    from .research import evaluate_test

    ws = _workspace(args)
    cfg = _strategy(args)
    registry = _registry(args)
    _banner(f"{label.upper()} period: {start} to {end}   (config {cfg.fingerprint()})")
    if log_test_access:
        # Logs the access and warns if the parameters were not frozen first.
        evaluate_test(ws, cfg, registry)
    variants = run_variants(ws, cfg, start, end)
    capacity = None
    if cfg.research.capacity_aum_inr:
        print("Capacity sweep ...")
        rf = pd.Series(ws.md.rf_daily, index=ws.md.dates)
        capacity = capacity_sweep(ws, cfg, start, end, cfg.research.capacity_aum_inr, rf)
    pack = build_report_pack(ws, cfg, variants, Path(args.out) / label, label, capacity, registry)
    cols = [c for c in ("net", "gross", "paper") if c in pack["01_performance_summary"].columns]
    rows = ["cagr", "annual_volatility", "sharpe", "sortino", "max_drawdown"]
    print(pack["01_performance_summary"].loc[rows, cols].to_string())


def cmd_train(args) -> None:
    cfg = StrategyConfig.load(args.strategy)
    _run_period(args, cfg.sample.train_start, cfg.sample.train_end, "train", log_test_access=False)


def cmd_test(args) -> None:
    cfg = StrategyConfig.load(args.strategy)
    _run_period(args, cfg.sample.test_start, cfg.sample.test_end, "test", log_test_access=True)


def cmd_full(args) -> None:
    cfg = StrategyConfig.load(args.strategy)
    print("NOTE: the full-sample run includes the test period, so it is logged as a test-period access.")
    _run_period(args, cfg.sample.train_start, cfg.sample.test_end, "full", log_test_access=True)


def cmd_search(args) -> None:
    from . import plots
    from .research import grid_search, sensitivity_table

    ws = _workspace(args)
    cfg = _strategy(args)
    registry = _registry(args)
    if not cfg.research.grid:
        sys.exit("research.grid is empty in the strategy config. List the parameter values you want to compare; "
                 "see docs/03_design_decisions.md.")
    n = 1
    for values in cfg.research.grid.values():
        n *= len(values)
    _banner(f"Parameter search on the TRAINING period: {n} combination(s)")
    table = grid_search(ws, cfg, registry, note=args.note)
    out = registry.folder / f"grid_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    table.to_csv(out, index=False)
    metric = cfg.research.selection_metric
    if metric and metric in table.columns:
        table = table.sort_values(metric, ascending=False)  # larger is better for every metric offered
    print(table.drop(columns=["fingerprint", "error"], errors="ignore").round(4).to_string(index=False))
    params = list(cfg.research.grid)
    if len(params) >= 2 and metric in table.columns:
        pivot = sensitivity_table(table, params[0], params[1], metric)
        plots.save(plots.sensitivity_heatmap(pivot, metric, params[0], params[1]),
                   registry.folder / f"sensitivity_{metric}.png", ws.md.data_kind, cfg.meta.is_example)
    print(f"\nSaved {out}. Every run is also logged in {registry.trials_path}.")
    print("The table ranks; it does not choose. Pick a robust region, write down why, then freeze.")


def cmd_freeze(args) -> None:
    from .research import freeze

    cfg = _strategy(args)
    record = freeze(cfg, _registry(args), args.frozen)
    _banner("Parameters frozen")
    for k, v in record.items():
        print(f"  {k:36s} {v}")
    print(f"\nRun the test period with:  python run.py test --strategy {args.frozen}")


def cmd_liquidity(args) -> None:
    from .config import UniverseCfg
    from .universe import segment_liquidity_table

    ws = _workspace(args)
    bounds = UniverseCfg().rank_bounds
    try:  # use the group's own segment definitions if a config exists
        bounds = StrategyConfig.load(args.strategy).universe.rank_bounds
    except ConfigError:
        pass
    _banner("Liquidity of each size segment at each year-end (typical daily traded value, Rs crore)")
    table = segment_liquidity_table(ws.md, bounds)
    with pd.option_context("display.width", 220, "display.float_format", "{:,.2f}".format):
        print(table.to_string())
    out = Path(args.out) / "segment_liquidity.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out)
    print(f"\nSaved {out}. Facts for choosing your liquidity filter, fund size and participation cap - the choice is yours.")


def cmd_episodes(args) -> None:
    from .stress import drawdown_episodes

    ws = _workspace(args)
    md = ws.md
    if not args.index:
        _banner("Index series available in your data")
        print("\n".join(f"  {name}" for name in sorted(md.indices.columns)))
        print("\nRun again with --index \"NAME\" to list that index's drawdown episodes.")
        return
    level = pd.Series(md.index_level(args.index), index=md.dates)
    _banner(f"Falls of at least {args.min_depth:.0%} in {args.index.upper()} "
            f"({level.dropna().index[0].date()} to {level.dropna().index[-1].date()})")
    table = drawdown_episodes(level, args.min_depth)
    print(table.to_string(index=False) if len(table) else "none")
    print("\nThese are facts about the index. Which episodes to use as stress windows, and why, is your call.")


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, strategy: bool = True):
        sp.add_argument("--data-config", default=DEFAULT_DATA)
        sp.add_argument("--statutory", default=DEFAULT_STATUTORY)
        sp.add_argument("--out", default="results", help="folder for outputs (default: results)")
        if strategy:
            sp.add_argument("--strategy", default=DEFAULT_STRATEGY)

    sp = sub.add_parser("fixture", help="generate the synthetic test fixture")
    sp.add_argument("--root", default=FIXTURE_ROOT)
    sp.add_argument("--companies", type=int, default=320)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_fixture)

    sp = sub.add_parser("download", help="download the public NSE files")
    sp.add_argument("--data-config", default=DEFAULT_DATA)
    sp.add_argument("--start", help="YYYY-MM-DD (default: download_start in the data config)")
    sp.add_argument("--end", help="YYYY-MM-DD (default: today)")
    sp.add_argument("--datasets", nargs="*", help="subset of: cm_bhav pr ind_close mcwb mcap_lists fo_bhav sec_list")
    sp.add_argument("--retry-missing", action="store_true", help="ask again for dates recorded as missing")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser("build", help="parse raw files, build the panel, write the quality report")
    sp.add_argument("--data-config", default=DEFAULT_DATA)
    sp.add_argument("--force", action="store_true", help="re-parse every raw file")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("check", help="is the strategy config complete? what is missing?")
    common(sp)
    sp.set_defaults(func=cmd_check)

    for name, func, text in (("train", cmd_train, "back-test the training period"),
                             ("test", cmd_test, "back-test the test period (logged)"),
                             ("full", cmd_full, "training + test period in one run (logged)")):
        sp = sub.add_parser(name, help=text)
        common(sp)
        sp.set_defaults(func=func)

    sp = sub.add_parser("liquidity", help="how liquid each size segment is, year by year (to choose filters)")
    common(sp)
    sp.set_defaults(func=cmd_liquidity)

    sp = sub.add_parser("episodes", help="date the large falls of an index (to choose stress windows)")
    common(sp, strategy=False)
    sp.add_argument("--index", help="index name; omit to list the names available")
    sp.add_argument("--min-depth", type=float, default=0.15, help="smallest fall to list (0.15 = 15%%)")
    sp.set_defaults(func=cmd_episodes)

    sp = sub.add_parser("search", help="parameter search on the training period (every run is logged)")
    common(sp)
    sp.add_argument("--note", default="", help="free text stored with these trials")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("freeze", help="lock the chosen parameters before the test period")
    common(sp)
    sp.add_argument("--frozen", default="config/frozen_strategy.yaml")
    sp.set_defaults(func=cmd_freeze)
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ConfigError as exc:
        sys.exit(f"\n{exc}")
    except FileNotFoundError as exc:
        sys.exit(f"\n{exc}")


if __name__ == "__main__":
    main()
