"""Size information as the exchange really publishes it.

The daily market-cap file starts in January 2024. Before that, size comes from
the exchange's periodic lists and from the monthly Nifty 50 / Nifty Next 50
member files. These tests cover the file layouts met in the real archive
(written here with invented companies), the point-in-time rules for using the
lists, and the way the universe moves from one size measure to the next.
"""

import copy
import datetime as dt
import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from momo.config import DataConfig, StrategyConfig
from momo.data import parsers
from momo.data.download import Downloader
from momo.data.external import parse_amfi_list
from momo.data.ingest import date_from_name, link_index_renames
from momo.data.panel import load_panel, load_securities
from momo.data.size import MCAP_SRC_DAILY, MCAP_SRC_LIST, MCAP_SRC_NONE, build_size_lists, map_asof, symbol_spans
from momo.data.sources import MCAP_LIST_FILES, catalogue_files, monthly_file, monthly_files
from momo.signals import market_return, market_return_sources
from momo.universe import SizeRanker, build_universe, size_metric_comparison
from momo.utils import month_end_flags

BC = "SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE\n"
FIRST_VALUE_LIST = pd.Timestamp("2022-03-31")  # late fixture: the first list that carries values
FIRST_LIST = pd.Timestamp("2021-09-30")  # late fixture: ranks only
DAILY_FROM = pd.Timestamp("2023-01-02")  # late fixture: first day of the daily market-cap file
LAG = pd.Timedelta(days=30)  # DataConfig.size_list_lag_days


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return path


def _xlsx(path, rows):
    pd.DataFrame(rows).to_excel(path, index=False, header=False)
    return path


def _month_ends(md):
    return [int(t) for t in np.where(month_end_flags(md.dates))[0]]


# --------------------------------------------------------------------------- real file layouts
@pytest.mark.parametrize("bc_name, bh_name, bh_header", [
    ("Bc030111.csv", "bh03012011.csv", "SYMBOL,SR,SECURITY,HIGH/LOW,INDEX FLAG"),      # January 2011
    ("Bc230320.csv", "bh230320.csv", "SYMBOL,SERIES,SECURITY,HIGH/LOW"),                # March 2020
    ("bc30102026.csv", "bh30102026.csv", "SYMBOL,SERIES,SECURITY,HIGH/LOW,INDEX FLAG"),  # October 2026
])
def test_pr_archive_members_are_found_under_every_name_used(tmp_path, bc_name, bh_name, bh_header):
    path = _zip(tmp_path / "PR.zip", {
        bc_name: BC + "BE,ALPHA,ALPHA LIMITED,07/01/2011,,,06/01/2011,,,INT DIV-RS.5/- PER SHARE\n",
        # rows carry one field more than the 2020 header: the extra field must not shift the columns
        bh_name: bh_header + "\nALPHA,EQ,ALPHA LIMITED,H,N\nBETA,BE,BETA LIMITED,L,N\n",
        "Readme.txt": "x",
    })
    out = parsers.parse_pr_zip(path, dt.date(2011, 1, 3))
    assert "_errors" not in out
    assert out["corp_actions"]["symbol"].tolist() == ["ALPHA"]
    hits = out["band_hits"].set_index("symbol")
    assert hits.loc["ALPHA", "hit"] == "H" and hits.loc["BETA", "hit"] == "L"
    assert hits.loc["BETA", "series"] == "BE", "the series column is headed SR in the oldest archives"


def test_market_cap_list_with_values_not_traded_rows_and_footnote(tmp_path):
    path = _xlsx(tmp_path / "MCAP31122022_0.xlsx", [
        ["Sr No", "Symbol", "Company Name", "Market capitalization as on December 30, 2022\n(Rs in Lakhs)"],
        [1, "ALPHA", "Alpha Limited", 172329584.148608],
        [2, "BETA", "Beta Limited", 250.5],
        [3, "GAMMA", "Gamma Limited", "* Not Traded as on December 30, 2022"],
        [None, None, None, None],
        ["Note: Kindly refer regulation 15 to the listing regulations", None, None, None],
    ])
    out = parsers.parse_mcap_list(path, dt.date(2022, 12, 31)).set_index("symbol")
    assert list(out.index) == ["ALPHA", "BETA", "GAMMA"], "the footnote is not a company"
    assert out.loc["ALPHA", "mcap"] == pytest.approx(172329584.148608 * 1e5), "lakhs -> rupees"
    assert np.isnan(out.loc["GAMMA", "mcap"]) and out.loc["GAMMA", "rank"] == 3
    assert (out["as_of"] == pd.Timestamp("2022-12-31")).all()


def test_market_cap_list_with_ranks_only_and_a_title_row(tmp_path):
    path = _xlsx(tmp_path / "Top1000_31032019.xlsx", [
        ["Top 1000 Companies based on Market Capitalisation as on March 31, 2019", None, None],
        ["Sr. No.", "Symbol", "Security Name"],
        [1, "ALPHA", "ALPHA LTD"], [2, "BETA", "BETA LTD"], [3, "GAMMA", "GAMMA LTD"],
    ])
    out = parsers.parse_mcap_list(path, dt.date(2019, 3, 31))
    assert out["rank"].tolist() == [1, 2, 3] and out["mcap"].isna().all()


def test_market_cap_list_whose_unit_is_not_stated_is_refused(tmp_path):
    path = _xlsx(tmp_path / "MCAP31032021.xlsx", [["Sr. No.", "Symbol", "Company Name", "Market capitalization"],
                                                  [1, "ALPHA", "Alpha Limited", 10.0]])
    with pytest.raises(parsers.ParseError, match="unit"):
        parsers.parse_mcap_list(path, dt.date(2021, 3, 31))


def test_monthly_index_file_in_the_old_and_the_current_layout(tmp_path):
    old_rows = ("Sr. No,Security Symbol,Security Name,Industry,Equity Capital,Free Float Market Capitalisation for "
                "December 2012,Weightage,Beta,R2,Volatility,Monthly Return,Avg. Impact Cost\n"
                ",,,,(In Rs.),(Rs. Crores),%,,,%,,%\n"
                "1,ALPHA,\"Alpha, Sons Ltd.\",BANKS,1000000,5000.5,60.0,1,0.3,1.5,0.5,0.05\n"
                "2,BETA,Beta Ltd.,CEMENT,2000000,3000,40.0,1,0.3,1.5,0.5,0.05\n"
                ",,,,,,,,,,,\n* Impact Cost for S&P CNX Nifty is the weightage average impact cost,,,,,,,,,,,\n")
    old = _zip(tmp_path / "mcwb_dec12.zip", {
        "mcwb_dec12/niftymcwb.csv": "Annexure II- S&P CNX Nifty Index : December 2012,,,,,,,,,,,\n,,,,,,,,,,,\n" + old_rows,
        "mcwb_dec12/jrniftymcwb.csv": "Annexure II-  CNX Nifty Jr. Index : December 2012,,,,,,,,,,,\n" + old_rows.replace("ALPHA", "DELTA").replace("BETA,", "OMEGA,"),
    })
    out = parsers.parse_index_weights(old, dt.date(2012, 12, 31))
    assert out.groupby("index_name")["symbol"].apply(list).to_dict() == {
        "NIFTY 50": ["ALPHA", "BETA"], "NIFTY NEXT 50": ["DELTA", "OMEGA"]}
    first = out.iloc[0]
    assert first["company"] == "Alpha, Sons Ltd." and first["ff_mcap"] == pytest.approx(5000.5 * 1e7)

    new_rows = ("Sr. No,Security Symbol,Security Name,Industry,Equity Capital (In Rs.),Free Float Market Capitalisation "
                "(Rs. Crores),Weightage (%),Beta,R2,Volatility (%),Monthly Return,Avg. Impact Cost (%)\n"
                "1,ALPHA,Alpha Ltd.,BANKS,1000000,5000,100,1,0.3,1.5,0.5,0.05\n")
    new = _zip(tmp_path / "mcwb_jan20.zip", {
        "nifty50_mcwb.csv": "Annexure II-  Nifty 50 Index : Jan 2020,,,,,,,,,,,\n,,,,,,,,,,,\n" + new_rows,
        "niftynext50_mcwb.csv": "Annexure II- Nifty Next 50 Index : Jan 2020,,,,,,,,,,,\n,,,,,,,,,,,\n" + new_rows.replace("ALPHA", "DELTA"),
    })
    out = parsers.parse_index_weights(new, dt.date(2020, 1, 31))
    assert set(out["index_name"]) == {"NIFTY 50", "NIFTY NEXT 50"} and len(out) == 2


def test_dates_in_file_names_and_archive_urls():
    assert date_from_name("mcwb", "mcwb_jan20.zip") == dt.date(2020, 1, 31)
    assert date_from_name("mcwb", "mcwb_feb24.zip") == dt.date(2024, 2, 29), "the date is the month-end"
    assert date_from_name("mcap_lists", "MCAP31122022_0.xlsx") == dt.date(2022, 12, 31)
    assert date_from_name("mcap_lists", "Top1000_31032019.xlsx") == dt.date(2019, 3, 31)
    assert date_from_name("mcap_lists", "MCAP_31032020.xlsx") == dt.date(2020, 3, 31)
    assert monthly_file("mcwb", 2020, 1).url_path == "/content/indices/mcwb_jan20.zip"
    months = monthly_files("mcwb", dt.date(2020, 3, 15), dt.date(2020, 6, 10))
    assert [m.date for m in months] == [dt.date(2020, 2, 29), dt.date(2020, 3, 31), dt.date(2020, 4, 30),
                                        dt.date(2020, 5, 31)], "from the month before the start, complete months only"
    cat = catalogue_files("mcap_lists")
    assert len(cat) == len(MCAP_LIST_FILES) and all(c.url_path.endswith(".xlsx") for c in cat)
    for c in cat:
        assert date_from_name("mcap_lists", c.local_rel.split("/")[-1]) == c.date, "as-on date must be in the name"


# --------------------------------------------------------------------------- index renames
def _index_days(name, closes, start):
    days = pd.bdate_range(start, periods=len(closes))
    prev = [np.nan] + closes[:-1]
    return pd.DataFrame({"date": days, "index_name": name, "close": closes,
                         "change": [c - p for c, p in zip(closes, prev)]})


def test_index_rename_is_linked_only_on_an_exact_and_unique_match():
    old = _index_days("CNX 500", [100.0, 101.0, 102.0], "2015-11-04")
    new = _index_days("NIFTY 500", [103.0, 104.0], "2015-11-09").assign(change=[1.0, 1.0])  # 103 - 1 = 102: continues
    twin_a = _index_days("CNX A", [50.0, 50.0, 50.0], "2015-11-04")
    twin_b = _index_days("CNX B", [50.0, 50.0, 50.0], "2015-11-04")
    orphan = _index_days("NIFTY NEW", [51.0, 52.0], "2015-11-09").assign(change=[1.0, 1.0])  # 50: two candidates
    unrelated = _index_days("NIFTY OTHER", [500.0, 501.0], "2015-11-09").assign(change=[3.0, 1.0])  # 497: no candidate
    out, links = link_index_renames(pd.concat([old, new, twin_a, twin_b, orphan, unrelated], ignore_index=True))
    assert links[["old_name", "new_name"]].values.tolist() == [["CNX 500", "NIFTY 500"]]
    joined = out[out["index_name"] == "NIFTY 500"].sort_values("date")
    assert joined["close"].tolist() == [100.0, 101.0, 102.0, 103.0, 104.0]
    assert joined["raw_name"].tolist() == ["CNX 500"] * 3 + ["NIFTY 500"] * 2
    assert {"CNX A", "CNX B", "NIFTY NEW", "NIFTY OTHER"} <= set(out["index_name"]), "ambiguous or unmatched: left alone"


def test_fixture_index_is_one_series_across_its_rename(ws):
    md = ws.md
    assert "CNX 500" not in md.indices.columns
    level = pd.Series(md.index_level("NIFTY 500"), index=md.dates)
    around = level.loc["2021-08-20":"2021-09-10"]
    assert around.notna().all() and len(around) > 10
    assert around.pct_change().abs().max() < 0.08, "no break in the level on the day the name changed"


# --------------------------------------------------------------------------- lists -> company ids
def test_a_reused_symbol_maps_to_the_company_that_carried_it_on_the_list_date(fixture_root):
    cfg = DataConfig(root=str(fixture_root), data_kind="synthetic")
    spans = symbol_spans(load_panel(cfg, columns=["date", "sid", "symbol"]))
    owners = spans.groupby("symbol")["sid"].nunique()
    symbol = owners[owners > 1].index[0]
    first, second = spans[spans["symbol"] == symbol].sort_values("first").itertuples()
    keys = pd.Series([symbol, "NO SUCH SYMBOL"])
    assert map_asof(spans, keys, first.first + pd.Timedelta(days=30)).tolist() == [first.sid, -1]
    assert map_asof(spans, keys, second.last).tolist() == [second.sid, -1]
    long_after_delisting = first.last + pd.Timedelta(days=120)
    if long_after_delisting < second.first - pd.Timedelta(days=60):
        assert map_asof(spans, keys, long_after_delisting).tolist() == [-1, -1], "nobody carried it then"


def test_company_ids_survive_price_files_without_an_isin_column(late_root, late_truth_root):
    sec = load_securities(DataConfig(root=str(late_root), data_kind="synthetic"))
    answer = pd.read_parquet(late_truth_root / "truth_companies.parquet")
    assert len(sec) == len(answer), "one id per true company although the oldest files carry no ISIN"
    panel = load_panel(DataConfig(root=str(late_root), data_kind="synthetic"), columns=["date", "isin"])
    assert (panel.loc[panel["date"] < "2021-06-22", "isin"] == "").all()
    assert (panel.loc[panel["date"] >= "2021-06-22", "isin"] != "").all()


def test_amfi_list_is_read_mapped_by_isin_and_dated(tmp_path, fixture_root):
    cfg = DataConfig(root=str(fixture_root), data_kind="synthetic")
    panel = load_panel(cfg, columns=["date", "sid", "symbol", "isin"])
    on_day = panel[panel["date"] == panel.loc[panel["date"] <= "2022-12-30", "date"].max()].head(5)
    rows = [["Average Market Capitalization of listed companies during the six months ended 31 Dec 2022"] + [None] * 10,
            ["Sr. No.", "Company name", "ISIN", "BSE Symbol", "BSE 6 month Avg Total Market Cap in (Rs. Crs.)",
             "NSE Symbol", "NSE 6 month Avg Total Market Cap (Rs. Crs.)", "MSEI Symbol",
             "MSEI 6 month Avg Total Market Cap in (Rs Crs.)", "Average of All Exchanges (Rs. Cr.)",
             "Categorization as per SEBI Circular dated Oct 6, 2017"]]
    for k, r in enumerate(on_day.itertuples(), start=1):
        rows.append([k, f"Company {k}", r.isin, 500000 + k, 1000.0 / k, r.symbol, 1010.0 / k, None, None, 1005.0 / k,
                     "Large Cap"])
    rows.append([6, "Only on another exchange", "INE999Z01011", 599999, 1.0, None, None, None, None, 1.0, "Small Cap"])
    path = _xlsx(tmp_path / "AverageMarketCapitalization31Dec2022.xlsx", rows)

    amfi = parse_amfi_list(path)
    assert (amfi["as_of"] == pd.Timestamp("2022-12-31")).all() and len(amfi) == 6
    assert amfi["mcap"].iloc[0] == pytest.approx(1005.0 * 1e7), "average of all exchanges, crore -> rupees"

    lists = build_size_lists(cfg, panel, None, amfi)
    assert set(lists["source"]) == {"amfi"} and len(lists) == 5, "the company not on this exchange drops out"
    assert lists.sort_values("rank")["sid"].tolist() == on_day["sid"].tolist()
    assert (lists["effective"] == pd.Timestamp("2022-12-31") + LAG).all()


# --------------------------------------------------------------------------- market cap before the daily file
def test_market_cap_before_the_daily_file_comes_from_the_latest_list(late_root, late_truth_root):
    cols = ["date", "sid", "symbol", "mcap", "mcap_src"]
    got = load_panel(DataConfig(root=str(late_root), data_kind="synthetic"), columns=cols)
    want = load_panel(DataConfig(root=str(late_truth_root), data_kind="synthetic"), columns=cols)
    both = got.merge(want, on=["date", "symbol"], suffixes=("", "_true"))
    assert len(both) == len(got) == len(want), "same companies and days in both fixtures"

    first_use = FIRST_VALUE_LIST + LAG
    early = both[both["date"] < first_use]
    assert early["mcap"].isna().all() and (early["mcap_src"] == MCAP_SRC_NONE).all(), \
        "no figure before the first list with values is in force (the first list has ranks only)"

    listed = both[(both["date"] >= first_use) & (both["date"] < DAILY_FROM)]
    have = listed[listed["mcap_src"] == MCAP_SRC_LIST]
    assert len(have) > 0.85 * len(listed), "most stocks are covered; recent listings wait for the next list"
    ratio = have["mcap"] / have["mcap_true"]
    assert abs(ratio.median() - 1.0) < 0.005
    assert (abs(ratio - 1.0) <= 0.05).mean() > 0.93, "off only where shares changed after the list date (rights issues)"

    daily = both[both["date"] >= DAILY_FROM]
    assert (daily["mcap_src"] == MCAP_SRC_DAILY).mean() > 0.99
    exact = daily[daily["mcap_src"] == MCAP_SRC_DAILY]
    assert np.allclose(exact["mcap"], exact["mcap_true"], rtol=1e-9)


def test_a_list_is_used_only_between_its_effective_date_and_its_maximum_age(late_ws):
    md = late_ws.md
    assert md.size_list("nse_list", FIRST_LIST + LAG - pd.Timedelta(days=1)) == (None, None)
    rank, as_of = md.size_list("nse_list", FIRST_LIST + LAG)
    assert as_of == FIRST_LIST and np.isfinite(rank).sum() > 50
    assert md.size_list("nse_list", pd.Timestamp("2022-06-15"))[1] == FIRST_VALUE_LIST, "the latest list in force"
    last_list = md.size_lists["as_of"].max()
    stale = last_list + pd.Timedelta(days=md.list_ages["size"] + 1)
    assert md.size_list("nse_list", stale) == (None, None), "a stale list is not carried on for ever"
    assert md.size_list("amfi", pd.Timestamp("2022-06-15")) == (None, None), "no such lists were supplied"


# --------------------------------------------------------------------------- the universe's choice of measure
def _universe_cfg(cfg: StrategyConfig, **changes):
    return cfg.with_overrides({f"universe.{k}": v for k, v in changes.items()}).universe


def test_size_measure_follows_the_stated_order_as_data_become_available(late_ws, cfg):
    md = late_ws.md
    ucfg = _universe_cfg(cfg, size_metrics=["exchange_mcap", "exchange_list", "traded_value"])
    uni = build_universe(md, ucfg, _month_ends(md))
    periods = uni.size_metric_periods()
    order = [m for m in periods["size_metric"] if m != "none"]
    assert order == ["traded_value", "exchange_list", "exchange_mcap"], periods.to_string()

    by = periods.set_index("size_metric")
    assert by.loc["exchange_list", "first_decision"] >= FIRST_LIST + LAG, "not before the list is in force"
    assert by.loc["exchange_list", "first_decision"] <= FIRST_LIST + LAG + pd.Timedelta(days=31)
    assert by.loc["exchange_mcap", "first_decision"] >= FIRST_VALUE_LIST + LAG
    table = uni.table()
    used = table[table["size_metric"] == "exchange_list"]
    assert set(used["size_list_date"]) <= {FIRST_LIST.date(), FIRST_VALUE_LIST.date()}
    seasoned = table[table.index >= "2022-02-01"]  # before that no stock meets the fixture's one-year listing rule
    assert (seasoned["eligible"] > 0).all() and (seasoned["ranked"] > 60).all()


def test_only_the_listed_measures_are_used(late_ws, cfg):
    md = late_ws.md
    rows = _month_ends(md)
    only_mcap = build_universe(md, _universe_cfg(cfg, size_metrics=["exchange_mcap"]), rows).table()
    early = only_mcap[only_mcap.index < FIRST_VALUE_LIST + LAG]
    assert (early["size_metric"] == "none").all() and (early["eligible"] == 0).all(), \
        "no fallback the group did not ask for: without a measure there is no universe, and the table says so"
    assert (only_mcap.loc[only_mcap.index > "2022-08-01", "size_metric"] == "exchange_mcap").all()


def test_a_measure_that_cannot_rank_deep_enough_is_skipped(late_ws):
    md = copy.copy(late_ws.md)
    md.__dict__.pop("_derived", None)
    md.size_lists = md.size_lists[md.size_lists["rank"] <= 20]  # as if the exchange list were a "top 20"
    t = md.loc("2021-12-31")
    shallow = SizeRanker(md, ["exchange_list", "traded_value"], 126, deepest_rank=20).rank(t)
    deep = SizeRanker(md, ["exchange_list", "traded_value"], 126, deepest_rank=90).rank(t)
    assert shallow[1] == "exchange_list" and shallow[4] <= 20
    assert deep[1] == "traded_value" and deep[4] > 60, "a top-20 list cannot define ranks 21-90"


def test_index_members_take_the_first_ranks_whatever_the_measure(late_ws):
    md = late_ws.md
    t = md.loc("2021-06-30")
    members, as_of = md.index_member_flags(["NIFTY 50", "NIFTY NEXT 50"], md.dates[t])
    assert as_of <= md.dates[t] and (md.dates[t] - as_of).days <= 31 and members.sum() == 30
    plain = SizeRanker(md, ["traded_value"], 126).rank(t)[0]
    fixed = SizeRanker(md, ["traded_value"], 126, ["NIFTY 50", "NIFTY NEXT 50"]).rank(t)
    k = int(members.sum())
    assert fixed[3] == as_of and np.nanmax(fixed[0][members]) == k, "the index members hold ranks 1..k"
    assert np.nanmax(plain[members]) > k, "traded value alone would have put some of them lower"
    others = ~members & np.isfinite(plain)
    assert (np.argsort(np.argsort(plain[others])) == np.argsort(np.argsort(fixed[0][others]))).all(), \
        "the order of the other stocks is unchanged"
    with_gap = md.index_member_flags(["NIFTY 50"], md.dates[0] - pd.Timedelta(days=200))
    assert with_gap == (None, None)


@pytest.mark.parametrize("day", ["2021-08-31", "2021-12-31", "2022-09-30", "2023-03-31"])
def test_universe_built_from_lists_is_point_in_time(late_ws, cfg, day):
    md = late_ws.md
    ucfg = _universe_cfg(cfg, size_metrics=["exchange_mcap", "exchange_list", "traded_value"],
                         large_cap_indices=["NIFTY 50", "NIFTY NEXT 50"])
    t = md.loc(day)
    full = build_universe(md, ucfg, [t])[t]
    cut = build_universe(md.truncate(md.dates[t]), ucfg, [t])[t]
    assert full.size_metric == cut.size_metric
    assert (full.eligible == cut.eligible).all() and (full.segment == cut.segment).all()
    assert np.array_equal(full.rank, cut.rank, equal_nan=True)


def test_size_measures_can_be_compared_where_two_exist(late_ws, cfg):
    comp = size_metric_comparison(late_ws.md, cfg.universe.rank_bounds)
    assert len(comp) > 0 and {"measure_a", "measure_b", "rank_correlation"} <= set(comp.columns)
    pairs = set(zip(comp["measure_a"], comp["measure_b"]))
    assert ("exchange_mcap", "traded_value") in pairs and ("exchange_list", "traded_value") in pairs
    assert comp["rank_correlation"].between(-1, 1).all()
    same = comp[[c for c in comp.columns if c.startswith("same_segment_")]]
    assert ((same.isna()) | ((same >= 0) & (same <= 1))).all().all()
    mcap_vs_list = comp[(comp["measure_a"] == "exchange_mcap") & (comp["measure_b"] == "exchange_list")]
    assert (mcap_vs_list["rank_correlation"] > 0.9).all(), "two views of the same market caps must nearly agree"


# --------------------------------------------------------------------------- market return
def test_market_return_uses_an_index_until_market_caps_exist(late_ws):
    md = late_ws.md
    src = market_return_sources(md)
    src = src[src["source"] != "none"]  # the very first day has no return at all
    assert src["source"].tolist() == ["exchange_index", "cap_weighted"], src.to_string()
    switch = src.loc[src["source"] == "cap_weighted", "first_day"].iloc[0]
    assert FIRST_VALUE_LIST + LAG <= switch <= FIRST_VALUE_LIST + LAG + pd.Timedelta(days=10)
    r = pd.Series(market_return(md), index=md.dates)
    index = pd.Series(md.index_return("NIFTY 500"), index=md.dates)
    before = r.index < switch
    assert np.allclose(r[before].iloc[1:], index[before].iloc[1:], equal_nan=True)
    assert r[~before].notna().all() and not np.allclose(r[~before], index[~before])


# --------------------------------------------------------------------------- download and config
def test_missing_file_is_final_only_once_it_had_time_to_be_published():
    manifest = pd.DataFrame({
        "dataset": ["cm_bhav", "cm_bhav", "mcwb", "mcwb", "cm_bhav"],
        "date": ["2024-07-06", "2024-07-08", "2024-06-30", "2024-03-31", "2024-07-09"],
        "status": ["missing", "missing", "missing", "missing", "ok"],
        "checked_at": ["2024-07-20T10:00:00", "2024-07-09T10:00:00", "2024-07-20T10:00:00", "2024-07-20T10:00:00",
                       "2024-07-20T10:00:00"],
    })
    settled = Downloader._settled_missing(manifest)
    assert settled == {("cm_bhav", "2024-07-06"), ("mcwb", "2024-03-31")}, \
        "a file asked for the day after its date, or a monthly file asked for three weeks later, is tried again"


def test_size_measures_are_a_required_and_checked_design_choice(cfg):
    blank = cfg.with_overrides({"universe.size_metrics": None})
    assert "universe.size_metrics (at least one)" in blank.missing()
    for bad, message in ((["market_cap"], "not one of"), (["traded_value", "traded_value"], "twice")):
        problems = cfg.with_overrides({"universe.size_metrics": bad})._range_checks()
        assert any(message in p for p in problems), problems
    problems = cfg.with_overrides({"universe.large_cap_indices": ["NIFTY MIDCAP 150"]})._range_checks()
    assert any("monthly member lists exist only for" in p for p in problems)
    by_index = cfg.with_overrides({"universe.source": "index_constituents", "universe.index_names": ["X"],
                                   "universe.size_metrics": None})
    assert not any("size_metrics" in m for m in by_index.missing()), "not needed when membership files define the universe"
