"""Corporate-action text parsing and the 'final wording wins' rule."""

import numpy as np
import pandas as pd
import pytest

from momo.data.corporate_actions import build_action_table, parse_purpose, rights_factor


@pytest.mark.parametrize("text,factor", [
    ("BONUS 1:1", 0.5),
    ("BONUS ISSUE 3:2", 0.4),
    ("RESERVE BASED BONUS 2:1", 1 / 3),
    ("FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER SHARE TO RS 2/- PER SHARE", 0.2),
    ("FV SPLIT RS.10/- TO RS.2/-", 0.2),
    ("FV SPLT FRM RS 10 TO RE 1", 0.1),
    ("STOCK SPLIT FROM RE 1 TO 50 PAISE", 0.5),
    ("CONSOLIDATION FROM RE 1 TO RS 10", 10.0),
])
def test_split_and_bonus_factors(text, factor):
    p = parse_purpose(text)
    assert p.recognised
    assert p.split_factor * p.bonus_factor == pytest.approx(factor)
    assert p.dividend == 0.0, "a split must not be read as a dividend ('SUB-DIVISION' contains DIV)"


@pytest.mark.parametrize("text,amount", [
    ("DIVIDEND - RS 5 PER SHARE", 5.0),
    ("DIV-RS.2.50 PER SHARE", 2.5),
    ("INTERIM DIVIDEND - RE 1 PER SHARE", 1.0),
    ("INT DIV-RE.0.50/- PER SH", 0.5),
    ("AGM/DIV-RS.3/- PER SHARE", 3.0),
    ("INTERIM DIVIDEND - RS 4 PER SHARE AND SPECIAL DIVIDEND - RS 6 PER SHARE", 10.0),
    ("DIVIDEND RS 2 + SPECIAL RS 3 PER SHARE", 5.0),
    ("DIVIDEND - 50 PAISE PER SHARE", 0.5),
    ("Interim Dividend - Rs 1.20 Per Share", 1.2),
])
def test_dividend_amounts(text, amount):
    p = parse_purpose(text)
    assert p.recognised and p.dividend == pytest.approx(amount)
    assert p.split_factor == 1.0 and p.bonus_factor == 1.0


def test_mixed_and_special_cases():
    p = parse_purpose("BONUS 1:1 / DIVIDEND - RS 8 PER SHARE")
    assert p.bonus_factor == 0.5 and p.dividend == 8.0
    assert parse_purpose("DIVIDEND-25%").dividend_pct_fv == 25.0
    r = parse_purpose("RIGHTS 1:4 @ PREMIUM RS 90/-")
    assert (r.rights_new, r.rights_held, r.rights_premium) == (1.0, 4.0, 90.0)
    assert parse_purpose("RIGHTS 3:10 @ PAR").rights_premium == 0.0
    assert parse_purpose("DEMERGER").demerger and parse_purpose("SCHEME OF ARRANGEMENT").capital_change
    assert parse_purpose("ANNUAL GENERAL MEETING").recognised
    assert not parse_purpose("SOMETHING NOBODY HAS SEEN").recognised


def test_rights_factor():
    # 1 new share at Rs 100 for every 4 held at Rs 200: TERP = (4*200 + 100) / 5 = 180
    assert rights_factor(200, 1, 4, 100) == pytest.approx(0.9)
    assert rights_factor(200, 1, 4, 250) == 1.0, "an out-of-the-money rights issue does not dilute the price"


def test_final_wording_wins_and_events_are_not_double_counted():
    ex = pd.Timestamp("2024-03-15")
    rows = [
        # mis-stated at first, corrected five days before the ex-date
        ("AAA", ex, "BONUS 2:1", "2024-03-01", "2024-03-09"),
        ("AAA", ex, "BONUS 1:1", "2024-03-10", "2024-03-15"),
        # two genuinely different events on one ex-date
        ("BBB", ex, "FINAL DIVIDEND - RS 2 PER SHARE", "2024-03-01", "2024-03-15"),
        ("BBB", ex, "BONUS 1:2", "2024-03-01", "2024-03-15"),
        # same text listed in two yearly files
        ("CCC", pd.Timestamp("2024-01-03"), "DIVIDEND - RS 4 PER SHARE", "2023-12-20", "2023-12-31"),
        ("CCC", pd.Timestamp("2024-01-03"), "DIVIDEND - RS 4 PER SHARE", "2024-01-01", "2024-01-03"),
    ]
    listings = pd.DataFrame(rows, columns=["symbol", "ex_date", "purpose", "first_seen", "last_seen"])
    listings["series"] = "EQ"
    for c in ("first_seen", "last_seen"):
        listings[c] = pd.to_datetime(listings[c])
    table = build_action_table(listings, ["EQ"]).set_index("symbol")
    assert table.loc["AAA", "bonus_factor"] == pytest.approx(0.5), "the corrected 1:1 must win over the stale 2:1"
    assert table.loc["BBB", "bonus_factor"] == pytest.approx(2 / 3) and table.loc["BBB", "dividend"] == 2.0
    assert table.loc["CCC", "dividend"] == 4.0, "one dividend, not two"
    assert table.loc["CCC", "first_seen"] == pd.Timestamp("2023-12-20")
