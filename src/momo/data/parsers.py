"""Parsers: one function per raw NSE file layout, each returning a tidy DataFrame.

The trade date always comes from the *file name* (which the downloader controls),
never from a column inside the file. Column names are matched after
normalisation so that small layout changes over the years do not break parsing.

Every parser returns the same columns for the same kind of data regardless of
which historical layout the file used; see ``PRICE_COLUMNS`` etc. below.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import zipfile
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from ..utils import to_ns


class ParseError(ValueError):
    """Raised when a raw file does not look like what its name promises."""


PRICE_COLUMNS = [
    "date", "symbol", "series", "isin",
    "open", "high", "low", "close", "last", "prevclose",
    "volume", "value", "trades",
]
CORP_ACTION_COLUMNS = ["symbol", "series", "security", "ex_date", "record_date", "purpose", "seen_date"]
MCAP_COLUMNS = ["date", "symbol", "series", "face_value", "shares", "mcap", "mcap_close", "category"]
BAND_HIT_COLUMNS = ["date", "symbol", "series", "hit"]
INDEX_COLUMNS = ["date", "index_name", "open", "high", "low", "close", "pe", "pb", "div_yield"]
MCAP_LIST_COLUMNS = ["as_of", "symbol", "company", "rank", "mcap"]
INDEX_WEIGHT_COLUMNS = ["as_of", "index_name", "symbol", "company", "industry", "equity_capital", "ff_mcap", "weight"]


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _norm(name: object) -> str:
    """'Closing Index Value' -> 'CLOSING_INDEX_VALUE'; 'TckrSymb' -> 'TCKRSYMB'."""
    return re.sub(r"[^A-Z0-9]+", "_", str(name).upper()).strip("_")


def _read_table(raw: bytes, must_have: Sequence[str]) -> pd.DataFrame:
    """Read a CSV whose header row may be preceded by title lines.

    The header is the first of the leading lines that contains every token in
    ``must_have`` (case-insensitive). All cells are read as text; numeric
    conversion happens explicitly afterwards so nothing is silently coerced.
    """
    lines = _decode(raw).splitlines()
    header_at = None
    for i, line in enumerate(lines[:25]):
        up = line.upper()
        if all(tok.upper() in up for tok in must_have):
            header_at = i
            break
    if header_at is None:
        raise ParseError(f"no header row containing {list(must_have)}")
    body = "\n".join(lines[header_at:])
    df = pd.read_csv(io.StringIO(body), dtype=str, skipinitialspace=True, on_bad_lines="skip")
    df.columns = [_norm(c) for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c and not c.startswith("UNNAMED")]]
    return df.dropna(how="all")


def _num(col: pd.Series) -> pd.Series:
    """Text -> float; blanks, dashes and stray commas become NaN."""
    cleaned = col.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def _txt(col: pd.Series) -> pd.Series:
    return col.fillna("").astype(str).str.strip().str.upper()


def _pick(df: pd.DataFrame, *candidates: str, contains: Iterable[str] = ()) -> Optional[str]:
    """First existing column among ``candidates``, else first whose name contains all ``contains``."""
    for c in candidates:
        if c in df.columns:
            return c
    tokens = [t.upper() for t in contains]
    if tokens:
        for c in df.columns:
            if all(t in c for t in tokens):
                return c
    return None


def _dates(col: pd.Series) -> pd.Series:
    """Parse day-first dates in the handful of formats NSE uses; unparseable -> NaT."""
    text = col.fillna("").astype(str).str.strip()
    out = pd.Series(pd.NaT, index=col.index, dtype="datetime64[ns]")
    for fmt in ("%d/%m/%Y", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d-%b-%y", "%d/%m/%y"):
        todo = out.isna() & (text != "")
        if not todo.any():
            break
        parsed = pd.to_datetime(text[todo], format=fmt, errors="coerce")
        out.loc[todo] = parsed.astype("datetime64[ns]")
    return out


def _first_csv_in_zip(path_or_bytes) -> bytes:
    source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
    with zipfile.ZipFile(source) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ParseError("zip holds no csv")
        return zf.read(names[0])


def _read_bytes(path: Path) -> bytes:
    return Path(path).read_bytes()


# --------------------------------------------------------------------------- #
# Cash-market bhavcopy (prices and volumes)
# --------------------------------------------------------------------------- #
def parse_cm_bhav(path: Path, date: dt.date) -> pd.DataFrame:
    """Cash-market bhavcopy, legacy or UDiFF layout -> ``PRICE_COLUMNS``.

    ``value`` is traded value in rupees; ``value / volume`` is the day's
    volume-weighted average price (VWAP), which the engine can use as the
    execution price.
    """
    raw = _first_csv_in_zip(path) if str(path).lower().endswith(".zip") else _read_bytes(path)
    head = _decode(raw[:400]).upper()
    if "TCKRSYMB" in head:  # UDiFF layout (8 July 2024 onwards)
        df = _read_table(raw, ["TckrSymb"])
        ftype = _pick(df, "FININSTRMTP")
        if ftype is not None:
            df = df[_txt(df[ftype]).isin(["STK", ""])]
        cols = {
            "symbol": "TCKRSYMB", "series": "SCTYSRS", "isin": "ISIN",
            "open": "OPNPRIC", "high": "HGHPRIC", "low": "LWPRIC", "close": "CLSPRIC",
            "last": "LASTPRIC", "prevclose": "PRVSCLSGPRIC",
            "volume": "TTLTRADGVOL", "value": "TTLTRFVAL", "trades": "TTLNBOFTXSEXCTD",
        }
    else:  # legacy layout
        df = _read_table(raw, ["SYMBOL", "SERIES"])
        cols = {
            "symbol": "SYMBOL", "series": "SERIES", "isin": "ISIN",
            "open": "OPEN", "high": "HIGH", "low": "LOW", "close": "CLOSE",
            "last": "LAST", "prevclose": "PREVCLOSE",
            "volume": "TOTTRDQTY", "value": "TOTTRDVAL", "trades": "TOTALTRADES",
        }
    missing = [c for k, c in cols.items() if c not in df.columns and k not in ("isin", "trades", "last")]
    if missing:
        raise ParseError(f"bhavcopy lacks columns {missing}")

    out = pd.DataFrame(index=df.index)
    out["date"] = pd.Timestamp(date)
    out["symbol"] = _txt(df[cols["symbol"]])
    out["series"] = _txt(df[cols["series"]])
    # Very old files have no ISIN column; identifiers.py then links by symbol only.
    out["isin"] = _txt(df[cols["isin"]]) if cols["isin"] in df.columns else ""
    for k in ("open", "high", "low", "close", "last", "prevclose", "volume", "value", "trades"):
        out[k] = _num(df[cols[k]]) if cols[k] in df.columns else np.nan
    out = out[(out["symbol"] != "") & out["close"].notna() & (out["close"] > 0)]
    return to_ns(out[PRICE_COLUMNS].reset_index(drop=True))


# --------------------------------------------------------------------------- #
# PR zip: corporate actions, market cap, price-band hits
# --------------------------------------------------------------------------- #
def parse_corp_actions(raw: bytes, seen_date: dt.date) -> pd.DataFrame:
    """``Bc*.csv``: corporate actions as published on ``seen_date``.

    ``seen_date`` is kept so the pipeline can prove a corporate action was
    public knowledge before it was used (point-in-time discipline).
    """
    df = _read_table(raw, ["SYMBOL", "PURPOSE"])
    sym = _pick(df, "SYMBOL")
    ser = _pick(df, "SERIES")
    sec = _pick(df, "SECURITY", contains=["SECURITY"])
    exd = _pick(df, "EX_DT", "EX_DATE", contains=["EX", "D"])
    rec = _pick(df, "RECORD_DT", "RECORD_DATE", contains=["RECORD"])
    pur = _pick(df, "PURPOSE")
    if sym is None or exd is None or pur is None:
        raise ParseError("corporate-action file lacks SYMBOL / EX_DT / PURPOSE")
    out = pd.DataFrame(
        {
            "symbol": _txt(df[sym]),
            "series": _txt(df[ser]) if ser else "",
            "security": _txt(df[sec]) if sec else "",
            "ex_date": _dates(df[exd]),
            "record_date": _dates(df[rec]) if rec else pd.NaT,
            "purpose": _txt(df[pur]),
        }
    )
    out["seen_date"] = pd.Timestamp(seen_date)
    out = out[(out["symbol"] != "") & out["ex_date"].notna() & (out["purpose"] != "")]
    return to_ns(out[CORP_ACTION_COLUMNS].drop_duplicates().reset_index(drop=True))


def parse_mcap(raw: bytes, date: dt.date) -> pd.DataFrame:
    """``MCAP*.csv``: shares outstanding ("issue size") and market capitalisation."""
    df = _read_table(raw, ["SYMBOL"])
    sym = _pick(df, "SYMBOL")
    ser = _pick(df, "SERIES")
    fv = _pick(df, contains=["FACE", "VALUE"])
    shares = _pick(df, contains=["ISSUE", "SIZE"])
    mcap = _pick(df, contains=["MARKET", "CAP"])
    close = _pick(df, contains=["CLOSE", "PRICE"])
    cat = _pick(df, "CATEGORY")
    if sym is None or (shares is None and mcap is None):
        raise ParseError("market-cap file lacks SYMBOL / ISSUE SIZE / MARKET CAP")
    out = pd.DataFrame(
        {
            "date": pd.Timestamp(date),
            "symbol": _txt(df[sym]),
            "series": _txt(df[ser]) if ser else "",
            "face_value": _num(df[fv]) if fv else np.nan,
            "shares": _num(df[shares]) if shares else np.nan,
            "mcap": _num(df[mcap]) if mcap else np.nan,
            "mcap_close": _num(df[close]) if close else np.nan,
            "category": _txt(df[cat]) if cat else "",
        }
    )
    out = out[(out["symbol"] != "") & (out["shares"].fillna(0) > 0)]
    return to_ns(out[MCAP_COLUMNS].reset_index(drop=True))


def parse_band_hits(raw: bytes, date: dt.date) -> pd.DataFrame:
    """``Bh*.csv``: securities that touched their upper (H) or lower (L) price band."""
    df = _read_table(raw, ["SYMBOL"])
    sym = _pick(df, "SYMBOL")
    ser = _pick(df, "SERIES", "SR")  # the series column is headed "SR" in the oldest archives
    hit = _pick(df, "HIGH_LOW", contains=["HIGH"])
    if sym is None or hit is None:
        raise ParseError("band-hit file lacks SYMBOL / HIGH-LOW flag")
    out = pd.DataFrame(
        {
            "date": pd.Timestamp(date),
            "symbol": _txt(df[sym]),
            "series": _txt(df[ser]) if ser else "",
            "hit": _txt(df[hit]).str[:1],
        }
    )
    out = out[(out["symbol"] != "") & out["hit"].isin(["H", "L"])]
    return to_ns(out[BAND_HIT_COLUMNS].drop_duplicates().reset_index(drop=True))


# Member names inside the PR zip changed over the years: the date part is six
# digits (ddmmyy) in most archives and eight (ddmmyyyy) in others - for example
# ``Bc030111.csv`` but ``bh03012011.csv`` in 2011, and ``bc30102026.csv`` in 2026 -
# and the case of the prefix varies. Match on prefix + 6-8 digits, any case.
_PR_MEMBERS = {
    "corp_actions": (re.compile(r"^bc\d{6,8}\.csv$"), parse_corp_actions),
    "mcap": (re.compile(r"^mcap\d{6,8}\.csv$"), parse_mcap),
    "band_hits": (re.compile(r"^bh\d{6,8}\.csv$"), parse_band_hits),
}


def parse_pr_zip(path: Path, date: dt.date) -> Dict[str, pd.DataFrame]:
    """Open the daily PR zip and parse the members we use.

    Returns a dict with keys ``corp_actions``, ``mcap``, ``band_hits``; a key is
    absent when that member is not in the zip (older archives carry fewer files).
    A member that fails to parse is skipped and reported under ``_errors``.
    """
    out: Dict[str, pd.DataFrame] = {}
    errors = []
    with zipfile.ZipFile(path) as zf:
        for member in zf.namelist():
            base = member.rsplit("/", 1)[-1].lower()
            for key, (pattern, parser) in _PR_MEMBERS.items():
                if pattern.match(base):
                    try:
                        out[key] = parser(zf.read(member), date)
                    except Exception as exc:  # keep going; the quality report shows the gap
                        errors.append({"date": pd.Timestamp(date), "member": member, "error": str(exc)})
    if errors:
        out["_errors"] = pd.DataFrame(errors)
    return out


# --------------------------------------------------------------------------- #
# Index snapshot
# --------------------------------------------------------------------------- #
def parse_ind_close(path: Path, date: dt.date) -> pd.DataFrame:
    """All-index closing snapshot -> ``INDEX_COLUMNS`` (index names upper-cased)."""
    df = _read_table(_read_bytes(path), ["INDEX NAME"])
    name = _pick(df, "INDEX_NAME")
    close = _pick(df, "CLOSING_INDEX_VALUE", contains=["CLOSING"])
    if name is None or close is None:
        raise ParseError("index file lacks 'Index Name' / 'Closing Index Value'")
    out = pd.DataFrame(
        {
            "date": pd.Timestamp(date),
            "index_name": _txt(df[name]),
            "open": _num(df[c]) if (c := _pick(df, "OPEN_INDEX_VALUE", contains=["OPEN"])) else np.nan,
            "high": _num(df[c]) if (c := _pick(df, "HIGH_INDEX_VALUE", contains=["HIGH"])) else np.nan,
            "low": _num(df[c]) if (c := _pick(df, "LOW_INDEX_VALUE", contains=["LOW"])) else np.nan,
            "close": _num(df[close]),
            "pe": _num(df[c]) if (c := _pick(df, "P_E")) else np.nan,
            "pb": _num(df[c]) if (c := _pick(df, "P_B")) else np.nan,
            "div_yield": _num(df[c]) if (c := _pick(df, "DIV_YIELD", contains=["DIV"])) else np.nan,
        }
    )
    out = out[(out["index_name"] != "") & out["close"].notna()]
    return to_ns(out[INDEX_COLUMNS].reset_index(drop=True))


# --------------------------------------------------------------------------- #
# Exchange market-capitalisation lists (all companies, as on a given date)
# --------------------------------------------------------------------------- #
_UNIT_RUPEES = (("LAKH", 1e5), ("CRORE", 1e7), ("CR", 1e7))


def _grid(path: Path) -> pd.DataFrame:
    """A spreadsheet or CSV as a grid of text cells, with no header assumed."""
    suffix = str(path).lower().rsplit(".", 1)[-1]
    if suffix in ("xlsx", "xlsm", "xls"):
        try:
            book = pd.ExcelFile(path)
        except ImportError as exc:  # pragma: no cover - depends on the installation
            raise ParseError(f"reading {suffix} files needs the 'openpyxl' package (pip install openpyxl)") from exc
        return book.parse(book.sheet_names[0], header=None, dtype=str)
    return pd.read_csv(io.StringIO(_decode(_read_bytes(path))), header=None, dtype=str,
                       skipinitialspace=True, on_bad_lines="skip")


def parse_mcap_list(path: Path, as_of: dt.date) -> pd.DataFrame:
    """NSE "market capitalisation - all companies" list -> ``MCAP_LIST_COLUMNS``.

    These are the lists the exchange publishes under the listing regulations
    (which companies are in the top 100 / 500 / 1000 by market capitalisation):
    one row per company with its rank and market capitalisation on the as-on
    date. ``mcap`` is returned in rupees. Companies that did not trade on that
    date carry a note instead of a number and come back as NaN, and the oldest
    list (31 March 2019, top 1000) has ranks but no values at all, so ``mcap``
    is NaN throughout for it and only ``rank`` can be used.
    """
    grid = _grid(Path(path))
    header_at = None
    for i in range(min(15, len(grid))):
        cells = [_norm(v) for v in grid.iloc[i].tolist()]
        if "SYMBOL" in cells:
            header_at = i
            break
    if header_at is None:
        raise ParseError("market-cap list has no header row with a 'Symbol' column")
    names = [_norm(v) if isinstance(v, str) else "" for v in grid.iloc[header_at].tolist()]
    body = grid.iloc[header_at + 1:].reset_index(drop=True)
    body.columns = range(body.shape[1])

    def col(*tokens: str) -> Optional[int]:
        for j, name in enumerate(names):
            if name and all(t in name for t in tokens):
                return j
        return None

    c_sym, c_rank = names.index("SYMBOL"), col("SR")
    c_name = col("COMPANY") if col("COMPANY") is not None else col("NAME")
    c_cap = col("MARKET", "CAP")
    scale = np.nan
    if c_cap is not None:
        for token, rupees in _UNIT_RUPEES:
            if token in names[c_cap]:
                scale = rupees
                break
        if not np.isfinite(scale):
            raise ParseError(f"market-cap list: cannot tell the unit of '{grid.iloc[header_at, c_cap]}'")

    out = pd.DataFrame(
        {
            "as_of": pd.Timestamp(as_of),
            "symbol": _txt(body[c_sym]),
            "company": body[c_name].fillna("").astype(str).str.strip() if c_name is not None else "",
            "rank": _num(body[c_rank]) if c_rank is not None else np.nan,
            "mcap": _num(body[c_cap]) * scale if c_cap is not None else np.nan,
        }
    )
    out = out[(out["symbol"] != "") & (out["symbol"] != "NAN")]
    if c_rank is not None:
        out = out[out["rank"].notna()]  # drops the footnote rows under the table
    else:
        out["rank"] = np.arange(1, len(out) + 1, dtype=float)
    if out.empty:
        raise ParseError("market-cap list has no company rows")
    return to_ns(out[MCAP_LIST_COLUMNS].drop_duplicates("symbol").reset_index(drop=True))


# --------------------------------------------------------------------------- #
# Monthly index files: members of the Nifty 50 and Nifty Next 50
# --------------------------------------------------------------------------- #
def _index_name_from_member(member: str) -> Optional[str]:
    """'niftymcwb.csv' / 'nifty50_mcwb.csv' -> NIFTY 50; 'jrniftymcwb.csv' / 'niftynext50_mcwb.csv' -> NIFTY NEXT 50."""
    base = member.rsplit("/", 1)[-1].lower()
    if not base.endswith(".csv") or "mcwb" not in base:
        return None
    if "jr" in base or "junior" in base or "next" in base:
        return "NIFTY NEXT 50"
    if "nifty" in base:
        return "NIFTY 50"
    return None


def parse_index_weights(path: Path, as_of: dt.date) -> pd.DataFrame:
    """Monthly "market capitalisation, weightage, beta" zip -> ``INDEX_WEIGHT_COLUMNS``.

    The exchange publishes one small zip per month (``mcwb_jan20.zip``) holding
    the members of the Nifty 50 and of the Nifty Next 50 as at that month-end,
    with each member's equity capital and free-float market capitalisation.
    Together they are the Nifty 100 - a point-in-time list of the large caps,
    available back to 2008. The index is named after the file inside the zip
    (the index itself was renamed three times), so the output always says
    ``NIFTY 50`` and ``NIFTY NEXT 50``. ``ff_mcap`` is in rupees.
    """
    import csv

    frames = []
    with zipfile.ZipFile(path) as zf:
        for member in zf.namelist():
            index_name = _index_name_from_member(member)
            if index_name is None:
                continue
            rows = list(csv.reader(io.StringIO(_decode(zf.read(member)))))
            header_at = next((i for i, r in enumerate(rows[:15]) if any("SYMBOL" in _norm(c) for c in r)), None)
            if header_at is None:
                raise ParseError(f"{member}: no header row with a symbol column")
            names = [_norm(c) for c in rows[header_at]]

            def col(*tokens: str, start: bool = False) -> Optional[int]:
                for j, name in enumerate(names):
                    if all(t in name for t in tokens) and (not start or name.startswith(tokens[0])):
                        return j
                return None

            c_sym, c_name, c_ind = col("SYMBOL"), col("SECURITY", "NAME"), col("INDUSTRY")
            c_eq, c_cap, c_wt = col("EQUITY", start=True), col("MARKET", "CAP"), col("WEIGHT")
            recs = []
            for r in rows[header_at + 1:]:
                if not r or not r[0].strip().replace(".", "", 1).isdigit() or len(r) <= c_sym:
                    continue  # the units row, blank rows and the footnotes

                def cell(j: Optional[int]) -> str:
                    return r[j].strip() if j is not None and j < len(r) else ""

                recs.append({"symbol": cell(c_sym).upper(), "company": cell(c_name), "industry": cell(c_ind),
                             "equity_capital": cell(c_eq), "ff_mcap": cell(c_cap), "weight": cell(c_wt)})
            if not recs:
                raise ParseError(f"{member}: no member rows")
            df = pd.DataFrame(recs)
            df["index_name"] = index_name
            frames.append(df)
    if not frames:
        raise ParseError("zip holds no index member file")
    out = pd.concat(frames, ignore_index=True)
    out["as_of"] = pd.Timestamp(as_of)
    out["equity_capital"] = _num(out["equity_capital"])
    out["ff_mcap"] = _num(out["ff_mcap"]) * 1e7  # published in Rs crore
    out["weight"] = _num(out["weight"])
    out = out[out["symbol"] != ""]
    return to_ns(out[INDEX_WEIGHT_COLUMNS].drop_duplicates(["index_name", "symbol"]).reset_index(drop=True))


# --------------------------------------------------------------------------- #
# Optional daily files
# --------------------------------------------------------------------------- #
def parse_fo_bhav(path: Path, date: dt.date) -> pd.DataFrame:
    """Derivatives bhavcopy -> symbols that had *stock futures* on ``date``.

    Used only for point-in-time F&O eligibility (which stocks could be shorted
    overnight through futures). Columns: ``date``, ``symbol``.
    """
    raw = _first_csv_in_zip(path) if str(path).lower().endswith(".zip") else _read_bytes(path)
    head = _decode(raw[:400]).upper()
    if "TCKRSYMB" in head:
        df = _read_table(raw, ["TckrSymb"])
        is_fut = _txt(df["FININSTRMTP"]) == "STF"
        symbols = _txt(df.loc[is_fut, "TCKRSYMB"])
    else:
        df = _read_table(raw, ["INSTRUMENT", "SYMBOL"])
        is_fut = _txt(df["INSTRUMENT"]) == "FUTSTK"
        symbols = _txt(df.loc[is_fut, "SYMBOL"])
    out = pd.DataFrame({"date": pd.Timestamp(date), "symbol": sorted(set(symbols) - {""})})
    return to_ns(out)


def parse_sec_list(path: Path, date: dt.date) -> pd.DataFrame:
    """Security-wise price bands -> ``date``, ``symbol``, ``series``, ``band`` (fraction)."""
    df = _read_table(_read_bytes(path), ["SYMBOL"])
    sym, ser = _pick(df, "SYMBOL"), _pick(df, "SERIES")
    band = _pick(df, "BAND", contains=["BAND"])
    if sym is None or band is None:
        raise ParseError("price-band file lacks SYMBOL / BAND")
    out = pd.DataFrame(
        {
            "date": pd.Timestamp(date),
            "symbol": _txt(df[sym]),
            "series": _txt(df[ser]) if ser else "",
            "band": _num(df[band]) / 100.0,  # "No Band" -> NaN
        }
    )
    return to_ns(out[out["symbol"] != ""].reset_index(drop=True))


# --------------------------------------------------------------------------- #
# Static files
# --------------------------------------------------------------------------- #
def parse_symbol_changes(path: Path) -> pd.DataFrame:
    """``symbolchange.csv`` -> ``old_symbol``, ``new_symbol``, ``change_date``.

    Layout: company name, old symbol, new symbol, effective date - with or
    without a header row, so columns are taken by position.
    """
    text = _decode(_read_bytes(path))
    df = pd.read_csv(io.StringIO(text), header=None, dtype=str, skipinitialspace=True, on_bad_lines="skip")
    if df.shape[1] < 4:
        raise ParseError("symbol-change file needs 4 columns")
    df = df.iloc[:, :4]
    df.columns = ["name", "old_symbol", "new_symbol", "change_date"]
    df["change_date"] = _dates(df["change_date"])
    df = df[df["change_date"].notna()]  # drops the header row, if there is one
    df["old_symbol"] = _txt(df["old_symbol"])
    df["new_symbol"] = _txt(df["new_symbol"])
    df = df[(df["old_symbol"] != "") & (df["new_symbol"] != "") & (df["old_symbol"] != df["new_symbol"])]
    return to_ns(df[["old_symbol", "new_symbol", "change_date"]].drop_duplicates().reset_index(drop=True))


def parse_index_members(path: Path) -> pd.DataFrame:
    """NSE index constituent list -> ``symbol``, ``isin``, ``industry``, ``company``.

    These lists are *today's* members. They are used only to seed the sector
    map - never to define the historical universe (that would be survivorship
    bias).
    """
    df = _read_table(_read_bytes(path), ["SYMBOL"])
    sym = _pick(df, "SYMBOL")
    ind = _pick(df, "INDUSTRY", contains=["INDUSTRY"])
    isin = _pick(df, "ISIN_CODE", "ISIN", contains=["ISIN"])
    name = _pick(df, "COMPANY_NAME", contains=["COMPANY"])
    out = pd.DataFrame(
        {
            "symbol": _txt(df[sym]),
            "isin": _txt(df[isin]) if isin else "",
            "industry": df[ind].fillna("").astype(str).str.strip() if ind else "",
            "company": df[name].fillna("").astype(str).str.strip() if name else "",
        }
    )
    return out[out["symbol"] != ""].drop_duplicates("symbol").reset_index(drop=True)
