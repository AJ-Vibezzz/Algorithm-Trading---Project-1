"""Convert RBI's "Auctions of 91-Day Government of India Treasury Bills" table into risk_free.csv.

Usage (from the repository folder):

    python tools/rbi_tbill_to_csv.py  path/to/Auctions_of_91-Day_..._Treasury_Bills.xlsx

writes ``data/external/risk_free.csv`` with two columns:

    date        the auction date (results are announced that day, so the yield is public from it)
    yield_pct   the implicit yield at the cut-off price, in percent (actual/365, as RBI states)

The file is the table as downloaded from RBI's Database on Indian Economy
(https://data.rbi.org.in) or the Handbook of Statistics. Nothing is computed or
filled in: auctions with no accepted bids (shown as "-") are left out, and each
day of the back-test uses the latest yield published on or before it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

DATE_HEADER = "date of auction"
YIELD_HEADER = "implicit yield at cut-off"


def convert(xlsx: Path) -> pd.DataFrame:
    grid = pd.read_excel(xlsx, header=None, dtype=object)
    norm = grid.apply(lambda col: col.map(lambda v: re.sub(r"\s+", " ", str(v)).strip().lower()))
    hits = [(i, j) for i in range(min(30, len(grid))) for j in range(grid.shape[1]) if DATE_HEADER in norm.iat[i, j]]
    if not hits:
        sys.exit(f"No '{DATE_HEADER}' header found in {xlsx} - is this the 91-day T-bill auction table?")
    row, date_col = hits[0]
    yield_cols = [j for j in range(grid.shape[1]) if YIELD_HEADER in norm.iat[row, j]]
    if not yield_cols:
        sys.exit(f"No '{YIELD_HEADER}' column found next to the date header in {xlsx}")
    body = grid.iloc[row + 1:, [date_col, yield_cols[0]]]
    body.columns = ["date", "yield_pct"]
    body["date"] = pd.to_datetime(body["date"], errors="coerce")  # financial-year labels and notes become NaT
    body["yield_pct"] = pd.to_numeric(body["yield_pct"], errors="coerce")  # "-" = no bids accepted
    out = body.dropna().sort_values("date").drop_duplicates("date", keep="last")
    return out.assign(date=out["date"].dt.strftime("%Y-%m-%d")).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xlsx", type=Path, help="the RBI table as downloaded (.xlsx)")
    ap.add_argument("--out", type=Path, default=Path("data/external/risk_free.csv"))
    args = ap.parse_args()
    out = convert(args.xlsx)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"Wrote {args.out}: {len(out)} auctions, {out['date'].iloc[0]} to {out['date'].iloc[-1]}, "
          f"yield {out['yield_pct'].min():.2f}% to {out['yield_pct'].max():.2f}%")


if __name__ == "__main__":
    main()
