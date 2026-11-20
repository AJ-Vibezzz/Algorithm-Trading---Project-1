"""Indian market frictions: price-band (circuit) locks, tradability, liquidity.

Why a circuit lock matters to a back-test
-----------------------------------------
Every NSE cash-market stock outside the F&O list has a daily price band (2%, 5%,
10% or 20% of the previous close). When a stock is pinned at its **upper** band
there are only buyers in the book: a new buy order does not get filled. Pinned
at the **lower** band there are only sellers: a holder cannot get out. A
momentum strategy in small caps meets both - winners that lock up when you want
in, and positions that lock down for days when you want out. Assuming you could
trade at the closing price on those days overstates returns and understates risk.

How a lock is detected from end-of-day data
-------------------------------------------
A stock is treated as locked at the close when its close equals its day's high
(or low) **and** either the exchange's own band-hit file names it, or its move
from the previous close equals one of the standard band sizes to within one
tick. The second test covers dates for which the band-hit file is unavailable.
"""

from __future__ import annotations

import numpy as np

STANDARD_BANDS = (0.02, 0.05, 0.10, 0.20)
TICK = 0.05


def circuit_lock(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    prevclose: np.ndarray,
    band_hit: np.ndarray,
) -> np.ndarray:
    """Per row: +1 locked at the upper band at the close, -1 at the lower band, 0 otherwise.

    ``band_hit`` holds 'H' / 'L' / '' from the exchange's band-hit file.
    """
    close, high, low, prevclose = (np.asarray(x, float) for x in (close, high, low, prevclose))
    with np.errstate(divide="ignore", invalid="ignore"):
        move = close / prevclose - 1.0
        # Band limits are rounded to the tick, so allow one tick of slack (plus a small floor).
        tol = np.maximum(0.0015, 1.2 * TICK / prevclose)
    near_up = np.zeros(close.shape, bool)
    near_dn = np.zeros(close.shape, bool)
    for b in STANDARD_BANDS:
        near_up |= np.abs(move - b) <= tol
        near_dn |= np.abs(move + b) <= tol
    at_high = close >= high - 1e-9
    at_low = close <= low + 1e-9
    hit = np.asarray(band_hit, dtype=object)
    lock = np.zeros(close.shape, np.int8)
    lock[at_high & (move > 0) & ((hit == "H") | near_up)] = 1
    lock[at_low & (move < 0) & ((hit == "L") | near_dn)] = -1
    return lock


def rolling_mean(a: np.ndarray, window: int, min_periods: int = 1) -> np.ndarray:
    """Trailing mean down the rows of a (days x stocks) array; NaN counts as missing."""
    import pandas as pd

    return pd.DataFrame(a).rolling(window, min_periods=min_periods).mean().to_numpy()


def rolling_median(a: np.ndarray, window: int, min_periods: int = 1) -> np.ndarray:
    import pandas as pd

    return pd.DataFrame(a).rolling(window, min_periods=min_periods).median().to_numpy()


def rolling_std(a: np.ndarray, window: int, min_periods: int = 2) -> np.ndarray:
    import pandas as pd

    return pd.DataFrame(a).rolling(window, min_periods=min_periods).std().to_numpy()


def ffill_rows(a: np.ndarray) -> np.ndarray:
    """Carry the last valid value down each column (used for 'last traded price')."""
    mask = np.isnan(a)
    idx = np.where(~mask, np.arange(a.shape[0])[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    return a[idx, np.arange(a.shape[1])[None, :]]
