"""Corporate-action text parsing and the rules for turning daily listings into events.

The real bhavcopy does not adjust the previous close on an ex-date, so every
split, bonus, rights issue and dividend in the returns comes from these texts.
"""

import numpy as np
import pandas as pd
import pytest

from momo.config import DataConfig
from momo.data.corporate_actions import build_action_table, parse_purpose, rights_factor
from momo.data.panel import _compute_returns, _face_values


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


# --------------------------------------------------------------------------- texts as the exchange writes them
# Shapes taken from the exchange's files of 2010-2021. The purpose field holds about 25 characters,
# so words are abbreviated, run together and sometimes cut off.
@pytest.mark.parametrize("text, split, bonus, dividend", [
    ("BON1:1/FV SPL RS.10TORE.1", 0.1, 0.5, 0.0),
    ("BONUS1:1/FV SPL-RS10TORS5", 0.5, 0.5, 0.0),
    ("AGM/BON 2:1/SPL RS10TORE1", 0.1, 1 / 3, 0.0),
    ("AGM/DIVRS10/STK SPT10 TO1", 0.1, 1.0, 10.0),
    ("INT DIV-2.5/SPL RS10TORE1", 0.1, 1.0, 2.5),
    ("DIV-RS 2/FV-SPTLRS10TORS2", 0.2, 1.0, 2.0),
    ("DIV-RS.6/FV SPL RS2TORE1", 0.5, 1.0, 6.0),
    ("FVSPLTFRM RS 100 TO RE 1", 0.01, 1.0, 0.0),
    ("FV SPLT-FRM-RS 10 TO RS 5", 0.5, 1.0, 0.0),
    ("SUB DIV FRM RS 10 TO RS 2", 0.2, 1.0, 0.0),
    ("CONSOLIDATION RE1 TO RS10", 10.0, 1.0, 0.0),
    ("DV-FIN 7.5+SPL 7.5/BON1:1", 1.0, 0.5, 15.0),
    ("BONUS 1:1/DIV-RS 29", 1.0, 0.5, 29.0),
    ("AGM/DIV-FIN RS3+INT RS120", 1.0, 1.0, 123.0),
    ("INTDIV-RS3.5SPLDIV-RS3.5", 1.0, 1.0, 7.0),
    ("AGM/DIV-RS 12.50+SPL 0.50", 1.0, 1.0, 13.0),
    ("AGM/SPLDIV-RS416/DIVRS24", 1.0, 1.0, 440.0),
    ("2ND INT DIV-RS.4/- PR SHR", 1.0, 1.0, 4.0),
    ("INTDIV-RS 974 PER SH", 1.0, 1.0, 974.0),
    ("FIN RS 18+SPL RS 25", 1.0, 1.0, 43.0),
    ("INTDIV RS - 20 PR SH", 1.0, 1.0, 20.0),
])
def test_texts_in_the_exchange_s_own_shorthand(text, split, bonus, dividend):
    p = parse_purpose(text)
    assert p.recognised
    assert p.split_factor == pytest.approx(split) and p.bonus_factor == pytest.approx(bonus)
    assert p.dividend == pytest.approx(dividend)


def test_cut_off_texts_say_what_is_missing_instead_of_guessing():
    split = parse_purpose("FV SPLIT FROM RS10 TO RS")
    assert split.recognised and split.split_unknown and split.split_factor == 1.0 and split.old_face_value == 10.0
    bonus = parse_purpose("AGM/DIV-RS 29.50/BONUS")
    assert bonus.bonus_unknown and bonus.bonus_factor == 1.0 and bonus.dividend == 29.5
    for text in ("INTERIM DIVIDEND", "2ND INTERIM DIVIDEND", "AGM/DIV/SPLDIV", "DIVIDEND"):
        p = parse_purpose(text)
        assert p.recognised and p.dividend_unstated and p.dividend == 0.0, "an ordinal is not an amount"


def test_things_that_look_like_a_bonus_or_a_dividend_but_are_not():
    debentures = parse_purpose("SCH OF AGMT-BONUS DEB 6:1")
    assert debentures.bonus_factor == 1.0 and not debentures.bonus_unknown and debentures.capital_change
    assert parse_purpose("BON 1 DVR : 20 EQ SHARES").bonus_factor == 1.0
    assert parse_purpose("AGM/DIV-RS3/BON DEB 1:1").dividend == 3.0
    for text in ("INTEREST PAYMENT", "INT PAYMENT/REDEMPTN", "BUYBACK", "ANNUAL BOOK CLOSURE", "E-VOTING"):
        p = parse_purpose(text)
        assert p.recognised and p.dividend == 0.0 and not p.dividend_unstated
    reduction = parse_purpose("CAP RED RS 10 TO RS 2")
    assert reduction.capital_change and reduction.split_factor == 1.0
    assert parse_purpose("SCH OF ARRGMNT").capital_change and parse_purpose("MERGER").capital_change
    both = parse_purpose("MERGER/DEMERGER")
    assert both.demerger and both.capital_change
    assert parse_purpose("DE-MERGER").demerger and not parse_purpose("DEMERGER").capital_change


@pytest.mark.parametrize("text, new, held, premium", [
    ("RIGHTS 67:66 @ PRM RS 51", 67, 66, 51.0),
    ("RHTS 2:5 @PREMIUM RS.140", 2, 5, 140.0),
    ("RIGHTS-2:5@PREUM RS 380", 2, 5, 380.0),
    ("RIGHTS 3:20 @PRMRS 123/-", 3, 20, 123.0),
    ("RGHTS 30:37 @PRM RS 53/-", 30, 37, 53.0),
    ("RIGHTS@3:2 AT PAR", 3, 2, 0.0),
])
def test_rights_terms_in_every_spelling(text, new, held, premium):
    p = parse_purpose(text)
    assert (p.rights_new, p.rights_held, p.rights_premium) == (new, held, premium)
    assert p.dividend == 0.0, "the premium is not a dividend"


def test_rights_special_cases():
    combined = parse_purpose("DIV-RS1.8/RHTS1:3@PRM30/-")
    assert combined.dividend == 1.8 and (combined.rights_new, combined.rights_held, combined.rights_premium) == (1, 3, 30)
    unpriced = parse_purpose("RIGHTS 2:7")
    assert unpriced.rights_new == 2 and np.isnan(unpriced.rights_premium)
    other = parse_purpose("RIGHTS: 4CCD:5EQ")  # rights to convertible debentures, not to shares
    assert other.recognised and other.rights_new == 0


# --------------------------------------------------------------------------- daily listings -> events
def _table(rows):
    listings = pd.DataFrame(rows, columns=["symbol", "series", "ex_date", "purpose", "first_seen", "last_seen"])
    for c in ("ex_date", "first_seen", "last_seen"):
        listings[c] = pd.to_datetime(listings[c])
    return build_action_table(listings, ["EQ", "BE"]).set_index("symbol")


def test_a_correction_days_before_the_ex_date_replaces_the_earlier_text():
    table = _table([
        # the dividend was restated (an interim was added) four days before it went ex
        ("NNN", "EQ", "2015-05-19", "AGM/DIV-RS 12.5 PER SHARE", "2015-04-07", "2015-05-15"),
        ("NNN", "EQ", "2015-05-19", "AGM/DIVRS12.5/INTDIVRS14", "2015-05-18", "2015-05-21"),
        # a typing error corrected the day before
        ("KKK", "EQ", "2020-09-18", "AGM/DIV-RS 0.05 PER SH", "2020-08-06", "2020-09-16"),
        ("KKK", "EQ", "2020-09-18", "AGM/DIV-RS 0.50 PER SH", "2020-09-17", "2020-09-25"),
    ])
    assert table.loc["NNN", "dividend"] == pytest.approx(26.5), "not 12.5 + 26.5"
    assert table.loc["KKK", "dividend"] == pytest.approx(0.5)


def test_events_listed_side_by_side_are_all_kept():
    table = _table([
        # a bonus and a dividend on one ex-date: the bonus row leaves the list on the ex-date,
        # the dividend row stays until the book closure ends
        ("WWW", "EQ", "2018-08-08", "BONUS 1:1", "2018-07-05", "2018-08-08"),
        ("WWW", "BE", "2018-08-08", "BONUS 1:1", "2018-07-05", "2018-08-08"),
        ("WWW", "EQ", "2018-08-08", "AGM/DIV-RS 9 PER SHARE", "2018-07-05", "2018-08-13"),
        ("WWW", "BE", "2018-08-08", "AGM/DIV-RS 9 PER SHARE", "2018-07-05", "2018-08-13"),
        # an interim and a special dividend, two rows
        ("PPP", "EQ", "2018-11-20", "INT DIV - RS 41 PER SH", "2018-11-15", "2018-11-21"),
        ("PPP", "EQ", "2018-11-20", "SPL DIV - RS 110 PER SH", "2018-11-15", "2018-11-21"),
    ])
    assert table.loc["WWW", "bonus_factor"] == pytest.approx(0.5) and table.loc["WWW", "dividend"] == 9.0
    assert table.loc["PPP", "dividend"] == pytest.approx(151.0)


def test_one_event_listed_twice_side_by_side_counts_once():
    table = _table([
        ("DDD", "EQ", "2019-09-13", "AGM/DIV-RS 0.20 PER SH", "2019-09-04", "2019-09-20"),
        ("DDD", "EQ", "2019-09-13", "DIV-RS 0.20 PER SH", "2019-09-06", "2019-09-11"),
        # same wording, corrected amount, both rows left on the list
        ("III", "EQ", "2014-07-24", "AGM/DIV 4+INT DIV 4.50", "2014-07-23", "2014-08-01"),
        ("III", "EQ", "2014-07-24", "AGM/DIV4.40+INT DIV 4.50", "2014-07-24", "2014-08-01"),
    ])
    assert table.loc["DDD", "dividend"] == pytest.approx(0.20)
    assert table.loc["III", "dividend"] == pytest.approx(8.90), "the row listed last is the corrected one"


def test_a_listing_withdrawn_well_before_its_ex_date_is_not_an_event():
    table = _table([
        ("XXX", "EQ", "2015-06-15", "BONUS 1:1", "2015-05-06", "2015-05-20"),  # re-dated: see next row
        ("XXX", "EQ", "2015-06-22", "BONUS 1:1", "2015-05-21", "2015-06-22"),
    ])
    assert len(table) == 1 and table["ex_date"].iloc[0] == pd.Timestamp("2015-06-22")


# --------------------------------------------------------------------------- the return on an ex-date
def _returns(**columns):
    """Run the return calculation on hand-made rows (one row = one stock-day)."""
    n = len(next(iter(columns.values())))
    base = {
        "face_value": np.nan, "split_factor": np.nan, "bonus_factor": np.nan, "rights_new": np.nan,
        "rights_held": np.nan, "rights_premium": np.nan, "dividend": np.nan, "dividend_pct_fv": np.nan,
        "demerger": False, "capital_change": False, "split_unknown": False, "bonus_unknown": False,
        "old_face_value": np.nan,
    }
    df = pd.DataFrame({**{k: [v] * n for k, v in base.items()}, **columns})
    df["prevclose"] = df["close_prev"]  # the real file: previous close left unadjusted
    return _compute_returns(df, DataConfig())


def test_a_very_large_dividend_is_used_when_it_explains_the_fall_and_dropped_when_it_does_not():
    out = _returns(close_prev=[985.65, 100.0, 100.0], close=[12.20, 101.0, 97.5], dividend=[974.0, 60.0, 2.0])
    assert out["adj_source"].tolist() == ["large_dividend", "large_dividend_rejected", ""]
    assert out["ret_total"].iloc[0] == pytest.approx((12.20 + 974.0) / 985.65 - 1), "a real special dividend"
    assert out["ret_total"].iloc[1] == pytest.approx(0.01), "a misread amount must not create a 61% gain"
    assert out["ret_total"].iloc[2] == pytest.approx(-0.005), "an ordinary dividend is always added back"


def test_ratio_missing_from_a_cut_off_text_is_recovered_from_the_price_and_labelled():
    out = _returns(close_prev=[1975.05, 2038.15, 500.0], close=[990.45, 427.40, 510.0],
                   bonus_unknown=[True, False, True], split_unknown=[False, True, False],
                   old_face_value=[np.nan, 10.0, np.nan], dividend=[29.5, 0.0, 0.0])
    assert out["adj_source"].tolist()[:2] == ["ca_ratio_inferred", "ca_ratio_inferred"]
    assert out["ca_factor"].iloc[0] == pytest.approx(0.5), "a 1:1 bonus"
    assert out["ca_factor"].iloc[1] == pytest.approx(0.2), "Rs 10 to Rs 2"
    assert out["ret_total"].iloc[0] == pytest.approx((990.45 + 29.5) / (1975.05 * 0.5) - 1)
    assert out["ca_factor"].iloc[2] == 1.0 and out["ret_price"].iloc[2] == pytest.approx(0.02), \
        "no standard ratio fits a 2% move: nothing is invented"


def test_rights_issue_price_uses_the_face_value_and_says_so_when_it_is_unknown():
    out = _returns(close_prev=[200.0, 200.0, 200.0], close=[180.0, 178.0, 180.0], rights_new=[1.0, 1.0, 1.0],
                   rights_held=[4.0, 4.0, 4.0], rights_premium=[90.0, 90.0, np.nan], face_value=[10.0, np.nan, 10.0])
    assert out["ca_factor"].iloc[0] == pytest.approx((4 * 200 + 100) / 5 / 200), "issue price 90 + 10"
    assert out["ca_factor"].iloc[1] == pytest.approx((4 * 200 + 90) / 5 / 200)
    assert out["adj_source"].tolist() == ["ca_text_only", "ca_text_rights_face_value_unknown", "rights_not_adjusted"]


def test_only_moves_of_more_than_a_few_ticks_are_flagged_for_review():
    out = _returns(close_prev=[0.15, 200.35, 0.30], close=[0.20, 58.45, 0.60])
    assert out["adj_source"].tolist() == ["", "unexplained_jump", "unexplained_jump"], \
        "Rs 0.15 to 0.20 is one tick; a 71% fall and a doubling are not"


def test_face_value_is_rebuilt_along_the_chain_of_splits(tmp_path):
    cfg = DataConfig(root=str(tmp_path))
    cfg.interim.mkdir(parents=True)
    pd.DataFrame({"symbol": ["BBB"], "isin": ["INE000B01029"], "face_value": [1.0],
                  "listing_date": [pd.Timestamp("2001-01-01")]}).to_parquet(cfg.interim / "equity_list.parquet")
    df = pd.DataFrame({
        "sid": [1, 1, 1, 1, 2, 2, 2, 3, 3],
        "isin": ["", "INE000A01011", "INE000A01029", "INE000A01029", "INE000B01011", "INE000B01029", "INE000B01029",
                 "INE000C01011", "INE000C01011"],
        "face_value": [np.nan] * 9,
        # company 1: the split text names both values; company 2: its current ISIN is in the exchange's list
        "split_factor": [np.nan, np.nan, 0.2, np.nan, np.nan, 0.5, np.nan, np.nan, np.nan],
        "new_face_value": [np.nan, np.nan, 2.0, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan],
    })
    stats = {}
    fv = _face_values(df, cfg, stats)
    assert fv[:4].tolist() == [10.0, 10.0, 2.0, 2.0], "Rs 10 before the split, Rs 2 from the ex-date"
    assert fv[4:7].tolist() == [2.0, 1.0, 1.0], "known today, doubled going back across the split"
    assert np.isnan(fv[7:]).all(), "nothing is assumed for a company with no anchor"
    assert stats["share_of_rows_with_face_value"] == pytest.approx(7 / 9, abs=1e-4)
