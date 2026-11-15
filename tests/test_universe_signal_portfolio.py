"""Universe, signal and portfolio construction."""

import numpy as np
import pandas as pd
import pytest

from momo.config import PortfolioCfg
from momo.market import SERIES_CODE
from momo.portfolio import TrancheBook, apply_caps, select_names, tranche_count
from momo.signals import compute_signal
from momo.strategy import MomentumStrategy, decision_rows
from momo.universe import build_universe


@pytest.fixture(scope="module")
def rows(ws, cfg):
    return decision_rows(ws.md, cfg, cfg.sample.train_start, cfg.sample.test_end)


@pytest.fixture(scope="module")
def universe(ws, cfg, rows):
    return ws.universe(cfg, rows)


# --------------------------------------------------------------------------- universe
def test_eligible_stocks_pass_every_filter(ws, cfg, rows, universe):
    md, u = ws.md, cfg.universe
    for t in rows[::4]:
        snap = universe[t]
        e = np.where(snap.eligible)[0]
        assert len(e) > 50
        assert set(snap.segment[e]) <= set(u.segments)
        assert (md.age[t, e] >= u.min_listing_days).all()
        assert (md.close_ff[t, e] >= u.min_price_inr).all()
        assert (md.median_value(u.liquidity_lookback_days)[t, e] >= u.min_median_traded_value_inr).all()
        assert (md.traded_fraction(u.liquidity_lookback_days)[t, e] >= u.min_traded_days_fraction).all()
        assert (md.days_since_trade[t, e] <= 5).all()


def test_segments_follow_market_cap_rank(ws, cfg, rows, universe):
    snap = universe[rows[10]]
    for name, (lo, hi) in cfg.universe.rank_bounds.items():
        members = snap.segment == name
        assert members.sum() > 0
        assert snap.rank[members].min() >= lo and snap.rank[members].max() <= hi
    ranked = np.isfinite(snap.rank)
    assert sorted(snap.rank[ranked]) == list(range(1, ranked.sum() + 1)), "ranks must be 1..n without gaps"


def test_trade_for_trade_and_new_listings_are_screened_out_with_a_reason(ws, cfg, rows, universe):
    reasons = pd.Series(np.concatenate([universe[t].reason[universe[t].in_segment] for t in rows]))
    counts = reasons.value_counts()
    assert counts.get("series", 0) > 0, "the fixture has BE-series spells inside the size segments"
    assert counts.get("listing_age", 0) > 0, "and recent listings"
    md = ws.md
    for t in rows[::6]:
        snap = universe[t]
        be = snap.in_segment & (md.series[t] == SERIES_CODE["BE"])
        assert not snap.eligible[be].any()


def test_universe_is_point_in_time(ws, cfg, rows):
    """A snapshot built from data truncated at the decision date equals the full-data snapshot."""
    md = ws.md
    t = rows[12]
    full = build_universe(md, cfg.universe, [t])[t]
    cut = build_universe(md.truncate(md.dates[t]), cfg.universe, [t])[t]
    assert (full.eligible == cut.eligible).all() and (full.segment == cut.segment).all()


def test_universe_includes_companies_that_later_delisted(ws, rows, universe):
    md = ws.md
    early = universe[rows[2]]
    later_gone = early.eligible & (md.last_row < md.T - 60)
    assert later_gone.sum() >= 1, "stocks that later left the exchange must be investable while they traded"


# --------------------------------------------------------------------------- signal
def test_twelve_minus_one_return_matches_a_hand_calculation(ws, cfg, rows, universe):
    md = ws.md
    t = rows[8]
    snap = universe[t]
    sig = compute_signal(md, t, cfg.signal, snap.eligible)
    raw = next(iter(sig.raw.values()))
    stock = np.where(snap.eligible & np.isfinite(raw))[0][3]
    by_hand = md.tri_ff[t - 21, stock] / md.tri_ff[t - 252, stock] - 1.0
    assert raw[stock] == pytest.approx(by_hand)
    # compounding the daily returns over the same window gives the same number
    window = md.ret[t - 252 + 1:t - 21 + 1, stock]
    assert np.prod(1 + np.nan_to_num(window)) - 1 == pytest.approx(by_hand, rel=1e-9)


def test_signal_ignores_the_skipped_month_and_the_future(ws, cfg, rows, universe):
    md = ws.md
    t = rows[8]
    snap = universe[t]
    base = compute_signal(md, t, cfg.signal, snap.eligible).score
    cut = compute_signal(md.truncate(md.dates[t]), t, cfg.signal, snap.eligible).score
    assert np.array_equal(base, cut, equal_nan=True), "nothing after the decision date may affect the score"


def test_scores_are_standardised_over_the_eligible_set(ws, cfg, rows, universe):
    t = rows[8]
    snap = universe[t]
    score = compute_signal(ws.md, t, cfg.signal, snap.eligible).score
    s = score[snap.eligible & np.isfinite(score)]
    assert abs(s.mean()) < 0.05 and 0.9 < s.std() < 1.05


@pytest.mark.parametrize("kind", ["return", "risk_adjusted_return", "residual", "high_52w"])
def test_every_signal_component_produces_scores(ws, cfg, rows, universe, kind):
    trial = cfg.with_overrides({"signal.components.0.kind": kind, "signal.components.0.residual_estimation_months": 18})
    t = rows[-3]
    snap = universe[t]
    score = compute_signal(ws.md, t, trial.signal, snap.eligible).score
    assert np.isfinite(score[snap.eligible]).mean() > 0.6


# --------------------------------------------------------------------------- portfolio
def test_caps_conserve_weight_and_bind():
    w, unplaced = apply_caps(np.full(10, 0.1), np.array(list("AAAAAABBCC"), dtype=object), 0.15, 0.40)
    assert w.sum() + unplaced == pytest.approx(1.0) and unplaced == pytest.approx(0.0)
    assert w.max() <= 0.15 + 1e-12 and w[:6].sum() == pytest.approx(0.40)
    w, unplaced = apply_caps(np.full(4, 0.25), np.array(list("AABB"), dtype=object), 0.10, 0.50)
    assert w.tolist() == pytest.approx([0.1] * 4) and unplaced == pytest.approx(0.6), "what cannot be placed stays in cash"


def test_exit_buffer_keeps_holdings_until_they_fall_far_enough():
    score = np.arange(40, 0, -1, dtype=float)  # stock 0 is best
    eligible = np.ones(40, bool)
    sector = np.array([f"S{i % 8}" for i in range(40)], dtype=object)
    cfg = PortfolioCfg(selection="top_n", n_holdings=10, exit_rank_multiple=2.0, weighting="equal",
                       max_weight_per_stock=0.2, max_weight_per_sector=1.0, holding_months=1, rebalance="monthly",
                       cash_buffer=0.0)
    names, n_target, _, kept, _ = select_names(score, eligible, np.array([15, 25]), sector, cfg)
    assert n_target == 10 and kept == 1
    assert 15 in names, "rank 16 is inside the exit threshold of 20 - keep it"
    assert 25 not in names, "rank 26 is outside it - sell"
    assert set(names) == set(range(9)) | {15}


def test_sector_limit_blocks_extra_names():
    score = np.arange(30, 0, -1, dtype=float)
    sector = np.array(["A"] * 10 + ["B"] * 10 + ["C"] * 10, dtype=object)
    cfg = PortfolioCfg(selection="top_n", n_holdings=9, exit_rank_multiple=1.0, weighting="equal",
                       max_weight_per_stock=0.2, max_weight_per_sector=0.34, holding_months=1, rebalance="monthly",
                       cash_buffer=0.0)
    names, *_, blocked = select_names(score, np.ones(30, bool), np.array([], int), sector, cfg)
    assert pd.Series(sector[names]).value_counts().max() == 3, "9 names x 34% allows three per sector"
    assert len(blocked) == 14, "the best-ranked names of full sectors are skipped and recorded"


def test_tranches_average_the_last_k_portfolios():
    sector = np.array(["A", "B", "C", "D"], dtype=object)
    book = TrancheBook(3, 1.0, 1.0, sector)
    book.add(np.array([1.0, 0, 0, 0]))
    book.add(np.array([0, 1.0, 0, 0]))
    book.add(np.array([0, 0, 1.0, 0]))
    assert book.blended().tolist() == pytest.approx([1 / 3, 1 / 3, 1 / 3, 0])
    book.add(np.array([0, 0, 0, 1.0]))  # the oldest tranche rolls off
    assert book.blended().tolist() == pytest.approx([0, 1 / 3, 1 / 3, 1 / 3])
    assert tranche_count(PortfolioCfg(holding_months=3, rebalance="monthly")) == 3
    assert tranche_count(PortfolioCfg(holding_months=6, rebalance="quarterly")) == 2


def test_target_weights_respect_the_limits(ws, cfg, rows, universe):
    strategy = MomentumStrategy(ws.md, cfg, universe)
    p = cfg.portfolio
    for t in rows[:12]:
        d = strategy.decide(t)
        w = d.weights
        assert w.min() >= 0 and w.sum() <= p.gross_exposure * (1 - p.cash_buffer) + 1e-9
        assert w.max() <= p.max_weight_per_stock + 1e-9
        by_sector = pd.Series(w).groupby(ws.md.sector).sum()
        assert by_sector.max() <= p.max_weight_per_sector + 1e-9
        assert (w > 0).sum() == d.info["n_fresh"] == p.n_holdings


@pytest.mark.parametrize("weighting", ["equal", "inverse_vol", "score", "min_variance"])
def test_every_weighting_scheme_runs(ws, cfg, rows, universe, weighting):
    trial = cfg.with_overrides({"portfolio.weighting": weighting})
    d = MomentumStrategy(ws.md, trial, universe).decide(rows[10])
    assert d.weights.sum() == pytest.approx(0.98, abs=0.02) and d.weights.max() <= 0.08 + 1e-9
