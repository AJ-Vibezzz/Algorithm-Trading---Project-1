"""Stage 2: build the survivorship-free daily panel with adjusted returns.

``data/interim``  ->  ``data/processed/panel/panel_YYYY.parquet``  (+ audit files)

What this stage does, in order
------------------------------
1. Keep ordinary equity shares only (series EQ/BE/BZ, equity ISINs) - ETFs,
   bonds, partly-paid shares and the like are dropped.
2. Give every company one permanent id across renames and ISIN changes
   (``identifiers.py``). Companies that later delisted stay in the panel.
3. Compute each day's return **robustly to corporate actions**. Two independent
   pieces of evidence are used and cross-checked:

   * the exchange's own ``prevclose`` field, which the exchange sets to the
     adjusted base price on an ex-date; and
   * the corporate-action text (bonus / split / rights / dividend) parsed from
     the exchange's daily listings.

   Whichever applies is used, and every adjustment is written to an audit log
   with the reason, so each one can be checked by hand.
4. Add dividends to get total returns; build total-return and price indices.
5. Attach shares outstanding and market capitalisation as published that day.

Nothing here looks forward in time: a row for date *t* depends only on files
dated *t* or earlier.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from ..config import DataConfig
from ..frictions import circuit_lock
from ..utils import to_ns
from .corporate_actions import build_action_table
from .identifiers import assign_security_ids, resolve_duplicates, security_master
from .ingest import read_interim

PANEL_COLUMNS = [
    "date", "sid", "symbol", "series", "isin",
    "open", "high", "low", "close", "prevclose", "vwap",
    "volume", "value", "trades",
    "ret_total", "ret_price", "tri", "pri",
    "dividend", "adj_factor", "adj_source",
    "shares", "face_value", "mcap", "band_hit", "lock", "cs2d", "arprod",
]


# --------------------------------------------------------------------------- #
# Step helpers
# --------------------------------------------------------------------------- #
def _equity_only(prices: pd.DataFrame, cfg: DataConfig, stats: dict) -> pd.DataFrame:
    """Drop companies whose ISIN says they are not ordinary equity shares.

    The test is applied per company (after linking), so a line with a blank
    ISIN in an old file is still classified by the ISIN it carries later.
    """
    if not cfg.require_equity_isin:
        return prices
    has_isin = prices["isin"] != ""
    is_equity = prices["isin"].str.match(cfg.equity_isin_regex)
    bad_sids = prices.loc[has_isin & ~is_equity, "sid"].unique()
    stats["non_equity_companies_dropped"] = int(len(bad_sids))
    stats["rows_with_blank_isin"] = int((~has_isin).sum())
    kept = prices[~prices["sid"].isin(bad_sids)]
    share_dropped = 1 - len(kept) / max(1, len(prices))
    stats["non_equity_row_share"] = round(share_dropped, 4)
    if share_dropped > 0.30:
        print(
            f"WARNING: the equity-ISIN filter removed {share_dropped:.0%} of rows. "
            "Check equity_isin_regex in config/data.yaml against your files."
        )
    return kept


def _map_actions_to_trading_days(actions: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Move each action to the first day the stock actually traded on/after its ex-date."""
    if actions.empty:
        return actions.assign(date=pd.NaT)
    left = actions.sort_values("ex_date")
    right = prices[["symbol", "date"]].drop_duplicates().sort_values("date")
    mapped = pd.merge_asof(
        left, right, left_on="ex_date", right_on="date", by="symbol",
        direction="forward", tolerance=pd.Timedelta(days=45),
    )
    mapped = mapped.dropna(subset=["date"])
    if mapped.empty:
        return mapped
    # Two ex-dates can land on the same first trading day: combine them.
    agg = mapped.groupby(["symbol", "date"], as_index=False).agg(
        split_factor=("split_factor", "prod"),
        bonus_factor=("bonus_factor", "prod"),
        dividend=("dividend", "sum"),
        dividend_pct_fv=("dividend_pct_fv", "sum"),
        rights_new=("rights_new", "max"),
        rights_held=("rights_held", "max"),
        rights_premium=("rights_premium", "max"),
        demerger=("demerger", "any"),
        capital_change=("capital_change", "any"),
        purposes=("purposes", lambda s: " || ".join(s)),
    )
    return agg


def _compute_returns(df: pd.DataFrame, cfg: DataConfig) -> pd.DataFrame:
    """Daily price and total returns with corporate-action handling. See module docstring."""
    tol = cfg.price_ratio_tolerance
    close = df["close"].to_numpy(float)
    pc = df["prevclose"].to_numpy(float)
    cp = df["close_prev"].to_numpy(float)  # this company's previous traded close
    fv = df["face_value"].to_numpy(float)
    first = ~np.isfinite(cp)

    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.where((cp > 0) & (pc > 0), pc / cp, np.nan)  # factor implied by the exchange
    g_ok = np.isfinite(g) & (g > 0.01) & (g < 100)

    # --- factor implied by the corporate-action text -------------------------
    f_text = df["split_factor"].fillna(1.0).to_numpy(float) * df["bonus_factor"].fillna(1.0).to_numpy(float)
    r_new = df["rights_new"].fillna(0.0).to_numpy(float)
    r_held = df["rights_held"].fillna(0.0).to_numpy(float)
    issue_price = df["rights_premium"].to_numpy(float) + np.where(np.isfinite(fv), fv, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        terp = (r_held * cp + r_new * issue_price) / (r_held + r_new)
        f_rights = np.where((r_new > 0) & (r_held > 0) & np.isfinite(issue_price) & (issue_price < cp),
                            terp / cp, 1.0)
    f_rights = np.where(np.isfinite(f_rights), f_rights, 1.0)
    rights_unpriced = (r_new > 0) & ~np.isfinite(issue_price)
    f_ca = f_text * f_rights
    has_ca = np.abs(f_ca - 1.0) > 1e-9

    # --- dividend in rupees ----------------------------------------------------
    div = df["dividend"].fillna(0.0).to_numpy(float)
    div_pct = df["dividend_pct_fv"].fillna(0.0).to_numpy(float)
    div = div + np.where(np.isfinite(fv), div_pct / 100.0 * fv, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        div = np.where((cp > 0) & (div / cp > 0.5), 0.0, div)  # a "dividend" above half the price is a parse error

    # --- choose the base price for the day's return -----------------------------
    exch_adj = g_ok & (np.abs(g - 1.0) > tol)
    with np.errstate(divide="ignore", invalid="ignore"):
        div_like = exch_adj & (div > 0) & (np.abs(g - (1.0 - div / cp)) <= max(tol, 0.003))
        r_raw = close / cp - 1.0
        r_ca = close / (cp * f_ca) - 1.0
    use_exch = exch_adj & ~div_like
    # A large factor (split, bonus) must make the day's jump smaller, otherwise the text is
    # suspect (wrong ex-date, postponed action). A small factor (rights) cannot be judged
    # that way - an ordinary price move can outweigh it - so the exchange text is trusted.
    small_factor = np.abs(f_ca - 1.0) < 0.15
    ca_sane = has_ca & (small_factor | (np.abs(r_ca) < np.abs(r_raw)))
    use_ca = ~exch_adj & ca_sane

    base = cp.copy()
    base[use_exch] = pc[use_exch]
    base[use_ca] = cp[use_ca] * f_ca[use_ca]
    with np.errstate(divide="ignore", invalid="ignore"):
        ret_price = close / base - 1.0
        div_used = div if cfg.include_dividends else np.zeros_like(div)
        ret_total = (close + div_used) / base - 1.0

    # --- demergers: the price drop is a distribution, not a loss -----------------
    special = (df["demerger"].fillna(False).to_numpy(bool) | df["capital_change"].fillna(False).to_numpy(bool))
    neutral = np.zeros(len(df), bool)
    if cfg.demerger_treatment == "neutralise":
        neutral = special & ~exch_adj & ~has_ca & (r_raw < -0.10)
        ret_price[neutral] = 0.0
        ret_total[neutral] = 0.0

    ret_price[first] = np.nan  # listing day: the "previous close" is the issue price
    ret_total[first] = np.nan

    # --- label every non-trivial case for the audit log -------------------------
    with np.errstate(divide="ignore", invalid="ignore"):
        agree = np.abs(g / f_ca - 1.0) <= 0.03
    source = np.full(len(df), "", dtype=object)
    source[use_exch & has_ca & agree] = "exchange+ca_text"
    source[use_exch & has_ca & ~agree] = "exchange_vs_ca_text_mismatch"
    source[use_exch & ~has_ca] = "exchange_only"
    source[use_ca] = "ca_text_only"
    source[has_ca & ~exch_adj & ~ca_sane] = "ca_text_rejected"
    source[div_like] = "exchange_dividend_adjusted"
    source[neutral] = "demerger_neutralised"
    source[rights_unpriced & ~exch_adj] = "rights_not_adjusted"
    unexplained = (source == "") & ~first & (np.abs(ret_price) > 0.25)
    source[unexplained] = "unexplained_jump"
    source[first] = ""

    df["dividend"] = div
    df["adj_factor"] = np.where(first, 1.0, base / np.where(cp > 0, cp, np.nan))
    df["adj_source"] = source
    df["ret_price"] = ret_price
    df["ret_total"] = ret_total
    df["exchange_factor"] = g
    df["ca_factor"] = f_ca
    df["ret_raw"] = r_raw
    return df


def _spread_inputs(df: pd.DataFrame) -> pd.DataFrame:
    """Daily building blocks of two published bid-ask spread estimators.

    Both use only daily high, low and close, so they work on bhavcopy data:

    ``cs2d``    Corwin and Schultz (2012) two-day spread estimate. The daily
                high is (almost always) a buy and the low a sell, so the
                high-low range mixes volatility and spread; volatility grows
                with the horizon and the spread does not, and comparing one-day
                with two-day ranges separates them. Includes their adjustment
                for overnight gaps.
    ``arprod``  Abdi and Ranaldo (2017): product of (close - mid-range today)
                and (close - mid-range tomorrow), in logs. Four times its
                average is the squared spread.

    Rows are left blank unless the stock traded on two consecutive market days
    with no corporate-action adjustment between them. ``market.py`` averages
    these over a month to get the spread used in the cost model.
    """
    days = np.sort(df["date"].unique())
    pos = np.searchsorted(days, df["date"].to_numpy())
    prev = df["date_prev"].to_numpy()
    pos_prev = np.where(pd.isna(prev), -10, np.searchsorted(days, np.where(pd.isna(prev), days[0], prev)))
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))  # noqa: E741
    by = df.groupby("sid", sort=False)
    h1, l1, c1 = (by[k].shift(1).to_numpy(float) for k in ("high", "low", "close"))
    clean = ((pos - pos_prev) == 1) & (np.abs(df["adj_factor"].to_numpy(float) - 1.0) < 1e-9)
    clean &= (h > 0) & (l > 0) & (h1 > 0) & (l1 > 0) & (c1 > 0) & (h >= l) & (h1 >= l1)

    with np.errstate(divide="ignore", invalid="ignore"):
        # Corwin-Schultz, with the overnight-gap adjustment of today's range
        up = np.maximum(l - c1, 0.0)
        down = np.maximum(c1 - h, 0.0)
        h_adj, l_adj = h - up + down, l - up + down
        beta = np.log(h1 / l1) ** 2 + np.log(h_adj / l_adj) ** 2
        gamma = np.log(np.maximum(h1, h_adj) / np.minimum(l1, l_adj)) ** 2
        k = 3.0 - 2.0 * np.sqrt(2.0)
        alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
        cs = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
        # Abdi-Ranaldo
        eta, eta1 = (np.log(h) + np.log(l)) / 2.0, (np.log(h1) + np.log(l1)) / 2.0
        ar = (np.log(c1) - eta1) * (np.log(c1) - eta)
    df["cs2d"] = np.where(clean, cs, np.nan).astype(np.float32)
    df["arprod"] = np.where(clean, ar, np.nan).astype(np.float32)
    return df


def _apply_overrides(df: pd.DataFrame, cfg: DataConfig, stats: dict) -> pd.DataFrame:
    """Optional hand corrections: ``data/external/return_overrides.csv``.

    Columns: ``symbol``, ``date``, ``ret_total`` (and optionally ``ret_price``),
    ``reason``. Use it for the few events the automatic logic flags as
    ``unexplained_jump`` once you have checked them against the exchange notice.
    """
    path = cfg.external / "return_overrides.csv"
    if not path.exists():
        stats["return_overrides_applied"] = 0
        return df
    ov = pd.read_csv(path, parse_dates=["date"])
    ov["symbol"] = ov["symbol"].astype(str).str.strip().str.upper()
    if "ret_price" not in ov.columns:
        ov["ret_price"] = ov["ret_total"]
    df = df.merge(ov[["symbol", "date", "ret_total", "ret_price"]], on=["symbol", "date"], how="left",
                  suffixes=("", "_ov"))
    hit = df["ret_total_ov"].notna()
    df.loc[hit, "ret_total"] = df.loc[hit, "ret_total_ov"]
    df.loc[hit, "ret_price"] = df.loc[hit, "ret_price_ov"].fillna(df.loc[hit, "ret_total_ov"])
    df.loc[hit, "adj_source"] = "manual_override"
    stats["return_overrides_applied"] = int(hit.sum())
    return df.drop(columns=["ret_total_ov", "ret_price_ov"])


def _cumulative_index(first_close: pd.Series, ret: pd.Series, sid: pd.Series) -> np.ndarray:
    """Price-scaled index: starts at the first close and compounds the returns."""
    log_ret = np.log1p(ret.fillna(0.0).clip(lower=-0.999999))
    return (first_close * np.exp(log_ret.groupby(sid, sort=False).cumsum())).to_numpy()


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def build_panel(cfg: DataConfig, verbose: bool = True) -> Dict[str, Path]:
    """Build the processed panel and its audit files. Returns the output paths."""
    out_dir = cfg.processed
    (out_dir / "panel").mkdir(parents=True, exist_ok=True)
    stats: dict = {"data_kind": cfg.data_kind}

    prices = read_interim(cfg, "prices")
    if prices.empty:
        raise FileNotFoundError(
            f"No parsed prices under {cfg.interim / 'prices'}. Run the download and ingest steps first."
        )
    prices = prices[prices["series"].isin(cfg.equity_series)].copy()
    prices["isin"] = prices["isin"].fillna("").astype(str)
    stats["raw_price_rows"] = int(len(prices))

    # 1-2. identity -------------------------------------------------------------
    sc_path = cfg.interim / "symbol_changes.parquet"
    symbol_changes = to_ns(pd.read_parquet(sc_path)) if sc_path.exists() else None
    prices = assign_security_ids(prices, symbol_changes, cfg.symbol_reuse_gap_days)
    prices = _equity_only(prices, cfg, stats)
    prices, dropped = resolve_duplicates(prices)
    stats["duplicate_rows_dropped"] = int(len(dropped))
    df = prices.sort_values(["sid", "date"]).reset_index(drop=True)

    # shares outstanding / face value / market cap, as published that day ----------
    mcap = read_interim(cfg, "mcap")
    if len(mcap):
        mcap = mcap.sort_values(["symbol", "date", "shares"]).drop_duplicates(["symbol", "date"], keep="last")
        df = df.merge(mcap[["symbol", "date", "shares", "face_value"]], on=["symbol", "date"], how="left")
    else:
        df["shares"] = np.nan
        df["face_value"] = np.nan
    by_sid = df.groupby("sid", sort=False)
    stats["share_of_rows_with_published_shares"] = round(float(df["shares"].notna().mean()), 4)
    df["shares"] = by_sid["shares"].ffill(limit=cfg.shares_ffill_days)
    df["face_value"] = by_sid["face_value"].ffill(limit=cfg.shares_ffill_days)
    df["mcap"] = df["close"] * df["shares"]

    # 3. corporate actions and returns ---------------------------------------------
    listings = read_interim(cfg, "corp_actions")
    actions = build_action_table(listings, cfg.equity_series)
    stats["corporate_action_events"] = int(len(actions))
    stats["corporate_action_lines_not_understood"] = int(actions["n_unrecognised"].sum()) if len(actions) else 0
    mapped = _map_actions_to_trading_days(actions, df)
    ca_cols = ["split_factor", "bonus_factor", "dividend", "dividend_pct_fv", "rights_new", "rights_held",
               "rights_premium", "demerger", "capital_change", "purposes"]
    if len(mapped):
        df = df.merge(mapped[["symbol", "date"] + ca_cols], on=["symbol", "date"], how="left")
    else:
        for c in ca_cols:
            df[c] = np.nan
    df = df.sort_values(["sid", "date"]).reset_index(drop=True)
    by_sid = df.groupby("sid", sort=False)
    df["close_prev"] = by_sid["close"].shift(1)
    df["date_prev"] = by_sid["date"].shift(1)
    df = _compute_returns(df, cfg)
    df = _spread_inputs(df)
    df = _apply_overrides(df, cfg, stats)

    # 4. indices ---------------------------------------------------------------------
    first_close = df.groupby("sid", sort=False)["close"].transform("first")
    df["tri"] = _cumulative_index(first_close, df["ret_total"], df["sid"])
    df["pri"] = _cumulative_index(first_close, df["ret_price"], df["sid"])

    # volume-weighted average price of the day (execution price candidate) -------------
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = df["value"] / df["volume"]
    inside = vwap.between(df["low"] * 0.999, df["high"] * 1.001)
    df["vwap"] = vwap.where(inside & (df["volume"] > 0), df["close"])

    # 5. exchange-reported price-band hits ----------------------------------------------
    hits = read_interim(cfg, "band_hits")
    if len(hits):
        hits = hits.drop_duplicates(["symbol", "date"])
        df = df.merge(hits[["symbol", "date", "hit"]], on=["symbol", "date"], how="left")
        df["band_hit"] = df["hit"].fillna("")
        stats["band_hit_file_days"] = int(hits["date"].nunique())
    else:
        df["band_hit"] = ""
        stats["band_hit_file_days"] = 0
    # +1 = pinned at the upper band at the close (cannot buy), -1 = lower band (cannot sell)
    df["lock"] = circuit_lock(df["close"], df["high"], df["low"], df["prevclose"], df["band_hit"])
    stats["locked_up_rows"] = int((df["lock"] == 1).sum())
    stats["locked_down_rows"] = int((df["lock"] == -1).sum())

    # ---- audit log: every return that is not a plain close-to-close move ------------------
    log_cols = ["date", "sid", "symbol", "adj_source", "close_prev", "prevclose", "close",
                "exchange_factor", "ca_factor", "dividend", "ret_raw", "ret_price", "purposes"]
    audit = df.loc[df["adj_source"] != "", log_cols].copy()
    audit.to_csv(out_dir / "adjustment_log.csv", index=False)
    stats["adjustments_by_source"] = audit["adj_source"].value_counts().to_dict()
    stats["dividend_events_used"] = int((df["dividend"] > 0).sum())

    # ---- write ---------------------------------------------------------------------------------
    df = df[PANEL_COLUMNS].sort_values(["date", "sid"]).reset_index(drop=True)
    for year, part in df.groupby(df["date"].dt.year):
        part.to_parquet(out_dir / "panel" / f"panel_{year}.parquet", index=False)
    master = security_master(df)
    master = _attach_sector(master, cfg)
    master.to_parquet(out_dir / "securities.parquet", index=False)

    stats.update(
        first_date=str(df["date"].min().date()),
        last_date=str(df["date"].max().date()),
        trading_days=int(df["date"].nunique()),
        panel_rows=int(len(df)),
        companies=int(master.shape[0]),
        companies_with_renames=int(master["symbols"].str.contains(r"\|").sum()),
        companies_with_isin_changes=int(master["isins"].str.contains(r"\|").sum()),
        companies_without_sector=int((master["sector"] == "UNKNOWN").sum()),
    )
    (out_dir / "panel_stats.json").write_text(json.dumps(stats, indent=1, default=str))
    if verbose:
        print(f"Panel built: {stats['panel_rows']:,} rows, {stats['companies']:,} companies, "
              f"{stats['first_date']} to {stats['last_date']}")
    return {
        "panel": out_dir / "panel",
        "securities": out_dir / "securities.parquet",
        "adjustment_log": out_dir / "adjustment_log.csv",
        "stats": out_dir / "panel_stats.json",
    }


def _attach_sector(master: pd.DataFrame, cfg: DataConfig) -> pd.DataFrame:
    """Sector per company: the group's own map first, then NSE's current industry labels.

    A sector label is a slowly-changing attribute, so using the latest known
    label for past dates is a mild and standard approximation; it is listed
    under limitations. Companies with no label are put in ``UNKNOWN``.
    """
    from .external import load_sector_map  # local import: avoids a circular import at module load

    sector_map = load_sector_map(cfg)
    master["sector"] = "UNKNOWN"
    if sector_map.empty:
        return master
    by_isin = sector_map[sector_map["isin"] != ""].drop_duplicates("isin").set_index("isin")["sector"]
    by_symbol = sector_map[sector_map["symbol"] != ""].drop_duplicates("symbol").set_index("symbol")["sector"]

    def lookup(row) -> str:
        for isin in str(row["isins"]).split("|"):
            if isin in by_isin.index:
                return by_isin[isin]
        for sym in reversed(str(row["symbols"]).split("|")):
            if sym in by_symbol.index:
                return by_symbol[sym]
        return "UNKNOWN"

    master["sector"] = master.apply(lookup, axis=1)
    return master


def load_panel(
    cfg: DataConfig,
    start: Optional[str] = None,
    end: Optional[str] = None,
    columns: Optional[list] = None,
) -> pd.DataFrame:
    """Read the processed panel (optionally a date range / subset of columns)."""
    files = sorted((cfg.processed / "panel").glob("panel_*.parquet"))
    if not files:
        raise FileNotFoundError(f"No panel under {cfg.processed / 'panel'}. Run the build step first.")
    lo = pd.Timestamp(start).year if start else None
    hi = pd.Timestamp(end).year if end else None
    frames = []
    for f in files:
        year = int(f.stem.split("_")[1])
        if (lo and year < lo) or (hi and year > hi):
            continue
        frames.append(pd.read_parquet(f, columns=columns))
    df = to_ns(pd.concat(frames, ignore_index=True))
    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["date"] <= pd.Timestamp(end)]
    return df.reset_index(drop=True)


def load_securities(cfg: DataConfig) -> pd.DataFrame:
    return to_ns(pd.read_parquet(cfg.processed / "securities.parquet"))
