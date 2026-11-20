"""Catalogue of the public NSE files the pipeline uses.

Every entry is a public end-of-day file on the NSE archive host. Nothing here is
scraped from a logged-in page. The URL templates were cross-checked against the
open-source ``nse`` and ``jugaad-data`` Python packages (see README, data sources).

==============  ==============================================================
dataset key     what it is
==============  ==============================================================
cm_bhav         Cash-market bhavcopy: OHLC, close, previous close, volume,
                traded value, ISIN for every security traded that day.
                Legacy layout before 8 July 2024, UDiFF layout from that date.
pr              Daily "PR" zip. We use three files inside it:
                  Bc*   corporate actions with ex-dates (as published that day)
                  MCAP* shares outstanding and market capitalisation
                  Bh*   securities that hit their price band (circuit)
ind_close       All-index closing snapshot (benchmarks, P/E, dividend yield).
                Published from 2012; a few days are missing on the server.
mcwb            Monthly zip with the members of the Nifty 50 and the Nifty
                Next 50 at each month-end (2008 onwards): a point-in-time
                list of the large caps.
mcap_lists      "Market capitalisation - all companies" lists the exchange
                publishes for the listing regulations (31 March 2019 onwards,
                once or twice a year). The daily market-cap file inside the
                PR zip only starts in January 2024; these lists give the
                exchange's own ranking for the years before.
fo_bhav         Derivatives bhavcopy - only to know which stocks had futures
                on a given day (point-in-time F&O eligibility). Optional.
sec_list        Security-wise price bands. Optional; history is patchy.
==============  ==============================================================
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List

# NSE switched the cash-market and F&O bhavcopy to the UDiFF layout on this date.
UDIFF_SWITCH_DATE = dt.date(2024, 7, 8)

DAILY_DATASETS = ("cm_bhav", "pr", "ind_close", "fo_bhav", "sec_list")
MONTHLY_DATASETS = ("mcwb",)
CATALOGUE_DATASETS = ("mcap_lists",)
ALL_DATASETS = DAILY_DATASETS + MONTHLY_DATASETS + CATALOGUE_DATASETS

MCWB_FIRST_MONTH = dt.date(2008, 1, 1)  # earliest monthly index file on the archive

# Exchange market-cap lists: as-on date -> file name under MCAP_LIST_DIR. The names follow no
# single pattern, so they are listed one by one (each was fetched and read on 15 Nov 2026).
# The 2019 list has ranks only (top 1000); the others give every company's market cap.
# Later lists can be added by hand: save the file from the NSE page "Market capitalisation -
# all companies" into data/raw/mcap_lists/ with the as-on date (ddmmyyyy) in its name.
MCAP_LIST_DIR = "/web/sites/default/files/inline-files"
MCAP_LIST_FILES: Dict[str, str] = {
    "2019-03-31": "Top1000_31032019.xlsx",
    "2020-03-31": "MCAP_31032020.xlsx",
    "2020-12-31": "MCAP31122020.xlsx",
    "2021-03-31": "MCAP31032021.xlsx",
    "2021-12-31": "MCAP31122021.xlsx",
    "2022-03-31": "MCAP31032022.xlsx",
    "2022-12-31": "MCAP31122022_0.xlsx",
    "2023-03-31": "MCAP31032023.xlsx",
    "2023-12-31": "MCAP31122023.xlsx",
    "2024-03-28": "MCAP28032024.xlsx",
}

# Files that are not dated: downloaded once and refreshed on demand.
STATIC_FILES: Dict[str, str] = {
    "symbolchange.csv": "/content/equities/symbolchange.csv",
    "EQUITY_L.csv": "/content/equities/EQUITY_L.csv",
    "ind_nifty500list.csv": "/content/indices/ind_nifty500list.csv",
    "ind_niftymidcap150list.csv": "/content/indices/ind_niftymidcap150list.csv",
    "ind_niftysmallcap250list.csv": "/content/indices/ind_niftysmallcap250list.csv",
    "ind_niftymicrocap250_list.csv": "/content/indices/ind_niftymicrocap250_list.csv",
    "ind_niftytotalmarket_list.csv": "/content/indices/ind_niftytotalmarket_list.csv",
}


@dataclass(frozen=True)
class RemoteFile:
    """One file to fetch: where it is on the server and where it goes on disk."""

    dataset: str
    date: dt.date | None
    url_path: str  # appended to the archive base URL
    local_rel: str  # path relative to data/raw
    kind: str  # "zip" or "csv" - used to sanity-check what the server returned


def remote_file(dataset: str, date: dt.date) -> RemoteFile:
    """URL path and local path of ``dataset`` for one trading date."""
    y = date.year
    mon = date.strftime("%b").upper()
    if dataset == "cm_bhav":
        if date < UDIFF_SWITCH_DATE:
            name = f"cm{date:%d}{mon}{y}bhav.csv.zip"
            url = f"/content/historical/EQUITIES/{y}/{mon}/{name}"
        else:
            name = f"BhavCopy_NSE_CM_0_0_0_{date:%Y%m%d}_F_0000.csv.zip"
            url = f"/content/cm/{name}"
        kind = "zip"
    elif dataset == "pr":
        name = f"PR{date:%d%m%y}.zip"
        url = f"/archives/equities/bhavcopy/pr/{name}"
        kind = "zip"
    elif dataset == "ind_close":
        name = f"ind_close_all_{date:%d%m%Y}.csv"
        url = f"/content/indices/{name}"
        kind = "csv"
    elif dataset == "fo_bhav":
        if date < UDIFF_SWITCH_DATE:
            name = f"fo{date:%d}{mon}{y}bhav.csv.zip"
            url = f"/content/historical/DERIVATIVES/{y}/{mon}/{name}"
        else:
            name = f"BhavCopy_NSE_FO_0_0_0_{date:%Y%m%d}_F_0000.csv.zip"
            url = f"/content/fo/{name}"
        kind = "zip"
    elif dataset == "sec_list":
        name = f"sec_list_{date:%d%m%Y}.csv"
        url = f"/content/equities/{name}"
        kind = "csv"
    else:
        raise KeyError(f"Unknown dataset '{dataset}'. Known: {DAILY_DATASETS}")
    return RemoteFile(dataset, date, url, f"{dataset}/{y}/{name}", kind)


def monthly_file(dataset: str, year: int, month: int) -> RemoteFile:
    """URL path and local path of a monthly ``dataset`` file; its date is the month-end."""
    if dataset != "mcwb":
        raise KeyError(f"Unknown monthly dataset '{dataset}'. Known: {MONTHLY_DATASETS}")
    first = dt.date(year, month, 1)
    month_end = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    name = f"mcwb_{first:%b}{first:%y}.zip".lower()
    return RemoteFile("mcwb", month_end, f"/content/indices/{name}", f"mcwb/{year}/{name}", "zip")


def monthly_files(dataset: str, start: dt.date, end: dt.date) -> List[RemoteFile]:
    """One file per calendar month ending inside ``[start, end]``, plus the month before
    ``start`` so that a list is already in force on the first day of the sample."""
    out: List[RemoteFile] = []
    first = max(MCWB_FIRST_MONTH, (start.replace(day=1) - dt.timedelta(days=1)).replace(day=1))
    y, m = first.year, first.month
    while dt.date(y, m, 1) <= end:
        rf = monthly_file(dataset, y, m)
        if rf.date <= end:
            out.append(rf)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def catalogue_files(dataset: str) -> List[RemoteFile]:
    """Files that exist under known names only (not one per day or month)."""
    if dataset != "mcap_lists":
        raise KeyError(f"Unknown catalogue dataset '{dataset}'. Known: {CATALOGUE_DATASETS}")
    return [
        RemoteFile("mcap_lists", dt.date.fromisoformat(as_of), f"{MCAP_LIST_DIR}/{name}", f"mcap_lists/{name}", "zip")
        for as_of, name in MCAP_LIST_FILES.items()  # an .xlsx file is a zip archive
    ]


def static_files() -> List[RemoteFile]:
    return [
        RemoteFile("static", None, url, f"static/{name}", "csv") for name, url in STATIC_FILES.items()
    ]


def candidate_dates(start: dt.date, end: dt.date, include_weekends: bool = True) -> List[dt.date]:
    """Calendar days to try.

    NSE has held sessions on Saturdays and Sundays (Budget days, Muhurat trading,
    disaster-recovery drills), so weekends are tried too; a non-trading day just
    returns "not found" and is remembered in the manifest.
    """
    days = []
    d = start
    while d <= end:
        if include_weekends or d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days
