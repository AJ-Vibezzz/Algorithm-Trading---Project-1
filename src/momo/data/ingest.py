"""Stage 1: parse every raw NSE file into compact yearly parquet files.

``data/raw``  (thousands of small zips/CSVs)  ->  ``data/interim``  (a few parquet files)

Nothing is interpreted here - no adjustments, no filters beyond keeping the
equity share lines. That keeps this stage a faithful, re-runnable copy of the
exchange files, and makes the next stage (``panel.py``) fast.

Re-running is cheap: a year is re-parsed only when its raw files changed.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from ..config import DataConfig
from ..utils import to_ns
from . import parsers

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover
    tqdm = None

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}

_NAME_PATTERNS: Dict[str, List[Tuple[re.Pattern, str]]] = {
    "cm_bhav": [(re.compile(r"cm(\d{2})([A-Z]{3})(\d{4})bhav", re.I), "dMy"),
                (re.compile(r"_CM_0_0_0_(\d{8})_F_", re.I), "Ymd")],
    "fo_bhav": [(re.compile(r"fo(\d{2})([A-Z]{3})(\d{4})bhav", re.I), "dMy"),
                (re.compile(r"_FO_0_0_0_(\d{8})_F_", re.I), "Ymd")],
    "pr": [(re.compile(r"PR(\d{6})\.zip$", re.I), "dmy")],
    "ind_close": [(re.compile(r"ind_close_all_(\d{8})", re.I), "dmY")],
    "sec_list": [(re.compile(r"sec_list_(\d{8})", re.I), "dmY")],
}


def date_from_name(dataset: str, name: str) -> Optional[dt.date]:
    """Trade date encoded in a raw file name, or None if the name is not recognised."""
    for pattern, style in _NAME_PATTERNS.get(dataset, []):
        m = pattern.search(name)
        if not m:
            continue
        try:
            if style == "dMy":
                return dt.date(int(m.group(3)), _MONTHS[m.group(2).upper()], int(m.group(1)))
            if style == "Ymd":
                return dt.datetime.strptime(m.group(1), "%Y%m%d").date()
            if style == "dmy":
                return dt.datetime.strptime(m.group(1), "%d%m%y").date()
            if style == "dmY":
                return dt.datetime.strptime(m.group(1), "%d%m%Y").date()
        except (ValueError, KeyError):
            return None
    return None


def _raw_files(cfg: DataConfig, dataset: str) -> Dict[int, List[Tuple[dt.date, Path]]]:
    """Raw files of one dataset, grouped by year and sorted by date."""
    by_year: Dict[int, List[Tuple[dt.date, Path]]] = {}
    base = cfg.raw / dataset
    if not base.exists():
        return by_year
    for path in base.rglob("*"):
        if not path.is_file() or path.name.endswith(".part"):
            continue
        d = date_from_name(dataset, path.name)
        if d is not None:
            by_year.setdefault(d.year, []).append((d, path))
    for year in by_year:
        by_year[year].sort()
    return by_year


def _signature(files: List[Tuple[dt.date, Path]]) -> str:
    """Cheap fingerprint of a year's raw files: count, total bytes, newest mtime."""
    stats = [p.stat() for _, p in files]
    return f"{len(files)}|{sum(s.st_size for s in stats)}|{max((int(s.st_mtime) for s in stats), default=0)}"


class _State:
    """Remembers which (dataset, year) were already parsed from which raw files."""

    def __init__(self, path: Path):
        self.path = path
        self.data: Dict[str, str] = json.loads(path.read_text()) if path.exists() else {}

    def fresh(self, key: str, sig: str, outputs: List[Path]) -> bool:
        return self.data.get(key) == sig and all(p.exists() for p in outputs)

    def mark(self, key: str, sig: str) -> None:
        self.data[key] = sig
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))


def _loop(items, desc: str, progress: bool):
    return tqdm(items, desc=desc, unit="file") if (tqdm and progress) else items


def _compress_actions(daily: pd.DataFrame) -> pd.DataFrame:
    """Daily corporate-action listings -> one row per distinct listing text.

    Keeps the first and last date each (symbol, ex-date, purpose) appeared, which
    is all the next stage needs to pick the exchange's final wording.
    """
    key = ["symbol", "series", "ex_date", "purpose"]
    g = daily.groupby(key, sort=False)
    out = g.agg(
        security=("security", "first"),
        record_date=("record_date", "first"),
        first_seen=("seen_date", "min"),
        last_seen=("seen_date", "max"),
    ).reset_index()
    return out


def ingest(cfg: DataConfig, force: bool = False, progress: bool = True) -> Dict[str, int]:
    """Parse raw files into ``data/interim``. Returns row counts per output."""
    interim = cfg.interim
    interim.mkdir(parents=True, exist_ok=True)
    state = _State(interim / "_state.json")
    errors: List[dict] = []
    counts: Dict[str, int] = {}

    def run_parser(parser: Callable, path: Path, d: dt.date, dataset: str):
        try:
            return parser(path, d)
        except Exception as exc:  # a bad file must not stop a 3,000-file run
            errors.append({"dataset": dataset, "date": d.isoformat(), "file": path.name, "error": str(exc)})
            return None

    # ---- cash-market bhavcopy -> prices ------------------------------------
    out_dir = interim / "prices"
    out_dir.mkdir(exist_ok=True)
    for year, files in sorted(_raw_files(cfg, "cm_bhav").items()):
        out = out_dir / f"prices_{year}.parquet"
        sig = _signature(files)
        if not force and state.fresh(f"cm_bhav:{year}", sig, [out]):
            continue
        frames = []
        for d, path in _loop(files, f"prices {year}", progress):
            df = run_parser(parsers.parse_cm_bhav, path, d, "cm_bhav")
            if df is not None and len(df):
                frames.append(df[df["series"].isin(cfg.equity_series)])
        if frames:
            pd.concat(frames, ignore_index=True).to_parquet(out, index=False)
            state.mark(f"cm_bhav:{year}", sig)

    # ---- PR zip -> corporate actions, market cap, band hits ---------------
    for sub in ("corp_actions", "mcap", "band_hits"):
        (interim / sub).mkdir(exist_ok=True)
    for year, files in sorted(_raw_files(cfg, "pr").items()):
        outs = {k: interim / k / f"{k}_{year}.parquet" for k in ("corp_actions", "mcap", "band_hits")}
        sig = _signature(files)
        if not force and state.fresh(f"pr:{year}", sig, [outs["corp_actions"]]):
            continue
        acc: Dict[str, List[pd.DataFrame]] = {k: [] for k in outs}
        for d, path in _loop(files, f"PR {year}", progress):
            parsed = run_parser(parsers.parse_pr_zip, path, d, "pr")
            if parsed is None:
                continue
            for k in outs:
                if k in parsed and len(parsed[k]):
                    frame = parsed[k]
                    if k != "corp_actions":  # keep only the equity lines
                        frame = frame[frame["series"].isin(cfg.equity_series + [""])]
                    acc[k].append(frame)
            if "_errors" in parsed:
                for rec in parsed["_errors"].to_dict("records"):
                    errors.append({"dataset": "pr", "date": d.isoformat(), "file": rec["member"], "error": rec["error"]})
        # Always write the corporate-action file (even if empty) so the year is marked done.
        ca = pd.concat(acc["corp_actions"], ignore_index=True) if acc["corp_actions"] else pd.DataFrame(
            columns=parsers.CORP_ACTION_COLUMNS)
        (_compress_actions(ca) if len(ca) else pd.DataFrame(
            columns=["symbol", "series", "ex_date", "purpose", "security", "record_date", "first_seen", "last_seen"]
        )).to_parquet(outs["corp_actions"], index=False)
        for k in ("mcap", "band_hits"):
            if acc[k]:
                pd.concat(acc[k], ignore_index=True).to_parquet(outs[k], index=False)
        state.mark(f"pr:{year}", sig)

    # ---- index snapshot, F&O eligibility, price bands ----------------------
    simple = [
        ("ind_close", parsers.parse_ind_close, "indices.parquet"),
        ("fo_bhav", parsers.parse_fo_bhav, "fo_eligible.parquet"),
        ("sec_list", parsers.parse_sec_list, "price_bands.parquet"),
    ]
    for dataset, parser, out_name in simple:
        by_year = _raw_files(cfg, dataset)
        if not by_year:
            continue
        files = [f for year in sorted(by_year) for f in by_year[year]]
        out = interim / out_name
        sig = _signature(files)
        if not force and state.fresh(f"{dataset}:all", sig, [out]):
            continue
        frames = [df for d, path in _loop(files, dataset, progress)
                  if (df := run_parser(parser, path, d, dataset)) is not None and len(df)]
        if frames:
            pd.concat(frames, ignore_index=True).to_parquet(out, index=False)
            state.mark(f"{dataset}:all", sig)

    # ---- static files --------------------------------------------------------
    static = cfg.raw / "static"
    sc = static / "symbolchange.csv"
    if sc.exists():
        try:
            parsers.parse_symbol_changes(sc).to_parquet(interim / "symbol_changes.parquet", index=False)
        except Exception as exc:
            errors.append({"dataset": "static", "date": "", "file": sc.name, "error": str(exc)})
    seeds = []
    for path in sorted(static.glob("ind_*list*.csv")) if static.exists() else []:
        try:
            seeds.append(parsers.parse_index_members(path).assign(source=path.name))
        except Exception as exc:
            errors.append({"dataset": "static", "date": "", "file": path.name, "error": str(exc)})
    if seeds:
        seed = pd.concat(seeds, ignore_index=True)
        seed = seed[seed["industry"] != ""].drop_duplicates("symbol")
        seed.to_parquet(interim / "sector_seed.parquet", index=False)

    pd.DataFrame(errors, columns=["dataset", "date", "file", "error"]).to_csv(
        interim / "_parse_errors.csv", index=False)

    for name in ("prices", "mcap", "band_hits", "corp_actions"):
        counts[name] = len(list((interim / name).glob("*.parquet")))
    counts["parse_errors"] = len(errors)
    return counts


def read_interim(cfg: DataConfig, name: str) -> pd.DataFrame:
    """Concatenate the yearly parquet files of one interim table (empty frame if none)."""
    folder = cfg.interim / name
    files = sorted(folder.glob("*.parquet")) if folder.exists() else []
    if not files:
        single = cfg.interim / f"{name}.parquet"
        return to_ns(pd.read_parquet(single)) if single.exists() else pd.DataFrame()
    return to_ns(pd.concat([pd.read_parquet(f) for f in files], ignore_index=True))
