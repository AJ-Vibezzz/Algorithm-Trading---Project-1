"""The back-test engine: accounting, realism rules and the absence of look-ahead."""

import numpy as np
import pandas as pd
import pytest

from momo.backtest import RunOptions
from momo.pipeline import Workspace, run_backtest, run_variants


def _locate(md, frame):
    rows = np.searchsorted(md.dates.values, frame["date"].to_numpy().astype("datetime64[ns]"))
    cols = np.searchsorted(md.sids, frame["sid"].to_numpy())
    return rows, cols


def test_accounting_identity_holds_every_day(net):
    d = net.daily
    pnl = net.pnl.groupby("date")[["pnl_gross", "cost"]].sum().reindex(d.index).fillna(0.0)
    explained = pnl["pnl_gross"] - pnl["cost"] + d["interest"] + d["hedge_pnl"]
    change = d["nav"].diff()
    change.iloc[0] = d["nav"].iloc[0] - net.meta["capital"]
    assert (explained - change).abs().max() < 1e-3, "stock P&L - costs + interest must equal the change in NAV"
    assert (d["nav"] - d["cash"] - d["equity_value"]).abs().max() < 1e-3


def test_costs_reconcile_three_ways(net):
    assert net.trades["cost"].sum() == pytest.approx(net.daily["cost_total"].sum(), rel=1e-9)
    assert net.pnl["cost"].sum() == pytest.approx(net.daily["cost_total"].sum(), rel=1e-9)
    parts = net.daily[[c for c in net.daily.columns if c.startswith("cost_") and c != "cost_total"]].sum().sum()
    assert parts == pytest.approx(net.daily["cost_total"].sum(), rel=1e-9)


def test_no_leverage(net):
    assert net.daily["cash"].min() >= -1e-6
    assert net.daily["exposure"].max() <= 1.0 + 1e-9


def test_decide_on_t_trade_on_t_plus_one(net):
    t = net.trades
    assert len(t) > 500
    assert (t["date"] > t["decision_date"]).all(), "no fill may carry the date of the decision that caused it"
    first_decision = net.decisions["date"].min()
    assert net.trades["date"].min() > first_decision


def test_liquidity_cap_is_respected(ws, cfg, net):
    t = net.trades
    assert t["participation"].max() <= cfg.execution.participation_cap + 1e-9
    md = ws.md
    rows, cols = _locate(md, t)
    realised = t["value"].to_numpy() / md.value[rows, cols]
    assert np.nanmax(realised) <= cfg.execution.participation_cap + 1e-9, "also capped against the day's own volume"
    assert (net.blocks["reason"] == "liquidity_cap").sum() > 0, "the fixture must actually exercise the cap"


def test_circuit_locks_and_non_trading_days_block_fills(ws, net, broad):
    md = ws.md
    for res in (net, broad):
        rows, cols = _locate(md, res.trades)
        lock = md.lock[rows, cols]
        side = res.trades["side"].to_numpy()
        assert not ((side == "BUY") & (lock == 1)).any(), "no buying a stock pinned at its upper band"
        assert not ((side == "SELL") & (lock == -1)).any(), "no selling a stock pinned at its lower band"
        assert md.traded[rows, cols].all(), "no fill on a day the stock did not trade"
    reasons = set(broad.blocks["reason"])
    assert {"upper_circuit", "lower_circuit", "no_trading"} <= reasons, "a book holding everything meets all three"


def test_trade_prices_lie_inside_the_days_range(ws, net):
    panel_cols = ["date", "sid", "low", "high"]
    from momo.data.panel import load_panel
    px = load_panel(ws.data_cfg, columns=panel_cols)
    m = net.trades.merge(px, on=["date", "sid"], how="left")
    assert (m["price"] >= m["low"] * 0.999).all() and (m["price"] <= m["high"] * 1.001).all()


def test_no_lookahead_truncating_the_data_does_not_change_history(ws, cfg, net):
    """Run the same back-test on data that ends early: the NAV path up to that day must be identical."""
    cut = pd.Timestamp("2024-03-28")
    md_cut = ws.md.truncate(cut)
    ws_cut = Workspace(ws.data_cfg, md_cut, ws.schedule, ws.surveillance, ws.constituents,
                       ws.delisting_returns, ws.factors)
    early = run_backtest(ws_cut, cfg, cfg.sample.train_start, cut)
    common = early.daily.index
    assert len(common) > 400
    assert (net.daily.loc[common, "nav"] - early.daily["nav"]).abs().max() < 1e-6


def test_paper_gross_net_ordering(ws, cfg):
    v = run_variants(ws, cfg, cfg.sample.train_start, cfg.sample.train_end)
    assert v["gross"].daily["cost_total"].sum() == 0.0 and v["paper"].daily["cost_total"].sum() == 0.0
    assert v["net"].nav.iloc[-1] < v["gross"].nav.iloc[-1], "costs can only reduce the final value"
    assert not {"liquidity_cap", "upper_circuit", "lower_circuit"} & set(v["paper"].blocks["reason"])


def test_bigger_fund_pays_more_impact_and_fills_less(ws, cfg):
    small = run_backtest(ws, cfg, cfg.sample.train_start, cfg.sample.train_end, RunOptions("s", capital=5e7))
    big = run_backtest(ws, cfg, cfg.sample.train_start, cfg.sample.train_end, RunOptions("b", capital=2e9))
    def impact_bps(r):
        return r.daily["cost_impact"].sum() / (r.daily["buy_value"].sum() + r.daily["sell_value"].sum())
    def fill(r):
        return r.orders["filled_value"].sum() / r.orders["wanted_value"].sum()
    assert impact_bps(big) > impact_bps(small)
    assert fill(big) < fill(small)


def test_writeoffs_follow_the_delisting_inputs(ws, cfg, broad):
    w = broad.writeoffs
    assert len(w) >= 3, "a book holding everything must be caught in some of the fixture's delistings"
    assert (w["proceeds"] <= w["carrying_value"] * 1.06).all()
    assert np.allclose(w["proceeds"], w["carrying_value"] * (1 + w["terminal_return"]))
    for row in w.itertuples():
        if row.source == "assumption":
            assert row.terminal_return == cfg.risk.delisting_return
        else:
            assert row.terminal_return == ws.delisting_returns[row.symbol]


def test_orders_end_in_a_known_state(net):
    assert set(net.orders["status"]) <= {"filled", "expired_partial", "expired_unfilled", "superseded_partial",
                                         "superseded_unfilled", "written_off", "open_at_end"}
    filled = net.orders[net.orders["status"] == "filled"]
    assert (filled["filled_value"] > 0).all()
    sells = net.orders[(net.orders["side"] == "SELL")]
    assert not sells["status"].str.startswith("expired").any(), "sell orders are worked until done - they never lapse"


def test_stop_loss_exits_and_cools_down(ws, cfg):
    """A tight trailing stop must fire on volatile small caps, sell the whole position and block re-entry."""
    trial = cfg.with_overrides({"risk.stop_loss.enabled": True, "risk.stop_loss.kind": "trailing",
                                "risk.stop_loss.threshold": 0.08, "risk.stop_loss.cooldown_days": 30})
    res = run_backtest(ws, trial, trial.sample.train_start, trial.sample.train_end)
    stops = res.risk_events[res.risk_events["event"] == "stop_loss"]
    assert len(stops) >= 5
    stop_orders = res.orders[res.orders["reason"] == "stop_loss"]
    assert len(stop_orders) == len(stops) and (stop_orders["side"] == "SELL").all()
    assert (stop_orders["kind"] == "exit").all()
    # no purchase of a stopped-out stock while it is in cool-down
    md = ws.md
    buys = res.trades[res.trades["side"] == "BUY"]
    for ev in stops.itertuples():
        t0 = md.loc(ev.date)
        again = buys[(buys["symbol"] == ev.symbol) & (buys["date"] > ev.date)
                     & (buys["date"] <= md.dates[min(t0 + 30, md.T - 1)])]
        assert again.empty, f"{ev.symbol} was bought back inside its cool-down"


def test_drawdown_tiers_cut_exposure_then_rebase(ws, cfg):
    """Set the hard limit inside the drawdown the unconstrained book is known to suffer: it must trigger."""
    small = RunOptions("small", capital=2e7)  # small enough that de-risking sells fill within days
    off = cfg.with_overrides({"risk.drawdown.soft_limit": 0.98, "risk.drawdown.hard_limit": 0.99})
    base = run_backtest(ws, off, off.sample.train_start, off.sample.test_end, small)
    assert set(base.risk_events["event"]) == set(), "with limits out of reach nothing may trigger"
    worst = abs(float((base.nav / base.nav.cummax() - 1.0).min()))
    assert worst > 0.05

    hard = 0.6 * worst
    on = cfg.with_overrides({"risk.drawdown.soft_limit": 0.3 * worst, "risk.drawdown.soft_exposure": 1.0,
                             "risk.drawdown.hard_limit": hard, "risk.drawdown.hard_exposure": 0.0,
                             "risk.drawdown.cooloff_days": 15})
    res = run_backtest(ws, on, on.sample.train_start, on.sample.test_end, small)
    events = res.risk_events
    assert {"soft_limit_breached", "hard_limit_breached", "rebase_after_cooloff"} <= set(events["event"])
    breach = events[events["event"] == "hard_limit_breached"]["date"].iloc[0]
    rebase = events[(events["event"] == "rebase_after_cooloff") & (events["date"] > breach)]["date"].iloc[0]
    # the soft tier keeps full exposure here, so the path equals the unconstrained one up to the hard breach
    before = res.daily.index[res.daily.index <= breach]
    assert (res.daily.loc[before, "nav"] - base.daily.loc[before, "nav"]).abs().max() < 1e-6
    assert res.nav.loc[breach] / res.nav.loc[:breach].max() - 1.0 <= -hard
    assert (res.orders["reason"] == "drawdown_derisk").sum() > 0
    # after the breach the book goes (almost) to cash and stays there until the cooling-off period ends
    cooling = res.daily.loc[breach:rebase].iloc[10:]
    assert len(cooling) >= 5 and cooling["exposure"].max() < 0.20
    assert res.daily.index.get_loc(rebase) - res.daily.index.get_loc(breach) == 15


def test_overlapping_tranches_and_vol_target_run(ws, cfg):
    trial = cfg.with_overrides({"portfolio.holding_months": 3, "risk.vol_target.enabled": True,
                                "risk.vol_target.annual_vol": 0.08})
    res = run_backtest(ws, trial, trial.sample.train_start, trial.sample.train_end)
    assert res.decisions["n_blended"].iloc[-1] > trial.portfolio.n_holdings, "three tranches hold more names than one"
    assert res.decisions["mult_vol_target"].min() < 1.0 and res.daily["exposure"].mean() < 0.9


def test_hedge_overlay_reduces_market_beta(ws, cfg):
    hedged = cfg.with_overrides({"risk.hedge.enabled": True, "risk.hedge.index": "NIFTY 500", "risk.hedge.ratio": 1.0})
    a = run_backtest(ws, cfg, cfg.sample.train_start, cfg.sample.train_end)
    b = run_backtest(ws, hedged, cfg.sample.train_start, cfg.sample.train_end)
    mkt = pd.Series(ws.md.index_return("NIFTY 500"), index=ws.md.dates)
    def beta(res):
        r = res.returns.iloc[5:]
        return np.cov(r, mkt.reindex(r.index))[0, 1] / mkt.reindex(r.index).var()
    assert b.daily["hedge_notional"].max() > 0
    assert abs(beta(b)) < 0.5 * abs(beta(a))
