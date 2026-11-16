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
   The exchange's daily market-cap file starts in January 2024; for earlier
   days the market cap is carried forward from the exchange's latest
   market-cap list (``size.py``). Each figure is labelled with its source.

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
from ..frictions import TICK, circuit_lock
from ..utils import to_ns
from .corporate_actions import build_action_table
from .identifiers import assign_security_ids, resolve_duplicates, security_master
from .ingest import read_interim
from .size import build_index_members, build_size_lists, estimate_mcap

LARGE_DIVIDEND = 0.20  # share of the previous close above which a dividend must explain the price fall
JUMP_MIN_TICKS = 3  # an unexplained move must exceed this many ticks to be flagged for review

PANEL_COLUMNS = [
    "date", "sid", "symbol", "series", "isin",
    "open", "high", "low", "close", "prevclose", "vwap",
    "volume", "value", "trades",
    "ret_total", "ret_price", "tri", "pri",
    "dividend", "adj_factor", "adj_source",
    "shares", "face_value", "mcap", "mcap_src", "band_hit", "lock", "cs2d", "arprod",
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
        split_unknown=("split_unknown", "any"),
        bonus_unknown=("bonus_unknown", "any"),
        old_face_value=("old_face_value", "max"),
        new_face_value=("new_face_value", "min"),
        dividend_unstated=("dividend_unstated", "any"),
        purposes=("purposes", lambda s: " || ".join(s)),
    )
    return agg


def _face_values(df: pd.DataFrame, cfg: DataConfig, stats: dict) -> np.ndarray:
    """Face value of the share on every row (NaN where it cannot be established).

    Needed for two things only: a rights issue is priced "at a premium of Rs x",
    i.e. x plus the face value, and some dividends are quoted as a percentage of
    face value. The exchange's daily file gives the face value from 2024. For
    earlier days it is rebuilt from facts that carry no look-ahead:

    * a face-value change always brings a new ISIN, so the face value of an
      ISIN (from the daily file, or from the exchange's list of listed
      securities) holds for every day that ISIN traded;
    * going back in time across a split, the face value is divided by the
      split factor (new / old); a split text naming both values anchors it.
    """
    by_isin: Dict[str, float] = {}
    path = cfg.interim / "equity_list.parquet"
    if path.exists():
        el = pd.read_parquet(path)
        by_isin.update(zip(el["isin"], el["face_value"]))
    pub = df.loc[df["face_value"].notna() & (df["isin"] != ""), ["isin", "face_value"]].drop_duplicates("isin", keep="last")
    by_isin.update(zip(pub["isin"], pub["face_value"]))

    known = df["face_value"].where(df["face_value"].notna(), df["isin"].map(by_isin))
    # A split names its values in the text: on the ex-date the share has the new one.
    from_text = df["new_face_value"].where(df["split_factor"].notna() & (df["split_factor"] != 1.0))
    factor = df["split_factor"].fillna(1.0).clip(lower=1e-6)
    log_cum = np.log(factor).groupby(df["sid"], sort=False).cumsum()  # cumulative split factor, ex-date included

    def carry(anchor: pd.Series) -> pd.Series:
        """Spread each company's latest anchored value over its other rows along the split chain."""
        ref_fv = anchor.groupby(df["sid"], sort=False).transform("last")  # 'last' skips blanks
        ref_cum = log_cum.where(anchor.notna()).groupby(df["sid"], sort=False).transform("last")
        return ref_fv * np.exp(log_cum - ref_cum)

    fv = carry(known)
    fv = fv.where(fv.notna(), carry(from_text))
    stats["share_of_rows_with_face_value"] = round(float(fv.notna().mean()), 4)
    return fv.round(4).to_numpy(float)


# Ratios tried when a split or bonus text was cut off before its ratio (see _infer_cut_off_ratios).
_FACE_VALUES = (10.0, 5.0, 2.0, 1.0, 0.5, 0.25, 0.2, 0.1)
_SPLIT_FACTORS = (0.5, 0.2, 0.1, 0.4, 0.25, 0.05, 0.02, 0.01)
_BONUS_FACTORS = tuple(sorted({b / (a + b) for a, b in [
    (1, 1), (1, 2), (2, 1), (1, 3), (3, 1), (1, 4), (4, 1), (1, 5), (5, 1), (2, 3), (3, 2), (1, 10), (3, 5), (2, 5),
    (1, 6), (1, 9), (4, 5), (5, 4), (3, 4), (1, 8), (1, 7), (3, 10), (1, 20)]}))
INFER_MAX_RESIDUAL = 0.12  # the price must land this close to "previous close x ratio" for a ratio to be accepted


def _infer_cut_off_ratios(df: pd.DataFrame, f_text: np.ndarray, close: np.ndarray, cp: np.ndarray) -> np.ndarray:
    """Recover a split or bonus ratio that the exchange's text was too short to hold.

    "FV SPLIT FROM RS10 TO RS" and "AGM/DIV-RS 29.50/BONUS" announce an event
    without its ratio. Splits and bonuses come in a small set of standard
    ratios, so the one that brings the ex-date price closest to the previous
    close is taken, and only if it lands within ``INFER_MAX_RESIDUAL`` of it
    and does better than no adjustment. Every such row is labelled
    ``ca_ratio_inferred`` in the audit log so it can be checked against the
    company's announcement. Returns a mask of the rows where a ratio was set;
    ``f_text`` is updated in place.
    """
    split_q = df["split_unknown"].eq(True).to_numpy()
    bonus_q = df["bonus_unknown"].eq(True).to_numpy()
    old_fv = df["old_face_value"].to_numpy(float)
    inferred = np.zeros(len(df), bool)
    for i in np.where((split_q | bonus_q) & np.isfinite(cp) & (cp > 0) & np.isfinite(close))[0]:
        splits = [1.0]
        if split_q[i]:
            splits = ([v / old_fv[i] for v in _FACE_VALUES if v < old_fv[i]] if np.isfinite(old_fv[i]) and old_fv[i] > 0
                      else list(_SPLIT_FACTORS))
        bonuses = list(_BONUS_FACTORS) if bonus_q[i] else [1.0]
        candidates = [a * b for a in splits for b in bonuses]
        if not candidates:
            continue
        residual = [abs(close[i] / (cp[i] * f_text[i] * c) - 1.0) for c in candidates]
        best = int(np.argmin(residual))
        if residual[best] <= INFER_MAX_RESIDUAL and residual[best] < abs(close[i] / (cp[i] * f_text[i]) - 1.0):
            f_text[i] *= candidates[best]
            inferred[i] = True
    return inferred


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
    inferred = _infer_cut_off_ratios(df, f_text, close, cp)
    r_new = df["rights_new"].fillna(0.0).to_numpy(float)
    r_held = df["rights_held"].fillna(0.0).to_numpy(float)
    premium = df["rights_premium"].to_numpy(float)
    # Issue price = premium + face value. Where the face value cannot be established and a premium
    # is stated, the premium alone is used: the issue price is then understated by the face value
    # (Rs 10 at most), a small error next to leaving the ex-rights fall in the return.
    fv_assumed = (r_new > 0) & np.isfinite(premium) & (premium > 0) & ~np.isfinite(fv)
    issue_price = premium + np.where(np.isfinite(fv), fv, np.where(fv_assumed, 0.0, np.nan))
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
    pct_without_fv = (div_pct > 0) & ~np.isfinite(fv)

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
        # A very large dividend is real often enough (special dividends of half the share price and
        # more occur) but is also what a misread amount looks like. A real one explains the fall in
        # the price; a misread one creates a jump. Above LARGE_DIVIDEND of the price the dividend is
        # used only if adding it back brings the day's return closer to zero.
        large = (cp > 0) & (div / cp > LARGE_DIVIDEND)
        div_rejected = large & ~(np.abs((close + div) / base - 1.0) < np.abs(ret_price))
        div = np.where(div_rejected, 0.0, div)
        div_used = div if cfg.include_dividends else np.zeros_like(div)
        ret_total = (close + div_used) / base - 1.0

    # --- demergers: the price drop is a distribution, not a loss -----------------
    special = df["demerger"].eq(True).to_numpy() | df["capital_change"].eq(True).to_numpy()
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
    source[use_ca & fv_assumed] = "ca_text_rights_face_value_unknown"
    source[use_ca & inferred] = "ca_ratio_inferred"
    source[has_ca & ~exch_adj & ~ca_sane] = "ca_text_rejected"
    source[div_like] = "exchange_dividend_adjusted"
    source[neutral] = "demerger_neutralised"
    source[rights_unpriced & ~exch_adj] = "rights_not_adjusted"
    source[(source == "") & div_rejected] = "large_dividend_rejected"
    source[(source == "") & large & ~div_rejected] = "large_dividend"
    # A move of a few ticks on a share priced under a rupee is a large percentage but needs no
    # explanation (Rs 0.15 to Rs 0.20 is one tick and 33%); only larger moves are flagged.
    unexplained = ((source == "") & ~first & (np.abs(ret_price) > 0.25)
                   & (np.abs(close - base) > JUMP_MIN_TICKS * TICK + 1e-9))
    source[unexplained] = "unexplained_jump"
    source[first] = ""
    df["dividend_pct_without_face_value"] = pct_without_fv

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

    # 3. corporate actions and returns ---------------------------------------------
    listings = read_interim(cfg, "corp_actions")
    actions = build_action_table(listings, cfg.equity_series)
    stats["corporate_action_events"] = int(len(actions))
    stats["corporate_action_lines_not_understood"] = int(actions["n_unrecognised"].sum()) if len(actions) else 0
    mapped = _map_actions_to_trading_days(actions, df)
    ca_cols = ["split_factor", "bonus_factor", "dividend", "dividend_pct_fv", "rights_new", "rights_held",
               "rights_premium", "demerger", "capital_change", "split_unknown", "bonus_unknown",
               "old_face_value", "new_face_value", "dividend_unstated", "purposes"]
    if len(mapped):
        df = df.merge(mapped[["symbol", "date"] + ca_cols], on=["symbol", "date"], how="left")
    else:
        for c in ca_cols:
            df[c] = np.nan
    df = df.sort_values(["sid", "date"]).reset_index(drop=True)
    by_sid = df.groupby("sid", sort=False)
    df["close_prev"] = by_sid["close"].shift(1)
    df["date_prev"] = by_sid["date"].shift(1)
    df["face_value"] = _face_values(df, cfg, stats)
    df = _compute_returns(df, cfg)
    stats["dividend_events_without_an_amount"] = int(df["dividend_unstated"].eq(True).sum())
    stats["dividends_in_percent_without_face_value"] = int(df["dividend_pct_without_face_value"].sum())
    df = _spread_inputs(df)
    df = _apply_overrides(df, cfg, stats)

    # 4. indices ---------------------------------------------------------------------
    first_close = df.groupby("sid", sort=False)["close"].transform("first")
    df["tri"] = _cumulative_index(first_close, df["ret_total"], df["sid"])
    df["pri"] = _cumulative_index(first_close, df["ret_price"], df["sid"])

    # market cap: the daily file where it exists, else the latest exchange list carried forward
    from .external import load_amfi_lists  # local import: avoids a circular import at module load

    size_lists = build_size_lists(cfg, df, read_interim(cfg, "mcap_lists"), load_amfi_lists(cfg), stats)
    df["mcap"], df["mcap_src"] = estimate_mcap(df, size_lists, cfg.size_list_max_age_days, stats)
    index_members = build_index_members(df, read_interim(cfg, "index_weights"), stats)

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
    size_lists.to_parquet(out_dir / "size_lists.parquet", index=False)
    index_members.to_parquet(out_dir / "index_members.parquet", index=False)

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


def load_size_lists(cfg: DataConfig) -> pd.DataFrame:
    """Exchange / AMFI size lists keyed by company id (empty if the panel was built without any)."""
    path = cfg.processed / "size_lists.parquet"
    return to_ns(pd.read_parquet(path)) if path.exists() else pd.DataFrame()


def load_index_members(cfg: DataConfig) -> pd.DataFrame:
    """Monthly Nifty 50 / Nifty Next 50 members keyed by company id (empty if none were downloaded)."""
    path = cfg.processed / "index_members.parquet"
    return to_ns(pd.read_parquet(path)) if path.exists() else pd.DataFrame()
