"""Combine niftyindices.com "Total Returns Index" downloads into one benchmark file.

Usage (from the repository folder):

    python tools/niftyindices_tri_to_csv.py  downloads/NIFTY*_Historical_TR_*.csv   (or a .zip of them)

niftyindices.com serves at most a year per download, newest day first, with
columns "IndexName", "Date", "Total Returns Index". This joins the pieces of
each index, sorts them, checks that no day appears twice with different
values, and writes data/external/benchmarks/<INDEX NAME> TRI.csv with columns
index_name, date (YYYY-MM-DD), total_returns_index. Values are not changed.
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd


def read_all(paths) -> pd.DataFrame:
    frames = []
    for path in map(Path, paths):
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".csv"):
                        frames.append(pd.read_csv(io.BytesIO(zf.read(name)), dtype=str).assign(_file=name))
        else:
            frames.append(pd.read_csv(path, dtype=str).assign(_file=path.name))
    if not frames:
        sys.exit("No CSV files given")
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    need = {"indexname", "date", "total_returns_index"}
    if not need <= set(df.columns):
        sys.exit(f"Expected columns IndexName, Date, Total Returns Index; found {list(df.columns)}")
    out = pd.DataFrame({
        "index_name": df["indexname"].str.strip().str.upper(),
        "date": pd.to_datetime(df["date"].str.strip(), format="%d %b %Y", errors="coerce"),
        "total_returns_index": pd.to_numeric(df["total_returns_index"].str.replace(",", ""), errors="coerce"),
    })
    bad = out["date"].isna() | out["total_returns_index"].isna()
    if bad.any():
        sys.exit(f"{int(bad.sum())} row(s) with an unreadable date or value, e.g. {df[bad].head(3).to_dict('records')}")
    clash = out.groupby(["index_name", "date"])["total_returns_index"].nunique()
    if (clash > 1).any():
        sys.exit(f"The same day has two different values: {clash[clash > 1].index.tolist()[:5]}")
    return out.drop_duplicates(["index_name", "date"]).sort_values(["index_name", "date"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+", help="downloaded CSV files, or a zip of them")
    ap.add_argument("--out-dir", type=Path, default=Path("data/external/benchmarks"))
    args = ap.parse_args()
    data = read_all(args.files)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, part in data.groupby("index_name"):
        out = args.out_dir / f"{name} TRI.csv"
        part.assign(date=part["date"].dt.strftime("%Y-%m-%d")).to_csv(out, index=False)
        gaps = part["date"].diff().dt.days
        print(f"Wrote {out}: {len(part)} days, {part['date'].iloc[0].date()} to {part['date'].iloc[-1].date()}, "
              f"longest gap {int(gaps.max())} calendar days")


if __name__ == "__main__":
    main()
