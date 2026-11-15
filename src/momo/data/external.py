"""Loaders for the files the project group supplies under ``data/external``.

Each loader accepts a simple, documented CSV layout (see docs/01_data_requirements.md),
is forgiving about column-name spelling, and returns an empty object when the
file is absent so the pipeline can say clearly what is missing instead of crashing.

==============================  ==============================================
file (under data/external)      purpose
==============================  ==============================================
risk_free.csv                   91-day T-bill yield (RBI DBIE) -> Sharpe, cash return
factors/*.csv                   India factor returns (e.g. IIMA library)
benchmarks/*.csv                Total-return index history (niftyindices.com)
index_constituents/*.csv        Historical index membership (point-in-time)
amfi/*.xlsx                     AMFI half-yearly lists of average market cap (size categories)
mcap_lists/*.xlsx               Extra NSE market-cap lists saved by hand (read by ingest)
sector_map.csv                  Sector per company, incl. delisted names
surveillance.csv                ASM / GSM history
delistings.csv                  Why a stock stopped trading, and what holders got
return_overrides.csv            Hand corrections to flagged data errors
==============================  ==============================================
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from ..config import DataConfig


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [re.sub(r"[^a-z0-9]+", "_", str(c).lower()).strip("_") for c in df.columns]
    return df


def _find(df: pd.DataFrame, *keywords: str) -> Optional[str]:
    """First column whose normalised name contains any of the keywords."""
    for kw in keywords:
        for c in df.columns:
            if kw in c:
                return c
    return None


def _read_csv(path: Path) -> pd.DataFrame:
    return _norm_cols(pd.read_csv(path, dtype=str, skipinitialspace=True))


def _to_date(col: pd.Series) -> pd.Series:
    """Parse dates given as ISO (YYYY-MM-DD) or day-first (DD-MM-YYYY, DD Mon YYYY)."""
    text = col.astype(str).str.strip()
    iso = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    other = pd.to_datetime(text.where(iso.isna()), dayfirst=True, errors="coerce", format="mixed")
    return iso.fillna(other).astype("datetime64[ns]")


def _to_decimal(col: pd.Series, name: str, verbose: bool = True) -> pd.Series:
    """Numbers given in percent become decimals. Heuristic: |median| above 0.5 means percent."""
    x = pd.to_numeric(col.astype(str).str.replace(",", "").str.replace("%", ""), errors="coerce")
    if x.abs().median() > 0.5:
        if verbose:
            print(f"  [{name}] values look like percentages -> divided by 100")
        x = x / 100.0
    return x


# --------------------------------------------------------------------------- #
# Risk-free rate
# --------------------------------------------------------------------------- #
def load_risk_free(cfg: DataConfig, verbose: bool = True) -> pd.Series:
    """Annualised risk-free yield as a decimal, indexed by date (as published).

    Expected file: ``data/external/risk_free.csv`` with a date column and a
    yield column (percent or decimal). RBI DBIE's 91-day Treasury-bill auction
    yield is the source the project brief suggests. The series is weekly; the
    pipeline forward-fills it, so each day uses the latest *published* yield.
    """
    path = cfg.external / "risk_free.csv"
    if not path.exists():
        return pd.Series(dtype=float, name="rf_annual")
    df = _read_csv(path)
    dcol = _find(df, "date", "week", "period")
    ycol = _find(df, "yield", "rate", "rf", "tbill", "t_bill", "91")
    if dcol is None or ycol is None or dcol == ycol:
        raise ValueError(f"{path}: need a date column and a yield column; found {list(df.columns)}")
    out = pd.Series(_to_decimal(df[ycol], "risk_free", verbose).to_numpy(), index=_to_date(df[dcol]), name="rf_annual")
    out = out[out.index.notna()].dropna().sort_index()
    return out[~out.index.duplicated(keep="last")]


def daily_risk_free(rf_annual: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """Risk-free *return* earned over each trading day (actual/365 on calendar days).

    The yield known at the previous trading day's close is applied to the
    calendar days up to today, so weekends and holidays accrue interest too.
    Returns zeros (with a warning to the caller via ``attrs``) if no yield is supplied.
    """
    out = pd.Series(0.0, index=dates, name="rf_daily")
    if rf_annual is None or rf_annual.empty:
        out.attrs["missing"] = True
        return out
    annual = rf_annual.reindex(rf_annual.index.union(dates)).sort_index().ffill().reindex(dates)
    annual = annual.bfill()  # only affects dates before the first published yield
    gap_days = pd.Series(dates, index=dates).diff().dt.days.fillna(1.0)
    out[:] = (annual.shift(1).fillna(annual) * gap_days / 365.0).to_numpy()
    out.attrs["missing"] = False
    return out


# --------------------------------------------------------------------------- #
# Factor returns and benchmark total-return indices
# --------------------------------------------------------------------------- #
_FACTOR_ALIASES = {
    "mkt_rf": ("mkt_rf", "mktrf", "mf", "rm_rf", "rmrf", "market", "mkt"),
    "smb": ("smb",),
    "hml": ("hml",),
    "wml": ("wml", "mom", "umd"),
    "rf": ("rf", "risk_free", "riskfree"),
}


def load_factors(cfg: DataConfig, verbose: bool = True) -> Dict[str, pd.DataFrame]:
    """Factor returns from ``data/external/factors/*.csv``, as decimals.

    Returns ``{"daily": DataFrame, "monthly": DataFrame}`` (either may be
    missing). Frequency is inferred from the spacing of the dates. Columns are
    renamed to ``mkt_rf``, ``smb``, ``hml``, ``wml``, ``rf`` where recognised.
    """
    folder = cfg.external / "factors"
    out: Dict[str, pd.DataFrame] = {}
    if not folder.exists():
        return out
    for path in sorted(folder.glob("*.csv")):
        df = _read_csv(path)
        dcol = _find(df, "date", "month", "period")
        if dcol is None:
            continue
        idx = _to_date(df[dcol])
        cols = {}
        for canon, aliases in _FACTOR_ALIASES.items():
            for c in df.columns:
                if c != dcol and c in aliases:
                    cols[canon] = _to_decimal(df[c], f"factors:{path.name}:{canon}", verbose)
                    break
        if not cols:
            continue
        frame = pd.DataFrame(cols).set_index(idx)
        frame = frame[frame.index.notna()].sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        spacing = pd.Series(frame.index).diff().dt.days.median()
        out["monthly" if spacing and spacing > 20 else "daily"] = frame
    return out


def load_benchmark_tri(cfg: DataConfig) -> pd.DataFrame:
    """Total-return index levels from ``data/external/benchmarks/*.csv``.

    One file per index (as downloaded from niftyindices.com, "Total Returns
    Index" history). The index name is taken from an "Index Name" column if
    present, otherwise from the file name. Returns dates x index names.
    """
    folder = cfg.external / "benchmarks"
    series = {}
    if not folder.exists():
        return pd.DataFrame()
    for path in sorted(folder.glob("*.csv")):
        df = _read_csv(path)
        dcol = _find(df, "date")
        vcol = _find(df, "total_return", "tri", "close", "value")
        if dcol is None or vcol is None:
            continue
        ncol = _find(df, "index_name", "index")
        name = (str(df[ncol].dropna().iloc[0]) if ncol and ncol not in (dcol, vcol) else path.stem)
        name = re.sub(r"\s+", " ", name.replace("_", " ")).strip().upper()
        s = pd.Series(pd.to_numeric(df[vcol].astype(str).str.replace(",", ""), errors="coerce").to_numpy(),
                      index=_to_date(df[dcol]))
        s = s[s.index.notna()].dropna().sort_index()
        series[name] = s[~s.index.duplicated(keep="last")]
    return pd.DataFrame(series).sort_index()


# --------------------------------------------------------------------------- #
# Universe-related files
# --------------------------------------------------------------------------- #
def load_index_constituents(cfg: DataConfig) -> pd.DataFrame:
    """Historical index membership: ``index_name``, ``symbol``, ``isin``, ``start``, ``end``.

    Files in ``data/external/index_constituents``: one row per membership spell,
    with a blank end date meaning "still a member". Using *today's* list for
    past dates is exactly the look-ahead the project brief forbids, so this
    loader insists on a start date for every row.
    """
    folder = cfg.external / "index_constituents"
    frames = []
    if folder.exists():
        for path in sorted(folder.glob("*.csv")):
            df = _read_csv(path)
            sym, isin = _find(df, "symbol", "ticker"), _find(df, "isin")
            start = _find(df, "start", "from", "inclusion", "added")
            end = _find(df, "end", "to_date", "exclusion", "removed")
            idx = _find(df, "index")
            if (sym is None and isin is None) or start is None:
                raise ValueError(f"{path}: need symbol/isin and a start date column; found {list(df.columns)}")
            frames.append(pd.DataFrame({
                "index_name": (df[idx].astype(str).str.strip().str.upper() if idx else path.stem.upper()),
                "symbol": df[sym].fillna("").astype(str).str.strip().str.upper() if sym else "",
                "isin": df[isin].fillna("").astype(str).str.strip().str.upper() if isin else "",
                "start": _to_date(df[start]),
                "end": _to_date(df[end]) if end else pd.NaT,
            }))
    if not frames:
        return pd.DataFrame(columns=["index_name", "symbol", "isin", "start", "end"])
    out = pd.concat(frames, ignore_index=True)
    if out["start"].isna().any():
        raise ValueError("index_constituents: every row needs a start date (point-in-time membership)")
    return out


AMFI_COLUMNS = ["as_of", "isin", "symbol", "company", "rank", "mcap", "category"]
_MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE_IN_TEXT = [
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "ymd"),
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s_\-]*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s_\-,]*(\d{4})", re.I), "dMy"),
    (re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s_\-]*(\d{1,2})[\s_\-,]+(\d{4})", re.I), "Mdy"),
]


def _date_in_text(text: str) -> Optional[dt.date]:
    """A date written inside a file name or a title line ('...ended 31 Dec 2023', '2023-12-31')."""
    for pattern, style in _DATE_IN_TEXT:
        m = pattern.search(str(text))
        if not m:
            continue
        try:
            if style == "ymd":
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if style == "dMy":
                return dt.date(int(m.group(3)), _MONTH_NUM[m.group(2).lower()[:3]], int(m.group(1)))
            return dt.date(int(m.group(3)), _MONTH_NUM[m.group(1).lower()[:3]], int(m.group(2)))
        except (ValueError, KeyError):
            continue
    return None


def parse_amfi_list(path: Path) -> pd.DataFrame:
    """One AMFI "average market capitalisation of listed companies" file -> ``AMFI_COLUMNS``.

    AMFI publishes, after every June and December, the six-month average
    market capitalisation of every listed company and its category under
    SEBI's rule (large = ranks 1-100, mid = 101-250, small = 251 onwards).
    ``mcap`` is the average across exchanges, in rupees; ``rank`` is the order
    in the file. The half-year end is read from the file name (for example
    ``...31Dec2023.xlsx``) or, failing that, from the title lines of the sheet;
    rename the file to ``amfi_2023-12-31.xlsx`` if neither carries it.
    """
    from .parsers import _grid, _norm, _num  # same tolerant readers as the exchange files

    grid = _grid(Path(path))
    header_at = None
    for i in range(min(25, len(grid))):
        if any("ISIN" in _norm(v) for v in grid.iloc[i].tolist() if isinstance(v, str)):
            header_at = i
            break
    if header_at is None:
        raise ValueError(f"{path}: no header row with an ISIN column - is this the AMFI list?")
    as_of = _date_in_text(Path(path).stem)
    for i in range(header_at):  # title lines above the table
        if as_of is not None:
            break
        as_of = _date_in_text(" ".join(str(v) for v in grid.iloc[i].tolist() if isinstance(v, str)))
    if as_of is None:
        raise ValueError(f"{path}: cannot tell which half-year this list is for. Put the half-year end in "
                         "the file name, e.g. amfi_2023-12-31.xlsx")

    names = [_norm(v) if isinstance(v, str) else "" for v in grid.iloc[header_at].tolist()]
    body = grid.iloc[header_at + 1:].reset_index(drop=True)
    body.columns = range(body.shape[1])

    def col(*tokens: str, without: tuple = ()) -> Optional[int]:
        for j, name in enumerate(names):
            if name and all(t in name for t in tokens) and not any(w in name for w in without):
                return j
        return None

    c_isin = col("ISIN")
    c_name = col("COMPANY") if col("COMPANY") is not None else col("NAME")
    c_avg = col("AVERAGE", "EXCHANGE")  # "Average of All Exchanges (Rs. Cr.)"
    if c_avg is None:
        c_avg = col("NSE", "MARKET", "CAP")
    c_cat = col("CATEGOR")
    c_sym = col("NSE", "SYMBOL")
    if c_avg is None:
        raise ValueError(f"{path}: no 'Average of All Exchanges' (or NSE market cap) column; found {names}")
    scale = 1e5 if "LAKH" in names[c_avg] else 1e7  # AMFI publishes in Rs crore

    out = pd.DataFrame({
        "as_of": pd.Timestamp(as_of),
        "isin": body[c_isin].fillna("").astype(str).str.strip().str.upper(),
        "symbol": body[c_sym].fillna("").astype(str).str.strip().str.upper() if c_sym is not None else "",
        "company": body[c_name].fillna("").astype(str).str.strip() if c_name is not None else "",
        "mcap": _num(body[c_avg]) * scale,
        "category": body[c_cat].fillna("").astype(str).str.strip() if c_cat is not None else "",
    })
    out = out[out["isin"].str.match(r"^IN[A-Z0-9]{10}$") & out["mcap"].notna()]
    out = out.drop_duplicates("isin").reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1, dtype=float)
    if out.empty:
        raise ValueError(f"{path}: no company rows with an ISIN and an average market cap")
    out["as_of"] = out["as_of"].astype("datetime64[ns]")
    return out[AMFI_COLUMNS]


def load_amfi_lists(cfg: DataConfig) -> pd.DataFrame:
    """Every AMFI list under ``data/external/amfi`` (xlsx, xls or csv) -> ``AMFI_COLUMNS``.

    Empty when the folder is absent. A file that cannot be read stops the run
    with the reason: a size list that silently went missing would change the
    universe without anyone noticing.
    """
    folder = cfg.external / "amfi"
    files = sorted(p for p in folder.glob("*") if p.suffix.lower() in (".xlsx", ".xls", ".csv")
                   and not p.name.startswith(("~", "."))) if folder.exists() else []
    if not files:
        return pd.DataFrame(columns=AMFI_COLUMNS)
    out = pd.concat([parse_amfi_list(p) for p in files], ignore_index=True)
    return out.drop_duplicates(["as_of", "isin"], keep="last").reset_index(drop=True)


def load_sector_map(cfg: DataConfig) -> pd.DataFrame:
    """Sector per company: ``symbol``, ``isin``, ``sector``.

    Priority: the group's own ``data/external/sector_map.csv`` (which can cover
    delisted names, e.g. from CMIE Prowess), then NSE's current industry labels
    harvested from the public index constituent lists.
    """
    frames = []
    own = cfg.external / "sector_map.csv"
    if own.exists():
        df = _read_csv(own)
        sym, isin = _find(df, "symbol", "ticker"), _find(df, "isin")
        sec = _find(df, "sector", "industry")
        if sec is None or (sym is None and isin is None):
            raise ValueError(f"{own}: need symbol or isin, and a sector column; found {list(df.columns)}")
        frames.append(pd.DataFrame({
            "symbol": df[sym].fillna("").astype(str).str.strip().str.upper() if sym else "",
            "isin": df[isin].fillna("").astype(str).str.strip().str.upper() if isin else "",
            "sector": df[sec].fillna("").astype(str).str.strip(),
        }))
    seed = cfg.interim / "sector_seed.parquet"
    if seed.exists():
        s = pd.read_parquet(seed)
        frames.append(pd.DataFrame({"symbol": s["symbol"], "isin": s["isin"], "sector": s["industry"]}))
    if not frames:
        return pd.DataFrame(columns=["symbol", "isin", "sector"])
    out = pd.concat(frames, ignore_index=True)  # own file first, so it wins on duplicates
    return out[out["sector"] != ""].reset_index(drop=True)


def load_surveillance(cfg: DataConfig) -> pd.DataFrame:
    """ASM / GSM spells: ``symbol``, ``list``, ``start``, ``end`` (blank end = still listed)."""
    path = cfg.external / "surveillance.csv"
    cols = ["symbol", "list", "start", "end"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = _read_csv(path)
    sym, start = _find(df, "symbol", "ticker"), _find(df, "start", "from", "inclusion")
    end, lst = _find(df, "end", "to_date", "exclusion"), _find(df, "list", "measure", "stage", "type")
    if sym is None or start is None:
        raise ValueError(f"{path}: need symbol and start date columns; found {list(df.columns)}")
    return pd.DataFrame({
        "symbol": df[sym].astype(str).str.strip().str.upper(),
        "list": df[lst].astype(str).str.strip().str.upper() if lst else "SURVEILLANCE",
        "start": _to_date(df[start]),
        "end": _to_date(df[end]) if end else pd.NaT,
    }).dropna(subset=["start"])[cols]


def load_delistings(cfg: DataConfig) -> pd.DataFrame:
    """Delisting outcomes: ``symbol``, ``terminal_return`` (and free-text ``reason``).

    ``terminal_return`` is what a holder received relative to the last traded
    price (0 for a merger paid in shares at fair value, -1 for a wipe-out).
    It overrides the blanket assumption in ``risk.delisting_return``.
    """
    path = cfg.external / "delistings.csv"
    cols = ["symbol", "terminal_return", "reason"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = _read_csv(path)
    sym, ret = _find(df, "symbol", "ticker"), _find(df, "terminal", "return")
    if sym is None or ret is None:
        raise ValueError(f"{path}: need symbol and terminal_return columns; found {list(df.columns)}")
    reason = _find(df, "reason", "type")
    return pd.DataFrame({
        "symbol": df[sym].astype(str).str.strip().str.upper(),
        "terminal_return": pd.to_numeric(df[ret], errors="coerce"),
        "reason": df[reason].astype(str) if reason else "",
    }).dropna(subset=["terminal_return"])[cols]


def external_inventory(cfg: DataConfig) -> pd.DataFrame:
    """What the group has (and has not) supplied - shown at the top of every run."""
    ext = cfg.external
    items = [
        ("risk_free.csv", "REQUIRED", "Sharpe/Sortino and return on cash; zero is assumed if absent"),
        ("factors", "recommended", "Factor exposures; self-built proxies are used if absent"),
        ("benchmarks", "recommended", "Total-return benchmarks; price indices are used if absent"),
        ("index_constituents", "recommended", "Needed only for universe.source = index_constituents"),
        ("amfi", "optional", "AMFI half-yearly size lists; needed only if universe.size_metrics uses amfi_list"),
        ("sector_map.csv", "recommended", "Sector limits; NSE current labels are used if absent"),
        ("surveillance.csv", "optional", "ASM/GSM history; series (BE/BZ) is the fallback proxy"),
        ("delistings.csv", "optional", "Delisting outcomes; the blanket assumption is used if absent"),
        ("return_overrides.csv", "optional", "Hand corrections to flagged data errors"),
    ]
    rows = []
    for name, need, note in items:
        p = ext / name
        present = p.exists() and (p.is_file() or any(p.glob("*.csv")) or any(p.glob("*.xls*")))
        rows.append({"file": name, "status": need, "present": bool(present), "if_absent": note})
    return pd.DataFrame(rows)
