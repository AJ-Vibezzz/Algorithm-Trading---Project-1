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
from .panel import load_index_members, load_panel, load_securities, load_size_lists


def index_replication(cfg: DataConfig, max_gap_days: int = 100) -> Dict[str, pd.DataFrame]:
    """Rebuild the Nifty 50 and Nifty Next 50 from their members and compare with the published index.

    An independent check of the adjusted returns. The exchange's monthly files
    give each index's members and weights at the month-end; holding those
    weights through the following month with **our** adjusted price returns
    should reproduce the published index almost exactly. A missed split or
    bonus in a member shows up as a large one-day difference, and the member
    that contributed most to it is named.

    Returns ``daily`` (basket return, index return, difference per day and
    index), ``worst`` (the largest differences, with the main contributor) and
    ``summary`` (one row per index). Small differences are expected: the index
    is rebalanced inside the month, caps weights and adjusts for rights issues.
    """
    members = load_index_members(cfg)
    indices = read_interim(cfg, "indices")
    empty = {"daily": pd.DataFrame(), "worst": pd.DataFrame(), "summary": pd.DataFrame()}
    if members.empty or indices.empty or "weight" not in members.columns:
        return empty
    panel = load_panel(cfg, columns=["date", "sid", "symbol", "ret_price", "adj_source"])
    panel = panel[panel["sid"].isin(members["sid"].unique())]
    ret = panel.pivot(index="date", columns="sid", values="ret_price").sort_index()
    days = ret.index
    daily, worst = [], []
    for name, mem in members.groupby("index_name"):
        level = indices[indices["index_name"] == name].set_index("date")["close"].sort_index()
        if level.empty:
            continue
        # the index file has a few missing days: a return is only taken between two consecutive market days
        on_days = level.reindex(days)
        index_ret = on_days / on_days.shift(1) - 1.0
        dates = sorted(mem["as_of"].unique())
        for k, as_of in enumerate(dates):
            nxt = dates[k + 1] if k + 1 < len(dates) else days[-1]
            window = days[(days > as_of) & (days <= nxt) & (days <= as_of + pd.Timedelta(days=max_gap_days))]
            part = mem[(mem["as_of"] == as_of) & mem["weight"].notna()]
            if len(window) == 0 or part.empty:
                continue
            w0 = part.set_index("sid")["weight"].astype(float)
            w0 = w0[w0.index.isin(ret.columns)]
            if w0.sum() <= 0:
                continue
            w0 = w0 / w0.sum()
            r = ret.loc[window, w0.index].fillna(0.0)
            value = (1.0 + r).cumprod() * w0  # buy and hold from the month-end weights
            before = value.shift(1)
            before.iloc[0] = w0
            contribution = (value - before).div(before.sum(axis=1), axis=0)
            basket = contribution.sum(axis=1)
            both = pd.DataFrame({"index_name": name, "basket_return": basket, "index_return": index_ret.reindex(window)})
            both["difference"] = both["basket_return"] - both["index_return"]
            both = both.dropna(subset=["index_return"])
            daily.append(both)
            for d, row in both[both["difference"].abs() > 0.004].iterrows():
                sid = contribution.loc[d].abs().idxmax()
                info = panel[(panel["date"] == d) & (panel["sid"] == sid)].iloc[0]
                worst.append({"date": d, "index_name": name, "difference": row["difference"],
                              "largest_contributor": info["symbol"], "its_return": info["ret_price"],
                              "its_weight": float(before.loc[d, sid] / before.loc[d].sum()),
                              "its_adjustment": info["adj_source"]})
    if not daily:
        return empty
    daily = pd.concat(daily).rename_axis("date").reset_index()
    rows = []
    for name, part in daily.groupby("index_name"):
        diff = part["difference"]
        rows.append({
            "index_name": name, "first_day": part["date"].min().date(), "last_day": part["date"].max().date(),
            "days_compared": int(len(part)),
            "correlation": round(float(part["basket_return"].corr(part["index_return"])), 5),
            "mean_abs_difference_bp": round(float(diff.abs().mean() * 1e4), 2),
            "tracking_error_pct_a_year": round(float(diff.std() * np.sqrt(252) * 100), 3),
            "days_difference_over_40bp": int((diff.abs() > 0.004).sum()),
            "cumulative_difference_pct": round(float(((1 + part["basket_return"]).prod()
                                                     / (1 + part["index_return"]).prod() - 1) * 100), 2),
        })
    worst = pd.DataFrame(worst).sort_values("difference", key=abs, ascending=False) if worst else pd.DataFrame(
        columns=["date", "index_name", "difference", "largest_contributor", "its_return", "its_weight", "its_adjustment"])
    return {"daily": daily, "worst": worst, "summary": pd.DataFrame(rows)}


def data_quality_report(cfg: DataConfig, verbose: bool = True) -> Dict[str, pd.DataFrame]:
    out_dir = cfg.processed / "quality"
    out_dir.mkdir(parents=True, exist_ok=True)
    panel = load_panel(cfg, columns=["date", "sid", "symbol", "series", "close", "value", "ret_total",
                                     "dividend", "adj_source", "shares", "lock", "mcap_src"])
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
        # where the day's market cap came from: the exchange's daily file (from 2024), its latest
        # market-cap list carried forward (from 2020), or nowhere (earlier years)
        "share_rows_mcap_from_daily_file": g["mcap_src"].apply(lambda s: (s == 1).mean()).round(4),
        "share_rows_mcap_from_lists": g["mcap_src"].apply(lambda s: (s == 2).mean()).round(4),
        "share_rows_without_mcap": g["mcap_src"].apply(lambda s: (s == 0).mean()).round(4),
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
    # Adjustments that rest on a judgement rather than on a complete exchange text: a ratio taken
    # from the price because the text was cut off, a very large dividend, a rights issue priced
    # without its face value, an event the text announced but the price did not confirm.
    to_check = log[log["adj_source"].isin(["ca_ratio_inferred", "large_dividend", "large_dividend_rejected",
                                           "ca_text_rights_face_value_unknown", "ca_text_rejected",
                                           "rights_not_adjusted", "exchange_vs_ca_text_mismatch"])]
    to_check.sort_values(["adj_source", "date"]).to_csv(out_dir / "adjustments_to_check.csv", index=False)

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

    # ---- size sources: every list, and the index series as linked across renames ----------
    lists = load_size_lists(cfg)
    if len(lists):
        size_sources = lists.groupby(["source", "as_of"]).agg(
            effective=("effective", "first"), companies_ranked=("sid", "size"),
            with_market_cap=("mcap", lambda x: int(x.notna().sum()))).reset_index()
    else:
        size_sources = pd.DataFrame(columns=["source", "as_of", "effective", "companies_ranked", "with_market_cap"])
    size_sources.to_csv(out_dir / "size_lists.csv", index=False)
    members = load_index_members(cfg)
    indices = read_interim(cfg, "indices")
    if len(indices):
        index_series = indices.groupby("index_name").agg(
            first_day=("date", "min"), last_day=("date", "max"), days=("date", "size"),
            names_in_file=("raw_name", lambda x: " -> ".join(pd.unique(x))) if "raw_name" in indices.columns
            else ("index_name", "first")).reset_index()
    else:
        index_series = pd.DataFrame(columns=["index_name", "first_day", "last_day", "days", "names_in_file"])
    index_series.to_csv(out_dir / "index_series.csv", index=False)

    replication = index_replication(cfg)
    replication["daily"].to_csv(out_dir / "index_replication_daily.csv", index=False)
    replication["worst"].to_csv(out_dir / "index_replication_largest_differences.csv", index=False)
    replication["summary"].to_csv(out_dir / "index_replication.csv", index=False)

    errors_path = cfg.interim / "_parse_errors.csv"
    n_parse_errors = len(pd.read_csv(errors_path)) if errors_path.exists() else 0

    summary = {
        **stats,
        "companies_that_stopped_trading": int(len(exits)),
        "unexplained_jumps_to_review": int(len(jumps)),
        "adjustments_to_check_by_hand": int(len(to_check)),
        "corporate_action_texts_not_understood": int(len(unrec)),
        "weekdays_without_data": int(len(gaps)),
        "weekdays_without_data_not_confirmed_holiday": int((gaps["download_status"] != "missing").sum()),
        "files_that_failed_to_parse": int(n_parse_errors),
        "first_day_with_a_market_cap": str(panel.loc[panel["mcap_src"] > 0, "date"].min().date())
        if (panel["mcap_src"] > 0).any() else None,
        "exchange_market_cap_lists": int((size_sources["source"] == "nse_list").sum()),
        "amfi_lists": int((size_sources["source"] == "amfi").sum()),
        "monthly_index_member_lists": int(members["as_of"].nunique()) if len(members) else 0,
        "index_series": int(len(index_series)),
        "index_replication": replication["summary"].to_dict("records"),
        "first_day_of_index_data": str(indices["date"].min().date()) if len(indices) else None,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    inventory = external_inventory(cfg)

    if verbose:
        print(f"Data-quality report written to {out_dir}")
        for k in ("first_date", "last_date", "trading_days", "companies", "companies_that_stopped_trading",
                  "unexplained_jumps_to_review", "adjustments_to_check_by_hand", "corporate_action_texts_not_understood",
                  "weekdays_without_data_not_confirmed_holiday", "files_that_failed_to_parse",
                  "companies_without_sector", "first_day_with_a_market_cap", "exchange_market_cap_lists",
                  "amfi_lists", "monthly_index_member_lists", "first_day_of_index_data"):
            print(f"  {k:48s} {summary.get(k)}")
        if len(replication["summary"]):
            print("  Check of the adjusted returns - published index rebuilt from its members:")
            for row in replication["summary"].itertuples():
                print(f"    {row.index_name:14s} {row.days_compared} days, correlation {row.correlation:.4f}, "
                      f"mean daily difference {row.mean_abs_difference_bp:.1f} bp, "
                      f"{row.days_difference_over_40bp} days over 40 bp")
        missing = inventory[(~inventory["present"]) & (inventory["status"] != "optional")]
        if len(missing):
            print("  Files you have not supplied yet:")
            for row in missing.itertuples():
                print(f"    - data/external/{row.file} ({row.status}): {row.if_absent}")
    return {"by_year": by_year, "exits": exits, "unexplained_jumps": jumps,
            "unrecognised_purposes": unrec, "calendar_gaps": gaps, "inventory": inventory,
            "adjustments_to_check": to_check, "index_replication": replication["summary"],
            "index_replication_worst": replication["worst"],
            "size_lists": size_sources, "index_series": index_series,
            "summary": pd.Series(summary, dtype=object)}
