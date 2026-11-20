"""The end-to-end notebook runs cleanly on the fixture, and is committed without outputs."""

from pathlib import Path

import nbformat
import yaml
from nbclient import NotebookClient

REPO = Path(__file__).resolve().parents[1]
NOTEBOOK = REPO / "notebooks" / "momentum_end_to_end.ipynb"


def test_committed_notebook_has_no_outputs():
    nb = nbformat.read(NOTEBOOK, as_version=4)
    code = [c for c in nb.cells if c.cell_type == "code"]
    assert len(code) >= 12
    assert all(not c.get("outputs") and c.get("execution_count") is None for c in code), \
        "strip outputs before committing: results in the repository must come from the group's own run"


def test_notebook_runs_end_to_end_on_the_fixture(fixture_root, tmp_path, monkeypatch):
    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text(yaml.safe_dump({"root": str(fixture_root), "data_kind": "synthetic"}))
    strategy = yaml.safe_load((REPO / "tests" / "fixtures" / "fixture_strategy.yaml").read_text())
    strategy["research"] = {"grid": {"signal.components.0.lookback_months": [6, 12], "portfolio.n_holdings": [15, 25]},
                            "selection_metric": "sharpe", "capacity_aum_inr": [5e7, 1e9]}
    strategy_yaml = tmp_path / "strategy.yaml"
    strategy_yaml.write_text(yaml.safe_dump(strategy, sort_keys=False))
    out = tmp_path / "results"
    for key, value in {"MOMO_DATA_CONFIG": data_yaml, "MOMO_STRATEGY": strategy_yaml, "MOMO_OUT": out,
                       "MOMO_RUN_SEARCH": 1, "MOMO_DO_FREEZE": 1, "MOMO_RUN_TEST": 1, "MOMO_RUN_FULL": 1}.items():
        monkeypatch.setenv(key, str(value))

    nb = nbformat.read(NOTEBOOK, as_version=4)
    NotebookClient(nb, timeout=1200, kernel_name="python3", resources={"metadata": {"path": str(REPO)}}).execute()

    for period in ("train", "test", "full"):
        assert (out / period / "tables" / "01_performance_summary.csv").exists()
        assert (out / period / "figures" / "01_equity_and_drawdown.png").exists()
        assert (out / period / "SYNTHETIC_NOT_A_RESULT.txt").exists()
    assert (out / "train" / "tables" / "20_capacity.csv").exists()
    research = out / "research"
    assert (research / "trials.csv").exists() and (research / "frozen.json").exists()
    assert (research / "test_access_log.csv").exists() and (research / "sensitivity.png").exists()
