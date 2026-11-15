"""Turn NSE corporate-action "purpose" text into numbers.

NSE publishes corporate actions as free text, for example::

    BONUS 1:1
    FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER SHARE TO RS 2/- PER SHARE
    INTERIM DIVIDEND - RS 4.50 PER SHARE
    AGM/DIV-RS.3/- PER SHARE
    RIGHTS 1:4 @ PREMIUM RS 90/-

This module extracts, for each (symbol, ex-date):

* ``split_factor``  price multiplier from a face-value split or consolidation
                    (new face value / old face value, so 10 -> 2 gives 0.2)
* ``bonus_factor``  price multiplier from a bonus issue (a new shares for every
                    b held gives b / (a + b), so 1:1 gives 0.5)
* ``dividend``      rupees per share going ex on that date
* rights terms, and flags for demergers / capital restructurings

Text that cannot be understood is *counted and reported*, never guessed at:
see ``recognised`` and the data-quality report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd

# --- text normalisation ------------------------------------------------------
_CURRENCY = re.compile(r"(?:\bRS|\bRE|\bINR|₹)\.?(?=\s*\d)")
_SEGMENT_SPLIT = re.compile(r"\+|&|/|;|\bAND\b|\bALONG WITH\b")
_NUM = r"(\d+(?:\.\d+)?)"

_BONUS = re.compile(r"\bBON(?:US)?\b[^0-9:]{0,30}(\d+)\s*:\s*(\d+)")
_SPLIT = re.compile(r"(?:SPLIT|SPLT|SUB\s*-?\s*DIVISION)[^0-9]*?" + _NUM + r"[^0-9.]+?" + _NUM + r"(\s*(?:PAISE|PAISA))?")
_CONSOL = re.compile(r"CONSOLIDATION[^0-9]*?" + _NUM + r"[^0-9.]+?" + _NUM)
_RIGHTS = re.compile(r"\bRIGHTS?\b[^0-9:]{0,30}(\d+)\s*:\s*(\d+)")
_PREMIUM = re.compile(r"PREM(?:IUM)?[^0-9]{0,20}" + _NUM)
_AT_PAR = re.compile(r"(?:@|\bAT)\s*PAR\b")
# "DIV", "DIVIDEND", "INTDIV" - but not the DIVISION in "SUB-DIVISION" (a split).
_DIV_WORD = re.compile(r"DIV(?!ISION)")
_DIV_QUALIFIER = re.compile(r"\b(?:SPECIAL|SPL|INTERIM|INT|FINAL|FIN)\b")
_AMOUNT_RS = re.compile(r"\bRS\s*" + _NUM)
_AMOUNT_PAISE = re.compile(_NUM + r"\s*(?:PAISE|PAISA)\b")
_AMOUNT_PCT = re.compile(_NUM + r"\s*%")
_DEMERGER = re.compile(r"DE\s*-?\s*MERGER|SPIN\s*-?\s*OFF|HIVE\s*-?\s*OFF")
_CAPITAL = re.compile(r"SCHEME OF ARR|CAPITAL REDUCTION|REDUCTION (?:OF|IN) (?:SHARE )?CAPITAL|AMALGAMATION")
_NOT_DIVIDEND = re.compile(r"BONUS|SPLIT|SPLT|RIGHTS?|CONSOLIDATION|BUY\s*-?\s*BACK|DEMERGER")
# Purposes that are real but have no effect on the share price series.
_NO_PRICE_EFFECT = re.compile(
    r"\bAGM\b|\bEGM\b|ANNUAL GENERAL|EXTRA.?ORDINARY GENERAL|BUY\s*-?\s*BACK|INTEREST|REDEMPTION|"
    r"\bCOURT CONVENED\b|POSTAL BALLOT|\bMEETING\b"
)


def normalise_purpose(text: str) -> str:
    """Upper-case, strip '/-' decorations and unify currency markers to 'RS'."""
    t = str(text).upper().replace("/-", " ")
    t = _CURRENCY.sub(" RS ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass(frozen=True)
class ParsedAction:
    split_factor: float = 1.0
    bonus_factor: float = 1.0
    dividend: float = 0.0  # rupees per share
    dividend_pct_fv: float = 0.0  # dividend quoted as % of face value (older records)
    rights_new: float = 0.0  # a: new shares offered ...
    rights_held: float = 0.0  # ... for every b held
    rights_premium: float = np.nan  # issue premium over face value; NaN = not stated
    demerger: bool = False
    capital_change: bool = False
    recognised: bool = False  # False = the text was not understood at all


@lru_cache(maxsize=200_000)
def parse_purpose(text: str) -> ParsedAction:
    """Parse one purpose string. Cached, because the same strings repeat daily."""
    t = normalise_purpose(text)
    split_factor = bonus_factor = 1.0
    dividend = dividend_pct = 0.0
    rights_new = rights_held = 0.0
    rights_premium = np.nan
    recognised = False

    m = _BONUS.search(t)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if 0 < a <= 100 and 0 < b <= 100:
            bonus_factor = b / (a + b)
            recognised = True

    m = _SPLIT.search(t)
    if m:
        old, new = float(m.group(1)), float(m.group(2))
        if m.group(3):  # "... TO 50 PAISE"
            new /= 100.0
        if old > 0 and new > 0 and old != new:
            split_factor *= new / old
            recognised = True

    m = _CONSOL.search(t)
    if m and "SPLIT" not in t:
        old, new = float(m.group(1)), float(m.group(2))
        if old > 0 and new > 0 and old != new:
            split_factor *= new / old
            recognised = True

    m = _RIGHTS.search(t)
    if m:
        rights_new, rights_held = float(m.group(1)), float(m.group(2))
        p = _PREMIUM.search(t)
        if p:
            rights_premium = float(p.group(1))
        elif _AT_PAR.search(t):
            rights_premium = 0.0
        recognised = True

    if _DIV_WORD.search(t):
        previous_was_dividend = False
        for seg in _SEGMENT_SPLIT.split(t):
            has_word = bool(_DIV_WORD.search(seg))
            continues = previous_was_dividend and bool(_DIV_QUALIFIER.search(seg)) and not _NOT_DIVIDEND.search(seg)
            if not (has_word or continues):
                previous_was_dividend = False
                continue
            previous_was_dividend = True
            rs = _AMOUNT_RS.search(seg)
            paise = _AMOUNT_PAISE.search(seg)
            pct = _AMOUNT_PCT.search(seg)
            if rs:
                dividend += float(rs.group(1))
            elif paise:
                dividend += float(paise.group(1)) / 100.0
            elif pct:
                dividend_pct += float(pct.group(1))
        recognised = True  # a dividend with no stated amount is still a known event type

    demerger = bool(_DEMERGER.search(t))
    capital_change = bool(_CAPITAL.search(t))
    if demerger or capital_change or _NO_PRICE_EFFECT.search(t):
        recognised = True

    return ParsedAction(
        split_factor=split_factor,
        bonus_factor=bonus_factor,
        dividend=dividend,
        dividend_pct_fv=dividend_pct,
        rights_new=rights_new,
        rights_held=rights_held,
        rights_premium=rights_premium,
        demerger=demerger,
        capital_change=capital_change,
        recognised=recognised,
    )


ACTION_COLUMNS = [
    "symbol", "ex_date", "split_factor", "bonus_factor", "dividend", "dividend_pct_fv",
    "rights_new", "rights_held", "rights_premium", "demerger", "capital_change",
    "n_unrecognised", "purposes", "first_seen",
]


def build_action_table(
    listings: pd.DataFrame,
    equity_series: Optional[list] = None,
    tolerance_days: int = 3,
) -> pd.DataFrame:
    """Collapse corporate-action listings into one row per (symbol, ex-date).

    The exchange lists the same event every day from its announcement until it
    goes ex, sometimes with corrected wording. To avoid counting an event twice
    we keep, for each (symbol, ex-date), only the wording that was **still being
    listed at the end** (its ``last_seen`` is within ``tolerance_days`` of the
    latest for that event) - the exchange's final word - and then combine the
    distinct events in it (factors multiply, dividends add).

    ``listings`` needs ``symbol``, ``ex_date``, ``purpose`` and either
    ``first_seen``/``last_seen`` (as written by ``ingest``) or ``seen_date``.
    """
    if listings is None or listings.empty:
        return pd.DataFrame(columns=ACTION_COLUMNS)
    ca = listings.copy()
    if "last_seen" not in ca.columns:
        seen = ca["seen_date"] if "seen_date" in ca.columns else pd.NaT
        ca["first_seen"] = seen
        ca["last_seen"] = seen
    if equity_series and "series" in ca.columns:
        ca = ca[ca["series"].isin(list(equity_series) + [""])]
    ca = ca.dropna(subset=["ex_date"])
    if ca.empty:
        return pd.DataFrame(columns=ACTION_COLUMNS)

    key = ["symbol", "ex_date"]
    # The same text can come from two yearly files (announced in December, ex in January).
    ca = ca.groupby(key + ["purpose"], as_index=False).agg(first_seen=("first_seen", "min"),
                                                          last_seen=("last_seen", "max"))
    final = ca.groupby(key)["last_seen"].transform("max")
    keep = ca["last_seen"].isna() | final.isna() | (ca["last_seen"] >= final - pd.Timedelta(days=tolerance_days))
    ca = ca[keep]

    parsed = pd.DataFrame([asdict(parse_purpose(p)) for p in ca["purpose"]], index=ca.index)
    ca = pd.concat([ca[key + ["purpose", "first_seen"]], parsed], axis=1)
    ca["unrecognised"] = ~ca["recognised"]

    grouped = ca.groupby(key, sort=True)
    out = grouped.agg(
        split_factor=("split_factor", "prod"),
        bonus_factor=("bonus_factor", "prod"),
        dividend=("dividend", "sum"),
        dividend_pct_fv=("dividend_pct_fv", "sum"),
        rights_new=("rights_new", "max"),
        rights_held=("rights_held", "max"),
        rights_premium=("rights_premium", "max"),
        demerger=("demerger", "any"),
        capital_change=("capital_change", "any"),
        n_unrecognised=("unrecognised", "sum"),
        purposes=("purpose", lambda s: " || ".join(sorted(set(s)))),
        first_seen=("first_seen", "min"),
    ).reset_index()
    return out[ACTION_COLUMNS]


def rights_factor(cum_price: float, new: float, held: float, issue_price: float) -> float:
    """Price multiplier on the ex-rights date (theoretical ex-rights price / cum price).

    ``new`` shares are offered at ``issue_price`` for every ``held`` shares.
    Returns 1.0 when the issue is not in the money (no dilution of price).
    """
    if not (cum_price > 0 and new > 0 and held > 0 and issue_price >= 0):
        return 1.0
    if issue_price >= cum_price:
        return 1.0
    terp = (held * cum_price + new * issue_price) / (held + new)
    return terp / cum_price
