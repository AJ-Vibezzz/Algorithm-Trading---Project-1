"""The data layer against the fixture's answer key: adjusted returns, identity, survivorship."""

import json

import numpy as np
import pandas as pd

from momo.config import DataConfig
from momo.data.panel import load_panel, load_securities
from momo.frictions import circuit_lock


def _compare(root):
    cfg = DataConfig(root=str(root), data_kind="synthetic")
    panel = load_panel(cfg)
    truth = pd.read_parquet(root / "truth_returns.parquet")
    merged = panel.merge(truth, on=["date", "symbol"], how="outer", indicator=True, suffixes=("", "_true"))
    return cfg, panel, merged


def test_total_returns_match_truth_when_exchange_adjusts_prevclose(fixture_root):
    _, _, m = _compare(fixture_root)
    assert (m["_merge"] != "right_only").all(), "every traded row in the truth must be in the panel"
    both = m[(m["_merge"] == "both") & (m["kind"] == "")]
    err = (both["ret_total"] - both["ret_total_true"]).abs()
    assert len(both) > 100_000
    # The exchange publishes its adjusted base price rounded to the paisa, so on an ex-date the
    # recovered return can differ from the exact one by half a paisa relative to the price.
    allowed = 0.0051 / both["prevclose"] * (1 + both["ret_total_true"].abs()) + 1e-6
    assert (err <= allowed).all(), "splits, bonuses, rights and dividends must be recovered to within paisa rounding"
    assert (err > 1e-4).sum() < 20 and err.max() < 5e-3


def test_total_returns_match_truth_when_only_the_action_text_is_available(unadjusted_root):
    cfg, _, m = _compare(unadjusted_root)
    both = m[(m["_merge"] == "both") & (m["kind"] == "")]
    err = (both["ret_total"] - both["ret_total_true"]).abs()
    assert err.max() < 1e-3
    stats = json.loads((cfg.processed / "panel_stats.json").read_text())
    assert stats["adjustments_by_source"].get("ca_text_only", 0) > 0, "this fixture must exercise the text-only path"


def test_first_trading_day_has_no_return(fixture_root):
    _, panel, _ = _compare(fixture_root)
    first = panel.sort_values("date").groupby("sid").head(1)
    assert first["ret_total"].isna().all(), "a listing-day 'return' from the issue price is not investable"


def test_demerger_drop_is_neutralised(fixture_root):
    _, _, m = _compare(fixture_root)
    dem = m[(m["_merge"] == "both") & (m["kind"] == "demerger")]
    if len(dem):  # the random fixture may contain none
        assert (dem["adj_source"] == "demerger_neutralised").all()
        assert (dem["ret_total"] == 0.0).all()


def test_non_equity_lines_are_dropped(fixture_root):
    _, panel, _ = _compare(fixture_root)
    assert not panel["symbol"].str.startswith("SYNETF").any(), "ETFs trade in the EQ series but are not shares"
    assert not (panel["symbol"] == "SYNBOND").any()
    assert set(panel["series"].unique()) <= {"EQ", "BE", "BZ"}


def test_renames_and_isin_changes_keep_one_company_id(fixture_root, truth):
    sec = load_securities(DataConfig(root=str(fixture_root), data_kind="synthetic"))
    assert len(sec) == len(truth["companies"]), "one id per true company - no splits, no merges"
    renamed = truth["companies"][truth["companies"]["symbols"].str.contains(r"\|")]
    assert len(renamed) > 0
    for symbols in renamed["symbols"]:
        old, new = symbols.split("|")[0], symbols.split("|")[-1]
        hit = sec[sec["symbols"].apply(lambda s: new in s.split("|"))]
        assert len(hit) == 1 and old in hit.iloc[0]["symbols"].split("|"), f"{old} -> {new} must be one company"
    assert sec["isins"].str.contains(r"\|").sum() > 0, "face-value splits change the ISIN; the id must survive"


def test_reused_symbol_is_two_companies(fixture_root, truth):
    comp = truth["companies"]
    first_symbols = comp["symbols"].str.split("|").str[0]
    reused = first_symbols[first_symbols.duplicated(keep=False)].unique()
    assert len(reused) == 1, "the fixture plants exactly one re-used symbol"
    sec = load_securities(DataConfig(root=str(fixture_root), data_kind="synthetic"))
    owners = sec[sec["symbols"].apply(lambda s: reused[0] in s.split("|"))]
    assert len(owners) == 2
    a, b = owners.sort_values("first_date").itertuples()
    assert a.last_date < b.first_date


def test_delisted_companies_stay_in_the_panel(fixture_root, truth):
    sec = load_securities(DataConfig(root=str(fixture_root), data_kind="synthetic"))
    gone = truth["companies"][truth["companies"]["delist_reason"] != ""]
    assert len(gone) >= 10
    panel_end = sec["last_date"].max()
    for row in gone.itertuples():
        last_symbol = row.symbols.split("|")[-1]
        hit = sec[sec["symbols"].apply(lambda s: last_symbol in s.split("|")) & (sec["last_date"] < panel_end)]
        assert len(hit) >= 1, f"delisted {last_symbol} is missing - that would be survivorship bias"


def test_circuit_lock_detection():
    #                 upper lock   lower lock   big move, not at high   normal day
    close = np.array([105.0,       95.0,        104.0,                  101.0])
    high = np.array([105.0,        99.0,        105.0,                  102.0])
    low = np.array([101.0,         95.0,        100.0,                  99.5])
    prev = np.array([100.0,        100.0,       100.0,                  100.0])
    none = np.array(["", "", "", ""], dtype=object)
    assert circuit_lock(close, high, low, prev, none).tolist() == [1, -1, 0, 0]
    # the exchange's own band-hit file marks a lock even at a non-standard band
    assert circuit_lock(np.array([103.0]), np.array([103.0]), np.array([100.0]), np.array([100.0]),
                        np.array(["H"], dtype=object)).tolist() == [1]


def test_panel_flags_match_planted_locks(fixture_root):
    _, panel, _ = _compare(fixture_root)
    locked = panel[panel["band_hit"].isin(["H", "L"])]
    assert len(locked) > 500
    expected = locked["band_hit"].map({"H": 1, "L": -1})
    assert (locked["lock"] == expected).mean() > 0.99
