"""Small shared helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd


def to_ns(df: pd.DataFrame) -> pd.DataFrame:
    """Cast every datetime column to nanosecond resolution (in place) and return ``df``.

    Different pandas versions and file formats hand back seconds, milliseconds
    or microseconds; joins and as-of merges need both sides to match, so the
    pipeline standardises on nanoseconds at every boundary.
    """
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            if getattr(df[col].dt, "tz", None) is not None:
                df[col] = df[col].dt.tz_localize(None)
            df[col] = df[col].astype("datetime64[ns]")
    return df


def ns_index(index) -> pd.DatetimeIndex:
    """A DatetimeIndex at nanosecond resolution."""
    return pd.DatetimeIndex(pd.to_datetime(index)).astype("datetime64[ns]")


def month_end_flags(dates: pd.DatetimeIndex) -> np.ndarray:
    """True on the last trading day of each calendar month."""
    ym = dates.year * 12 + dates.month
    flags = np.zeros(len(dates), bool)
    flags[:-1] = ym[1:] != ym[:-1]
    flags[-1] = True
    return flags


def week_end_flags(dates: pd.DatetimeIndex) -> np.ndarray:
    """True on the last trading day of each ISO week."""
    iso = dates.isocalendar()
    wk = (iso["year"].to_numpy().astype(int) * 100 + iso["week"].to_numpy().astype(int))
    flags = np.zeros(len(dates), bool)
    flags[:-1] = wk[1:] != wk[:-1]
    flags[-1] = True
    return flags


def quarter_end_flags(dates: pd.DatetimeIndex) -> np.ndarray:
    """True on the last trading day of each calendar quarter."""
    q = dates.year * 4 + (dates.month - 1) // 3
    flags = np.zeros(len(dates), bool)
    flags[:-1] = q[1:] != q[:-1]
    flags[-1] = True
    return flags
