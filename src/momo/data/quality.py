"""Data-quality report - read this before trusting any back-test number.

The project brief makes the group "responsible for every number". This report
is the evidence pack for the data: what was loaded, what was adjusted and why,
what the code could not understand, and which companies left the sample.

Outputs land in ``data/processed/quality``:

    summary.json              headline counts
    by_year.csv               coverage and activity per calendar year
    exits.csv                 every company that stopped trading (survivorship check)
    unexplained_jumps.csv     large one-day moves with no corporate action on record
    unrecognised_purposes.csv corporate-action text the parser did not understand
    calendar_gaps.csv         weekdays with no data (holiday or missing download?)
"""

from __future__ import annotations

import json
from typing import Dict

import numpy as np
import pandas as pd

from ..config import DataConfig
from .corporate_actions import parse_purpose
from .external import external_inventory
from .ingest import read_interim
from .panel import load_panel, load_securities


def data_quality_report(cfg: DataConfig, verbose: bool = True) -> Dict[str, pd.DataFrame]:
    out_dir = cfg.processed / "quality"
    out_dir.mkdir(parents=True, exist_ok=True)
    panel = load_panel(cfg, columns=["date", "sid", "symbol", "series", "close", "value", "ret_total",
                                     "dividend", "adj_source", "shares", "lock"])
    sec = load_securities(cfg)
    stats = json.loads((cfg.processed / "panel_stats.json").read_text())
    last_day = panel["date"].max()

    # ---- per-year coverage ----------------------------------------------------
    panel["year"] = panel["date"].dt.year
    g = panel.groupby("year")
    by_year = pd.DataFrame({
        "trading_days": g["date"].nunique(),
        "rows": g.size(),
        "companies_traded": g["sid"].nunique(),
        "median_daily_value_cr": g["value"].median() / 1e7,
        "share_rows_with_shares_outstanding": g["shares"].apply(lambda s: s.notna().mean()).round(4),
        "dividend_events": g["dividend"].apply(lambda s: int((s > 0).sum())),
        "split_bonus_rights_adjustments": g["adj_source"].apply(
            lambda s: int(s.isin(["exchange+ca_text", "exchange_only", "ca_text_only",
                                  "exchange_vs_ca_text_mismatch"]).sum())),
        "rows_locked_upper_band": g["lock"].apply(lambda s: int((s == 1).sum())),
        "rows_locked_lower_band": g["lock"].apply(lambda s: int((s == -1).sum())),
        "rows_not_in_EQ_series": g["series"].apply(lambda s: int((s != "EQ").sum())),
    })
    by_year["avg_rows_per_day"] = (by_year["rows"] / by_year["trading_days"]).round(0)
    sec["first_year"] = sec["first_date"].dt.year
    sec["last_year"] = sec["last_date"].dt.year
    exited = sec[sec["last_date"] < last_day - pd.Timedelta(days=45)]
    by_year["new_listings"] = sec.groupby("first_year").size().reindex(by_year.index).fillna(0).astype(int)
    by_year["exits"] = exited.groupby("last_year").size().reindex(by_year.index).fillna(0).astype(int)
    by_year.to_csv(out_dir / "by_year.csv")

    # ---- companies that left: the survivorship evidence ---------------------------
    last_px = panel.sort_values("date").groupby("sid").tail(1).set_index("sid")
    exits = exited[["sid", "symbol", "symbols", "first_date", "last_date", "n_days", "last_series", "sector"]].copy()
    exits["last_close"] = exits["sid"].map(last_px["close"])
    ret_6m = (panel[panel["sid"].isin(exits["sid"])].sort_values("date").groupby("sid")["ret_total"]
              .apply(lambda r: float(np.prod(1 + r.tail(126).fillna(0)) - 1)))
    exits["return_last_126_days"] = exits["sid"].map(ret_6m)
    exits = exits.sort_values("last_date")
    exits.to_csv(out_dir / "exits.csv", index=False)

    # ---- things to check by hand -----------------------------------------------------
    log = pd.read_csv(cfg.processed / "adjustment_log.csv", parse_dates=["date"])
    jumps = log[log["adj_source"] == "unexplained_jump"].copy()
    jumps = jumps.reindex(jumps["ret_price"].abs().sort_values(ascending=False).index)
    jumps.to_csv(out_dir / "unexplained_jumps.csv", index=False)

    listings = read_interim(cfg, "corp_actions")
    if len(listings):
        listings = listings[listings["series"].isin(cfg.equity_series + [""])]
        counts = listings.groupby("purpose").size().sort_values(ascending=False)
        bad = [p for p in counts.index if not parse_purpose(p).recognised]
        unrec = counts.loc[bad].rename("listings").reset_index()
    else:
        unrec = pd.DataFrame(columns=["purpose", "listings"])
    unrec.to_csv(out_dir / "unrecognised_purposes.csv", index=False)

    # ---- weekdays with no data --------------------------------------------------------
    have = pd.DatetimeIndex(panel["date"].unique())
    weekdays = pd.bdate_range(have.min(), have.max())
    gaps = pd.DataFrame({"date": weekdays.difference(have)})
    gaps["weekday"] = gaps["date"].dt.day_name()
    manifest_path = cfg.raw / "_manifest.csv"
    if manifest_path.exists():
        man = pd.read_csv(manifest_path, dtype=str)
        man = man[man["dataset"] == "cm_bhav"].set_index("date")["status"]
        gaps["download_status"] = gaps["date"].dt.strftime("%Y-%m-%d").map(man).fillna("not attempted")
    else:
        gaps["download_status"] = "no manifest"
    gaps.to_csv(out_dir / "calendar_gaps.csv", index=False)

    errors_path = cfg.interim / "_parse_errors.csv"
    n_parse_errors = len(pd.read_csv(errors_path)) if errors_path.exists() else 0

    summary = {
        **stats,
        "companies_that_stopped_trading": int(len(exits)),
        "unexplained_jumps_to_review": int(len(jumps)),
        "corporate_action_texts_not_understood": int(len(unrec)),
        "weekdays_without_data": int(len(gaps)),
        "weekdays_without_data_not_confirmed_holiday": int((gaps["download_status"] != "missing").sum()),
        "files_that_failed_to_parse": int(n_parse_errors),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    inventory = external_inventory(cfg)

    if verbose:
        print(f"Data-quality report written to {out_dir}")
        for k in ("first_date", "last_date", "trading_days", "companies", "companies_that_stopped_trading",
                  "unexplained_jumps_to_review", "corporate_action_texts_not_understood",
                  "weekdays_without_data_not_confirmed_holiday", "files_that_failed_to_parse",
                  "companies_without_sector"):
            print(f"  {k:48s} {summary.get(k)}")
        missing = inventory[(~inventory["present"]) & (inventory["status"] != "optional")]
        if len(missing):
            print("  Files you have not supplied yet:")
            for row in missing.itertuples():
                print(f"    - data/external/{row.file} ({row.status}): {row.if_absent}")
    return {"by_year": by_year, "exits": exits, "unexplained_jumps": jumps,
            "unrecognised_purposes": unrec, "calendar_gaps": gaps, "inventory": inventory,
            "summary": pd.Series(summary, dtype=object)}
