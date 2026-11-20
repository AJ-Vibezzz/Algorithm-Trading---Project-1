"""Shared test fixtures.

All tests run on the SYNTHETIC fixture (invented data written in the real NSE
file layouts by ``momo.data.synthetic``). The fixture is built once and cached
under ``data/_synthetic_fixture_tests`` (git-ignored); delete that folder to
force a rebuild. Nothing here touches real market data or produces results.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from momo.config import DataConfig, StrategyConfig  # noqa: E402
from momo.data.ingest import ingest  # noqa: E402
from momo.data.panel import build_panel  # noqa: E402
from momo.data.synthetic import FixtureSpec, generate_fixture  # noqa: E402
from momo.pipeline import Workspace, run_backtest  # noqa: E402

# Where the built fixture is cached. Override with MOMO_TEST_CACHE to force a clean build elsewhere.
CACHE = Path(os.environ.get("MOMO_TEST_CACHE", REPO / "data" / "_synthetic_fixture_tests"))
STATUTORY = REPO / "config" / "statutory_charges.yaml"
STRATEGY = REPO / "tests" / "fixtures" / "fixture_strategy.yaml"


def _signature(spec: FixtureSpec) -> str:
    """Changes whenever the spec or the generator / data-layer code changes."""
    blob = json.dumps(asdict(spec), sort_keys=True, default=str)
    for name in ("synthetic.py", "parsers.py", "panel.py", "ingest.py", "identifiers.py", "corporate_actions.py",
                 "size.py", "sources.py", "external.py"):
        blob += (REPO / "src" / "momo" / "data" / name).read_text()
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


_CONFIG = None  # the pytest config, so the slow first-run setup can talk past output capture


def pytest_configure(config):
    global _CONFIG
    _CONFIG = config


def _say(message: str) -> None:
    """Print to the terminal even while pytest captures output (so a first run does not look frozen)."""
    capman = _CONFIG.pluginmanager.getplugin("capturemanager") if _CONFIG else None
    if capman is None:
        print(message, flush=True)
        return
    with capman.global_and_fixture_disabled():
        print("\n" + message, flush=True)


def build_fixture(name: str, spec: FixtureSpec) -> Path:
    """Generate (or reuse) one synthetic data set and run it through the data layer."""
    root = CACHE / name
    stamp = root / "_signature.txt"
    sig = _signature(spec)
    if stamp.exists() and stamp.read_text() == sig:
        return root
    shutil.rmtree(root, ignore_errors=True)
    _say(f"[setup] building synthetic test data '{name}' - first run only, about 1-3 minutes, no output meanwhile ...")
    generate_fixture(root, spec)
    cfg = DataConfig(root=str(root), data_kind="synthetic")
    ingest(cfg, progress=False)
    build_panel(cfg, verbose=False)
    stamp.write_text(sig)
    return root


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    """Main fixture: 320 companies, 2021-2025, previous close left unadjusted on ex-dates (as in the real files)."""
    return build_fixture("main", FixtureSpec())


@pytest.fixture(scope="session")
def adjusted_root() -> Path:
    """Small fixture in which the exchange DOES put the adjusted base price in the previous close."""
    spec = FixtureSpec(start=dt.date(2023, 3, 1), end=dt.date(2024, 12, 31), n_companies=90, seed=11,
                       prevclose_adjusted=True)
    return build_fixture("adjusted", spec)


# A small fixture laid out like the real archive: no ISIN column in the oldest price files, a first
# market-cap list with ranks only, later lists with values, and the daily market-cap file only at the end.
LATE_SPEC = FixtureSpec(
    start=dt.date(2021, 1, 4), end=dt.date(2023, 6, 30), n_companies=110, seed=3,
    isin_from=dt.date(2021, 6, 22),
    mcap_list_dates=[dt.date(2021, 9, 30), dt.date(2022, 3, 31), dt.date(2022, 9, 30)],
    rank_only_until=dt.date(2021, 9, 30),
    mcap_daily_from=dt.date(2023, 1, 2),
)


@pytest.fixture(scope="session")
def late_root() -> Path:
    """The real-archive-like fixture: size comes from lists first, from the daily file only at the end."""
    return build_fixture("late_mcap", LATE_SPEC)


@pytest.fixture(scope="session")
def late_truth_root() -> Path:
    """The same companies and prices, but with the daily market-cap file from the first day: the answer key."""
    return build_fixture("late_mcap_truth", dataclasses.replace(LATE_SPEC, mcap_daily_from=None))


@pytest.fixture(scope="session")
def late_ws(late_root) -> Workspace:
    return Workspace.load(statutory_yaml=STATUTORY, data_cfg=DataConfig(root=str(late_root), data_kind="synthetic"),
                          verbose=False)


@pytest.fixture(scope="session")
def data_cfg(fixture_root) -> DataConfig:
    return DataConfig(root=str(fixture_root), data_kind="synthetic")


@pytest.fixture(scope="session")
def ws(data_cfg) -> Workspace:
    return Workspace.load(statutory_yaml=STATUTORY, data_cfg=data_cfg, verbose=False)


@pytest.fixture(scope="session")
def cfg() -> StrategyConfig:
    c = StrategyConfig.load(STRATEGY)
    c.validate()
    return c


@pytest.fixture(scope="session")
def net(ws, cfg):
    """Net back-test over the whole fixture sample."""
    return run_backtest(ws, cfg, cfg.sample.train_start, cfg.sample.test_end)


@pytest.fixture(scope="session")
def broad(ws, cfg):
    """A back-test that holds every eligible stock (hundreds of small positions).

    Used by the tests of rare events - circuit blocks, non-trading days,
    delisting write-offs - which a 20-stock book might not meet on a given
    random path. Holding everything makes them certain to occur.
    """
    wide = cfg.with_overrides({"portfolio.selection": "top_fraction", "portfolio.top_fraction": 1.0})
    return run_backtest(ws, wide, wide.sample.train_start, wide.sample.test_end)


@pytest.fixture(scope="session")
def truth(fixture_root):
    return {name: pd.read_parquet(fixture_root / f"truth_{name}.parquet")
            for name in ("returns", "actions", "companies")}
