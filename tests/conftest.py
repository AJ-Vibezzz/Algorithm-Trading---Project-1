"""Shared test fixtures.

All tests run on the SYNTHETIC fixture (invented data written in the real NSE
file layouts by ``momo.data.synthetic``). The fixture is built once and cached
under ``data/_synthetic_fixture_tests`` (git-ignored); delete that folder to
force a rebuild. Nothing here touches real market data or produces results.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
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

CACHE = REPO / "data" / "_synthetic_fixture_tests"
STATUTORY = REPO / "config" / "statutory_charges.yaml"
STRATEGY = REPO / "tests" / "fixtures" / "fixture_strategy.yaml"


def _signature(spec: FixtureSpec) -> str:
    """Changes whenever the spec or the generator / data-layer code changes."""
    blob = json.dumps(asdict(spec), sort_keys=True, default=str)
    for name in ("synthetic.py", "parsers.py", "panel.py", "ingest.py", "identifiers.py", "corporate_actions.py"):
        blob += (REPO / "src" / "momo" / "data" / name).read_text()
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def build_fixture(name: str, spec: FixtureSpec) -> Path:
    """Generate (or reuse) one synthetic data set and run it through the data layer."""
    root = CACHE / name
    stamp = root / "_signature.txt"
    sig = _signature(spec)
    if stamp.exists() and stamp.read_text() == sig:
        return root
    shutil.rmtree(root, ignore_errors=True)
    generate_fixture(root, spec)
    cfg = DataConfig(root=str(root), data_kind="synthetic")
    ingest(cfg, progress=False)
    build_panel(cfg, verbose=False)
    stamp.write_text(sig)
    return root


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    """Main fixture: 320 companies, 2021-2025, exchange-adjusted previous close."""
    return build_fixture("main", FixtureSpec())


@pytest.fixture(scope="session")
def unadjusted_root() -> Path:
    """Small fixture in which the exchange does NOT adjust the previous close on ex-dates."""
    spec = FixtureSpec(start=dt.date(2023, 3, 1), end=dt.date(2024, 12, 31), n_companies=90, seed=11,
                       prevclose_adjusted=False)
    return build_fixture("unadjusted", spec)


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
def truth(fixture_root):
    return {name: pd.read_parquet(fixture_root / f"truth_{name}.parquet")
            for name in ("returns", "actions", "companies")}
