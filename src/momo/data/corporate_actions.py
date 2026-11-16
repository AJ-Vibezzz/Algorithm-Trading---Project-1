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
# The purpose field of the exchange file is short (about 25 characters), so its texts are heavily
# abbreviated, run words together and are sometimes cut off:
#     BON1:1/FV SPL RS.10TORE.1      FVSPLTFRM RS 10 TO RE 1      DIV-RS 2/FV-SPTLRS10TORS2
#     AGM/DIV-FIN RS3+INT RS120      INTDIV-RS5SPLDIV-RS10        FV SPLIT FROM RS10 TO RS
# Every pattern below was written against the texts found in the real files of 2010-2026.
_NUM = r"(\d+(?:\.\d+)?|\.\d+)"
_GLUE = r"(?:(?<![A-Z])|(?<=DIV)|(?<=DV)|(?<=SPTL)|(?<=SPLT)|(?<=SPLIT)|(?<=SPL)|(?<=FRM)|(?<=FROM)|(?<=FIN)|(?<=INT)|(?<=PRM)|(?<=PREM)|(?<=PREMIUM)|(?<=PAR))"
_CURRENCY = re.compile(_GLUE + r"(?:RS|RE|INR)\.?\s*-?\s*(?=\.?\d)|₹\s*")  # also "RS - 20"
_TO_NUMBER = re.compile(r"(?<=\d)\s*TO\s*(?:RS|RE)?\.?\s*(?=\.?\d)")  # "RS10TORE1" -> "RS10 TO 1"
_TO_NOTHING = re.compile(r"(?<=\d)\s*TO\s*(?:RS|RE)\b\.?(?!\s*\d)")   # cut off: "FROM RS10 TO RS"
_ORDINAL = re.compile(r"\b\d+\s*(?:ST|ND|RD|TH)\b")                   # "2ND INTERIM DIVIDEND"

_BONUS = re.compile(r"(?<![A-Z])BON(?:US|S)?(?![A-Z])\s*(?:ISSUE|SHARES?)?\s*[-@:]?\s*(\d+)\s*:\s*(\d+)")
_BONUS_WORD = re.compile(r"(?<![A-Z])BON(?:US|S)?(?![A-Z])")
_BONUS_NOT_SHARES = re.compile(r"BON\w*\s*[-:]?\s*(?:DEB|NCD|PREF|NCRPS)|\bDVR\b|\bDEB\b")  # bonus debentures, DVRs
_SPLIT_WORD = r"(?:SPL(?:IT|T)?|SPTL?|FVS|SUB\s*-?\s*DIV(?:ISION)?|SUBDIVISION)"
# keyword, a little filler ("(SUB-DIVISION) - FROM RS"), the old value, "TO", the new value (may be cut off)
_SPLIT = re.compile(_SPLIT_WORD + r"[^0-9|]{0,34}?" + _NUM + r"\s*(PAISE|PAISA|PS)?\s*(?:(?:PER|PR)\s*SH\w*|EACH)?\s*TO\s*"
                    r"(?:RS)?\s*" + _NUM + r"?\s*(PAISE|PAISA|PS)?")
_CONSOL = re.compile(r"CONSOL\w*[^0-9|]{0,34}?" + _NUM + r"\s*(?:(?:PER|PR)\s*SH\w*|EACH)?\s*TO\s*(?:RS)?\s*" + _NUM)
_RIGHTS_WORD = r"(?<![A-Z])(?:RIGHTS?|RHTS?|RGHTS?|RGTS?|RH)(?![A-Z])"
_RIGHTS = re.compile(_RIGHTS_WORD + r"[\s@:\-]*(?:EQ)?\s*(\d+)\s*:\s*(\d+)")
_RIGHTS_ANY = re.compile(_RIGHTS_WORD)
_RIGHTS_OTHER = re.compile(r"CCD|NCD|WRNT|WARRANT|PARTLY|\bDEB\b|\dW\b")  # rights to something other than plain shares
_PREMIUM = re.compile(r"(?<![A-Z])P(?:R|E)[A-Z]{0,5}M(?![A-Z])[\s@]*(?:RS)?\s*" + _NUM)  # PRM, PREM, PREMIUM, PREUM ...
_AT_PAR = re.compile(r"(?:@|\bAT)\s*PAR\b")
# "DIV", "DIVIDEND", "INTDIV", "SPLDIV", "DV" - but not the DIV of "SUB DIV(ISION)" (a split).
_DIV_WORD = re.compile(r"(?<!SUB)(?<!SUB )(?<!SUB-)DIV(?!ISION)|(?<![A-Z])DV(?![A-Z])")
_AMOUNT_RS = re.compile(r"\bRS\s*" + _NUM)
_AMOUNT_PAISE = re.compile(_NUM + r"\s*(?:PAISE|PAISA)\b")
_AMOUNT_PCT = re.compile(_NUM + r"\s*%")
_AMOUNT_BARE = re.compile(r"(?:DIV(?:IDEND|D)?|DV|FIN(?:AL)?|INT(?:ERIM|RM)?|SPL|SPECIAL)\s*[-:]?\s*" + _NUM + r"(?!\s*[:%\d])")
_DEMERGER = re.compile(r"DE\s*-?\s*MERGER|SPIN\s*-?\s*OFF|HIVE\s*-?\s*OFF")
_CAPITAL = re.compile(r"\bSCH\w*\.?\s+(?:OF\s+)?A\w+|ARRANGEMENT|ARRGMNT|CAP(?:ITAL)?\.?\s*RED|"
                      r"REDUCTION\W+(?:(?:OF|IN)\W+)?(?:SHARE )?CAP|AMALG|(?<!DE)(?<!DE-)MERGER")
# A dividend written without the word: "FIN RS 18+SPL RS 25", "INT RS 5 PER SHARE", "INT IDV RS 1 PER SHARE".
_DIV_IMPLIED = re.compile(r"^(?:\W*(?:FIN(?:AL)?|INT(?:ERIM)?|SPL|SPECIAL|IDV)\W*)+RS " + _NUM
                          + r"(?:\W*(?:PER|PR|P)\W*SH\w*)?(?:\W+(?:FIN(?:AL)?|INT(?:ERIM)?|SPL|SPECIAL)\W*RS "
                          + _NUM + r"(?:\W*(?:PER|PR|P)\W*SH\w*)?)*\W*$")
# Purposes that are real but have no effect on the share price series.
_NO_PRICE_EFFECT = re.compile(
    r"\bAGM\b|\bEGM\b|\bEOGM\b|ANNUAL GENERAL|EXTRA.?ORDINARY GENERAL|GENERAL MEETING|BUY\s*-?\s*BACK|INTEREST|"
    r"REDEMP|\bCOURT CONVENED\b|POSTAL BALLOT|\bMEETING\b|CLOSURE|CLOSING|E\s*-?\s*VOTING|\bDISTRIBUTION\b|"
    r"ELECTION|PURPOSE|\bEXIT\b|DELIST|CONV TO"
)


def normalise_purpose(text: str) -> str:
    """Upper-case, drop '/-' decorations and ordinals, and give the run-together forms their spaces.

    ``RS.10TORE.1`` -> ``RS 10 TO 1``; ``DIVRS10`` -> ``DIV RS 10``; a currency
    marker is always written ``RS`` followed by a space.
    """
    t = str(text).upper().replace("/-", " ")
    t = _ORDINAL.sub(" ", t)
    t = _TO_NUMBER.sub(" TO ", t)
    t = _TO_NOTHING.sub(" TO ", t)
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
    # A split or bonus is announced but its ratio is missing (the text was cut off). panel.py then
    # recovers the ratio from the price on the ex-date, and logs that it did.
    split_unknown: bool = False
    bonus_unknown: bool = False
    old_face_value: float = np.nan  # face values named in a split text, when both or the old one are given
    new_face_value: float = np.nan
    dividend_unstated: bool = False  # a dividend with no amount in the text ("INTERIM DIVIDEND")


@lru_cache(maxsize=200_000)
def parse_purpose(text: str) -> ParsedAction:
    """Parse one purpose string. Cached, because the same strings repeat daily."""
    t = normalise_purpose(text)
    split_factor = bonus_factor = 1.0
    dividend = dividend_pct = 0.0
    rights_new = rights_held = 0.0
    rights_premium = old_fv = new_fv = np.nan
    recognised = split_unknown = bonus_unknown = dividend_unstated = False
    rest = t  # what is left for the dividend reader once the other events are cut out

    # --- bonus -----------------------------------------------------------------
    not_shares = bool(_BONUS_NOT_SHARES.search(t))
    m = _BONUS.search(t)
    if m and not not_shares:
        a, b = float(m.group(1)), float(m.group(2))
        if 0 < a <= 100 and 0 < b <= 100:
            bonus_factor = b / (a + b)
            recognised = True
        rest = rest.replace(m.group(0), " | ")
    elif _BONUS_WORD.search(t):
        recognised = True
        bonus_unknown = not not_shares  # "AGM/DIV-RS 29.50/BONUS": the ratio did not fit in the field

    # --- face-value split / consolidation -------------------------------------------
    m = _SPLIT.search(t)
    c = None if m else _CONSOL.search(t)
    if m:
        old = float(m.group(1)) / (100.0 if m.group(2) else 1.0)
        recognised = True
        if m.group(3):
            new = float(m.group(3)) / (100.0 if m.group(4) else 1.0)
            if old > 0 and new > 0 and old != new and 0.001 <= new / old <= 1000:
                split_factor, old_fv, new_fv = new / old, old, new
        else:
            split_unknown, old_fv = True, old  # "FV SPLIT FROM RS10 TO RS": cut off before the new value
        rest = rest.replace(m.group(0), " | ")
    elif c:
        old, new = float(c.group(1)), float(c.group(2))
        if old > 0 and new > 0 and old != new and 0.001 <= new / old <= 1000:
            split_factor, old_fv, new_fv = new / old, old, new
            recognised = True
        rest = rest.replace(c.group(0), " | ")

    # --- rights --------------------------------------------------------------------
    if _RIGHTS_ANY.search(t):
        recognised = True
        m = _RIGHTS.search(t)
        if m and not _RIGHTS_OTHER.search(t):
            rights_new, rights_held = float(m.group(1)), float(m.group(2))
            prem = _PREMIUM.search(t)
            if prem:
                rights_premium = float(prem.group(1))
                rest = rest.replace(prem.group(0), " | ")
            elif _AT_PAR.search(t):
                rights_premium = 0.0
                rest = _AT_PAR.sub(" | ", rest)
            rest = rest.replace(m.group(0), " | ")
        else:
            rest = _RIGHTS_ANY.sub(" | ", rest)

    # --- dividends: every amount in what is left belongs to a dividend ---------------------
    if _DIV_WORD.search(rest) or _DIV_IMPLIED.match(rest):
        recognised = True
        # Amounts are written "RS 5" or bare after the word ("DIV-2.5", "FIN 7.5+SPL 7.5"); one text
        # can mix both ("DIV-RS 12.50+SPL 0.50"), so the two kinds are added up.
        amounts = [float(x) for x in _AMOUNT_RS.findall(rest)] + [float(x) for x in _AMOUNT_BARE.findall(rest)]
        paise = [float(x) for x in _AMOUNT_PAISE.findall(rest)]
        pct = [float(x) for x in _AMOUNT_PCT.findall(rest)]
        if paise:
            dividend = sum(paise) / 100.0
        elif pct:
            dividend_pct = sum(pct)
        elif amounts:
            dividend = sum(amounts)
        else:
            dividend_unstated = True

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
        split_unknown=split_unknown,
        bonus_unknown=bonus_unknown,
        old_face_value=old_fv,
        new_face_value=new_fv,
        dividend_unstated=dividend_unstated,
    )


ACTION_COLUMNS = [
    "symbol", "ex_date", "split_factor", "bonus_factor", "dividend", "dividend_pct_fv",
    "rights_new", "rights_held", "rights_premium", "demerger", "capital_change",
    "split_unknown", "bonus_unknown", "old_face_value", "new_face_value", "dividend_unstated",
    "n_unrecognised", "purposes", "first_seen",
]
WITHDRAWN_DAYS = 10  # a listing that stopped this long before its ex-date was withdrawn or re-dated

_SKELETON_DROP = re.compile(r"ANNUAL GENERAL MEETING|ANN(?:UAL)? BOOK CL\w+|\bAGM\b|\b(?:PER|PR|P) ?SH\w*")


def _skeleton(purpose: str) -> str:
    """The wording of a purpose with its numbers and decorations removed.

    ``AGM/DIV-RS 0.20 PER SH`` and ``DIV-RS 0.20 PER SHARE`` give the same
    skeleton (``DIVRS#``): they are two listings of one event. ``INT DIV - RS 41``
    and ``SPL DIV - RS 110`` give different ones: two events.
    """
    t = _SKELETON_DROP.sub(" ", normalise_purpose(purpose)).replace("DIVIDEND", "DIV")
    t = re.sub(_NUM, "#", t)
    return re.sub(r"[^A-Z#:]", "", t)


def build_action_table(
    listings: pd.DataFrame,
    equity_series: Optional[list] = None,
    tolerance_days: int = WITHDRAWN_DAYS,
) -> pd.DataFrame:
    """Collapse corporate-action listings into one row per (symbol, ex-date).

    The exchange lists an event every day from its announcement until about
    its ex-date, and what it lists changes: wording is corrected, an amount is
    added, a bonus joins a dividend. How the real listings behave (2010-2026):

    * A **correction replaces** the earlier text: the old text stops being
      listed and the new one starts the next day. So a text is dropped when
      another text for the same ex-date first appears after it was last seen.
    * **Separate events** on one ex-date (a bonus and a dividend, an interim
      and a special dividend) are listed side by side, on overlapping days.
      They are all kept and combined: factors multiply, dividends add.
    * The same event is sometimes listed twice side by side in slightly
      different words. Texts with the same wording once numbers are removed
      count as one event, and the one listed last is used.
    * A text that stopped being listed more than ``tolerance_days`` before its
      ex-date was withdrawn (cancelled, or moved to another ex-date).

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
    # The same text comes under several series (EQ and BE) and can span two yearly files.
    ca = ca.groupby(key + ["purpose"], as_index=False).agg(first_seen=("first_seen", "min"),
                                                          last_seen=("last_seen", "max"))
    withdrawn = ca["last_seen"].notna() & (ca["last_seen"] < ca["ex_date"] - pd.Timedelta(days=tolerance_days))
    ca = ca[~withdrawn]
    newest_start = ca.groupby(key)["first_seen"].transform("max")
    replaced = ca["last_seen"].notna() & newest_start.notna() & (newest_start > ca["last_seen"])
    ca = ca[~replaced].copy()
    if ca.empty:
        return pd.DataFrame(columns=ACTION_COLUMNS)
    ca["skeleton"] = ca["purpose"].map(_skeleton)
    ca = (ca.sort_values(key + ["skeleton", "first_seen", "last_seen", "purpose"])
            .drop_duplicates(key + ["skeleton"], keep="last"))

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
        split_unknown=("split_unknown", "any"),
        bonus_unknown=("bonus_unknown", "any"),
        old_face_value=("old_face_value", "max"),
        new_face_value=("new_face_value", "min"),
        dividend_unstated=("dividend_unstated", "any"),
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
