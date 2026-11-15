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
