"""Daily, event-driven back-test engine.

Timeline of one trading day ``t``
---------------------------------
1. **Trade.** Orders created at an earlier close are worked at today's
   execution price (VWAP, open or close of day ``t``). A decision taken at the
   close of ``t-1`` therefore trades no earlier than ``t`` - "decide on day t,
   trade on day t+1".
2. **Mark to market** at today's close; write off holdings that have not
   traded for too long (delisting / suspension).
3. **Risk rules** at the close: per-stock stop-loss and the portfolio drawdown
   controller. Any action they trigger becomes an order for tomorrow.
4. **Rebalance decision** (on decision days only): the strategy sets target
   weights from data up to today's close; the difference from current holdings
   becomes orders for tomorrow.

What makes a fill realistic
---------------------------
* **Liquidity cap.** Each day at most ``participation_cap`` of the stock's
  average daily traded value (known the day before) can be traded, and, if
  ``cap_on_realised_volume`` is set, at most that share of the day's actual
  volume. The rest of the order carries over to following days.
* **Circuit locks.** No buying a stock pinned at its upper price band; no
  selling one pinned at its lower band.
* **No trade, no fill.** A stock that does not trade today cannot be traded.
* **Cash.** Buys are scaled down if sales and cash do not cover them - no leverage.
* **Costs.** Every fill pays statutory charges, brokerage, half the spread and
  market impact (``costs.py``).

Buy orders lapse after ``max_execution_days``; sell orders are worked until
done, because risk reduction should not expire. Every fill that fell short is
logged with its reason, so the report can state how many signals each limit blocked.

Accounting
----------
Positions are held in units of each stock's total-return index, so splits,
bonuses and dividends need no special handling: value = units x index level.
The identity "sum of stock P&L - costs + cash interest (+ hedge) = change in
NAV" holds every day and is checked in the tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .costs import COMPONENTS, CostModel, StatutorySchedule
from .market import MarketData
from .risk import DrawdownController
from .strategy import MomentumStrategy


@dataclass
class RunOptions:
    label: str = "net"
    costs: bool = True  # False -> gross of all costs
    frictions: bool = True  # False -> no liquidity cap and no circuit blocks ("paper" portfolio)
    capital: Optional[float] = None  # override the fund size (used by the capacity sweep)


@dataclass
class BacktestResult:
    label: str
    daily: pd.DataFrame  # one row per trading day: nav, cash, exposure, costs, ...
    positions: pd.DataFrame  # date, sid, symbol, value, weight (held names only)
    trades: pd.DataFrame  # every fill
    orders: pd.DataFrame  # every order and how it ended
    blocks: pd.DataFrame  # every day an order fell short, with the reason
    pnl: pd.DataFrame  # date, sid, pnl_gross, cost  (per-stock daily P&L)
    decisions: pd.DataFrame  # one row per rebalance decision
    screened: pd.DataFrame  # top-ranked stocks removed by a filter / limit
    risk_events: pd.DataFrame  # stop-losses, drawdown breaches, ...
    writeoffs: pd.DataFrame  # holdings written off after they stopped trading
    meta: Dict[str, object] = field(default_factory=dict)

    @property
    def nav(self) -> pd.Series:
        return self.daily["nav"]

    @property
    def returns(self) -> pd.Series:
        return self.daily["nav"].pct_change().fillna(0.0)


class Backtester:
    def __init__(
        self,
        md: MarketData,
        cfg: StrategyConfig,
        strategy: MomentumStrategy,
        schedule: StatutorySchedule,
        rows: List[int],
        end_row: int,
        options: Optional[RunOptions] = None,
        delisting_returns: Optional[Dict[str, float]] = None,
    ):
        self.md, self.cfg, self.strategy = md, cfg, strategy
        self.rows = sorted(rows)
        self.decision_set = set(self.rows)
        self.start_row, self.end_row = self.rows[0], end_row
        self.opt = options or RunOptions()
        self.delisting_returns = delisting_returns or {}
        self.costs = CostModel(schedule, cfg.costs, md.dates, enabled=self.opt.costs)

    # ------------------------------------------------------------------ main loop
    def run(self) -> BacktestResult:
        md, cfg, opt = self.md, self.cfg, self.opt
        ex, rk = cfg.execution, cfg.risk
        N = md.N
        capital = float(opt.capital if opt.capital is not None else cfg.capital.initial_capital_inr)

        # --- market inputs, all "as known before the trading day" where relevant -------------
        with np.errstate(divide="ignore", invalid="ignore"):
            if ex.price == "vwap":
                ratio = md.vwap.astype(float) / md.close.astype(float)
            elif ex.price == "open":
                ratio = md.open.astype(float) / md.close.astype(float)
            else:
                ratio = np.ones_like(md.tri)
        adv = md.adv(ex.adv_window_days)
        vol = md.volatility(cfg.costs.impact_vol_window_days)
        half_spread = md.half_spread(cfg.costs.spread_model, cfg.costs.spread_window_days,
                                     cfg.costs.fixed_half_spread_bps, cfg.costs.max_half_spread_bps)
        hedge_ret = None
        if rk.hedge.enabled:
            hedge_ret = md.index_return(rk.hedge.index)
            if not rk.hedge.index.strip().upper().endswith("TRI"):
                # A price index omits dividends, which a short futures position effectively pays.
                hedge_ret = hedge_ret + 0.012 / 252.0

        # --- state ------------------------------------------------------------------------------
        shares = np.zeros(N)  # units of each stock's total-return index
        cash = capital
        ord_shares = np.zeros(N)  # signed units still to trade
        ord_age = np.zeros(N, int)
        ord_idx = np.full(N, -1)  # row in `orders` of the open order for this stock
        entry_px = np.full(N, np.nan)
        peak_px = np.full(N, np.nan)
        banned_until = np.full(N, -1)
        base_target: Optional[np.ndarray] = None
        dd = DrawdownController(rk.drawdown, capital)
        hedge_notional = 0.0

        orders: List[dict] = []
        trades: List[dict] = []
        blocks: List[dict] = []
        risk_events: List[dict] = []
        writeoffs: List[dict] = []
        decisions: List[dict] = []
        screened: List[dict] = []
        pos_rows: List[pd.DataFrame] = []
        pnl_rows: List[pd.DataFrame] = []
        days = range(self.start_row, self.end_row + 1)
        daily = {k: np.zeros(len(days)) for k in
                 ["nav", "cash", "equity_value", "n_positions", "interest", "hedge_pnl", "hedge_notional",
                  "buy_value", "sell_value", "cost_total"] + [f"cost_{c}" for c in COMPONENTS]}

        def close_order(j: int, status: str) -> None:
            if ord_idx[j] >= 0:
                rec = orders[ord_idx[j]]
                if rec["status"] == "open":
                    rec["status"] = status
            ord_shares[j], ord_age[j], ord_idx[j] = 0.0, 0, -1

        def place_orders(t: int, target: np.ndarray, nav: float, reason: str, sells_only: bool = False) -> None:
            """Turn target weights into orders for the next trading day (replacing open orders)."""
            px = md.tri_ff[t]
            cur_val = np.nan_to_num(shares * px)
            diff = target * nav - cur_val
            is_exit = (target <= 0) & (shares > 0)
            is_entry = (target > 0) & (shares <= 0)
            band = max(ex.min_trade_value_inr, cfg.portfolio.no_trade_band * nav)
            act = (np.abs(diff) > band) | is_exit | is_entry
            act &= np.isfinite(px) & (px > 0)
            if sells_only:
                act &= diff < 0
            for j in np.where((ord_shares != 0) | act)[0]:
                if ord_shares[j] != 0:
                    close_order(j, "superseded_partial" if orders[ord_idx[j]]["filled_value"] > 0 else "superseded_unfilled")
                if not act[j]:
                    continue
                units = -shares[j] if is_exit[j] else diff[j] / px[j]
                if units == 0:
                    continue
                kind = "exit" if is_exit[j] else ("entry" if is_entry[j] else "resize")
                orders.append({
                    "order_id": len(orders), "decision_date": md.dates[t], "sid": int(md.sids[j]),
                    "symbol": md.symbols[j], "side": "BUY" if units > 0 else "SELL",
                    "reason": reason, "kind": kind, "wanted_value": abs(units) * px[j],
                    "filled_value": 0.0, "fill_days": 0, "first_fill": pd.NaT, "last_fill": pd.NaT,
                    "blocked_days": 0, "status": "open",
                })
                ord_shares[j], ord_age[j], ord_idx[j] = units, 0, len(orders) - 1

        for k, t in enumerate(days):
            date = md.dates[t]
            px_prev = md.tri_ff[t - 1] if t > 0 else np.full(N, np.nan)
            value_start = np.nan_to_num(shares * px_prev)
            net_bought = np.zeros(N)  # rupees bought minus sold today, per stock
            cost_today = np.zeros(N)

            # --- overnight carry: interest on cash, P&L on the futures hedge -----------------------
            interest = cash * md.rf_daily[t] if (cfg.capital.cash_return == "rf" and cash > 0) else 0.0
            cash += interest
            hedge_pnl = 0.0
            if hedge_notional > 0 and hedge_ret is not None and np.isfinite(hedge_ret[t]):
                hedge_pnl = -hedge_notional * (hedge_ret[t] - md.rf_daily[t])
                cash += hedge_pnl
                hedge_notional *= 1.0 + hedge_ret[t]

            # --- 1. work the open orders --------------------------------------------------------------
            pend = np.where(ord_shares != 0)[0]
            if len(pend):
                px = md.tri[t, pend] * ratio[t, pend]
                want = ord_shares[pend]
                buy = want > 0
                tradable = md.traded[t, pend] & np.isfinite(px) & (px > 0)
                reason = np.full(len(pend), "", dtype=object)
                reason[~tradable] = "no_trading"
                if opt.frictions:
                    lock = md.lock[t, pend]
                    if ex.block_buy_at_upper_circuit:
                        reason[tradable & buy & (lock == 1)] = "upper_circuit"
                    if ex.block_sell_at_lower_circuit:
                        reason[tradable & ~buy & (lock == -1)] = "lower_circuit"
                can = reason == ""
                want_val = np.where(can, np.abs(want) * np.nan_to_num(px), 0.0)
                if opt.frictions:
                    cap = ex.participation_cap * np.nan_to_num(adv[t - 1, pend])
                    if ex.cap_on_realised_volume:
                        cap = np.minimum(cap, ex.participation_cap * np.nan_to_num(md.value[t, pend]))
                else:
                    cap = np.full(len(pend), np.inf)
                fill_val = np.minimum(want_val, cap)
                short_liq = can & (fill_val < want_val - 1e-6)

                a, v, hs = adv[t - 1, pend], vol[t - 1, pend], half_spread[t - 1, pend]
                buy_val, sell_val = np.where(buy, fill_val, 0.0), np.where(~buy, fill_val, 0.0)
                fc = self.costs.fills(t, buy_val, sell_val, a, v, hs)
                # No leverage: buys must be covered by cash plus today's net sale proceeds.
                need = buy_val.sum() + fc.per_stock[buy].sum()
                have = cash + sell_val.sum() - fc.per_stock[~buy].sum()
                short_cash = np.zeros(len(pend), bool)
                if need > have + 1e-9 and need > 0:
                    scale = max(0.0, have / need)
                    short_cash = buy & (fill_val > 0)
                    fill_val = np.where(buy, fill_val * scale, fill_val)
                    buy_val = np.where(buy, fill_val, 0.0)
                    fc = self.costs.fills(t, buy_val, sell_val, a, v, hs)

                with np.errstate(divide="ignore", invalid="ignore"):
                    frac = np.where(want_val > 0, fill_val / want_val, 0.0)
                units = want * frac
                # --- book the fills -------------------------------------------------------------------
                old = shares[pend].copy()
                shares[pend] = np.where(np.abs(old + units) < 1e-12, 0.0, old + units)
                cash += sell_val.sum() - buy_val.sum() - fc.per_stock.sum()
                net_bought[pend] = buy_val - sell_val
                cost_today[pend] = fc.per_stock
                for c in COMPONENTS:
                    daily[f"cost_{c}"][k] = fc.by_component[c]
                daily["cost_total"][k] = fc.per_stock.sum()
                daily["buy_value"][k], daily["sell_value"][k] = buy_val.sum(), sell_val.sum()

                # entry and peak prices for the stop-loss
                got = buy & (units > 0)
                jb = pend[got]
                with np.errstate(invalid="ignore", divide="ignore"):
                    avg = (old[got] * np.nan_to_num(entry_px[jb]) + units[got] * px[got]) / (old[got] + units[got])
                entry_px[jb] = np.where(old[got] > 0, avg, px[got])
                peak_px[jb] = np.fmax(peak_px[jb], px[got])
                flat = pend[shares[pend] == 0]
                entry_px[flat], peak_px[flat] = np.nan, np.nan

                remaining = want - units
                raw_px = md.close[t, pend].astype(float) * ratio[t, pend]
                for i, j in enumerate(pend):
                    rec = orders[ord_idx[j]]
                    if fill_val[i] > 0:
                        rec["filled_value"] += fill_val[i]
                        rec["fill_days"] += 1
                        rec["last_fill"] = date
                        if pd.isna(rec["first_fill"]):
                            rec["first_fill"] = date
                        trades.append({
                            "date": date, "order_id": rec["order_id"], "sid": rec["sid"], "symbol": rec["symbol"],
                            "side": rec["side"], "reason": rec["reason"], "kind": rec["kind"],
                            "value": fill_val[i], "price": raw_px[i],
                            "quantity": fill_val[i] / raw_px[i] if raw_px[i] > 0 else np.nan,
                            "cost": fc.per_stock[i],
                            "participation": fill_val[i] / a[i] if a[i] > 0 else np.nan,
                            "decision_date": rec["decision_date"],
                        })
                    why = reason[i] or ("liquidity_cap" if short_liq[i] else ("cash" if short_cash[i] else ""))
                    if why:
                        rec["blocked_days"] += 1
                        blocks.append({"date": date, "order_id": rec["order_id"], "sid": rec["sid"],
                                       "symbol": rec["symbol"], "side": rec["side"], "reason": why,
                                       "wanted_value": abs(want[i]) * px[i] if np.isfinite(px[i]) else np.nan,
                                       "filled_value": fill_val[i]})
                    done = abs(remaining[i]) * (px[i] if np.isfinite(px[i]) else 0.0) < 1.0 and can[i]
                    if done:
                        close_order(j, "filled")
                    else:
                        ord_shares[j] = remaining[i]
                        ord_age[j] += 1
                        if buy[i] and ord_age[j] >= ex.max_execution_days:  # stop chasing a buy
                            close_order(j, "expired_partial" if rec["filled_value"] > 0 else "expired_unfilled")

            # --- 2. mark to market; write off holdings that stopped trading ------------------------------
            px_close = md.tri_ff[t]
            pos_val = np.nan_to_num(shares * px_close)
            held = np.where(shares > 0)[0]
            stale = held[md.days_since_trade[t, held] > rk.max_stale_days]
            for j in stale:
                terminal = self.delisting_returns.get(md.symbols[j], rk.delisting_return)
                proceeds = pos_val[j] * (1.0 + terminal)
                cash += proceeds
                net_bought[j] -= proceeds
                writeoffs.append({"date": date, "sid": int(md.sids[j]), "symbol": md.symbols[j],
                                  "carrying_value": pos_val[j], "terminal_return": terminal,
                                  "proceeds": proceeds,
                                  "source": "delistings.csv" if md.symbols[j] in self.delisting_returns else "assumption"})
                shares[j], pos_val[j] = 0.0, 0.0
                entry_px[j], peak_px[j] = np.nan, np.nan
                close_order(j, "written_off")
            equity = pos_val.sum()
            nav = cash + equity

            # per-stock P&L: change in value, less net cash put in (identity: sums to the NAV change)
            active = np.where((value_start != 0) | (net_bought != 0) | (pos_val != 0))[0]
            if len(active):
                pnl_rows.append(pd.DataFrame({
                    "date": date, "sid": md.sids[active],
                    "pnl_gross": pos_val[active] - value_start[active] - net_bought[active],
                    "cost": cost_today[active],
                }))
            held = np.where(shares > 0)[0]
            if len(held):
                pos_rows.append(pd.DataFrame({"date": date, "sid": md.sids[held], "symbol": md.symbols[held],
                                              "value": pos_val[held], "weight": pos_val[held] / nav}))
            for name, val in (("nav", nav), ("cash", cash), ("equity_value", equity), ("n_positions", len(held)),
                              ("interest", interest), ("hedge_pnl", hedge_pnl), ("hedge_notional", hedge_notional)):
                daily[name][k] = val

            if t == self.end_row:
                break

            # --- 3. risk rules at the close ----------------------------------------------------------------
            if rk.stop_loss.enabled and len(held):
                live = held[md.traded[t, held]]
                now = md.tri[t, live]
                peak_px[live] = np.fmax(peak_px[live], now)
                ref = peak_px[live] if rk.stop_loss.kind == "trailing" else entry_px[live]
                with np.errstate(invalid="ignore", divide="ignore"):
                    hit = live[(now / ref - 1.0) <= -rk.stop_loss.threshold]
                for j in hit:
                    if ord_shares[j] < 0 and ord_idx[j] >= 0 and orders[ord_idx[j]]["kind"] == "exit":
                        continue  # already being sold
                    if ord_shares[j] != 0:
                        close_order(j, "superseded_unfilled")
                    orders.append({
                        "order_id": len(orders), "decision_date": date, "sid": int(md.sids[j]),
                        "symbol": md.symbols[j], "side": "SELL", "reason": "stop_loss", "kind": "exit",
                        "wanted_value": pos_val[j], "filled_value": 0.0, "fill_days": 0,
                        "first_fill": pd.NaT, "last_fill": pd.NaT, "blocked_days": 0, "status": "open",
                    })
                    ord_shares[j], ord_age[j], ord_idx[j] = -shares[j], 0, len(orders) - 1
                    banned_until[j] = t + rk.stop_loss.cooldown_days
                    if base_target is not None:
                        base_target[j] = 0.0
                    risk_events.append({"date": date, "event": "stop_loss", "symbol": md.symbols[j],
                                        "detail": f"{now[live == j][0] / ref[live == j][0] - 1.0:.1%} vs "
                                                  f"{'peak' if rk.stop_loss.kind == 'trailing' else 'entry'}"})

            event = dd.update(nav)
            if event:
                risk_events.append({"date": date, "event": event, "symbol": "",
                                    "detail": f"drawdown {nav / dd.hwm - 1.0:.1%}; exposure multiplier {dd.multiplier:.2f}"})
            is_decision = t in self.decision_set
            if event in ("soft_limit_breached", "hard_limit_breached") and base_target is not None and not is_decision:
                place_orders(t, base_target * dd.multiplier, nav, reason="drawdown_derisk", sells_only=True)

            # --- 4. rebalance decision --------------------------------------------------------------------------
            if is_decision:
                dec = self.strategy.decide(t, dd.multiplier, banned_until > t)
                base_target = dec.base_weights.copy()
                place_orders(t, dec.weights, nav, reason="rebalance")
                if rk.hedge.enabled:
                    beta = max(float(dec.info.get("beta_full", 1.0)), 0.0)
                    target_hedge = rk.hedge.ratio * beta * float(dec.weights.sum()) * nav
                    fee = abs(target_hedge - hedge_notional) * rk.hedge.cost_bps_per_side / 1e4
                    fee += target_hedge * rk.hedge.roll_cost_bps_per_month / 1e4
                    if opt.costs:
                        cash -= fee
                        daily["cost_total"][k] += fee
                    hedge_notional = target_hedge
                    dec.info["hedge_notional"] = target_hedge
                dec.info["nav"] = nav
                dec.info["drawdown"] = nav / dd.hwm - 1.0
                decisions.append(dec.info)
                screened.extend(dec.screened)

        for rec in orders:  # whatever is still open when the sample ends
            if rec["status"] == "open":
                rec["status"] = "open_at_end"

        index = md.dates[self.start_row:self.end_row + 1]
        daily_df = pd.DataFrame(daily, index=index)
        daily_df["exposure"] = daily_df["equity_value"] / daily_df["nav"]

        def frame(rows: List[dict], cols: List[str]) -> pd.DataFrame:
            return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)

        return BacktestResult(
            label=opt.label,
            daily=daily_df,
            positions=pd.concat(pos_rows, ignore_index=True) if pos_rows else pd.DataFrame(
                columns=["date", "sid", "symbol", "value", "weight"]),
            trades=frame(trades, ["date", "order_id", "sid", "symbol", "side", "reason", "kind", "value", "price",
                                  "quantity", "cost", "participation", "decision_date"]),
            orders=frame(orders, ["order_id", "decision_date", "sid", "symbol", "side", "reason", "kind",
                                  "wanted_value", "filled_value", "fill_days", "first_fill", "last_fill",
                                  "blocked_days", "status"]),
            blocks=frame(blocks, ["date", "order_id", "sid", "symbol", "side", "reason", "wanted_value", "filled_value"]),
            pnl=pd.concat(pnl_rows, ignore_index=True) if pnl_rows else pd.DataFrame(
                columns=["date", "sid", "pnl_gross", "cost"]),
            decisions=frame(decisions, ["date"]),
            screened=frame(screened, ["date", "sid", "symbol", "reason", "score_rank"]),
            risk_events=frame(risk_events, ["date", "event", "symbol", "detail"]),
            writeoffs=frame(writeoffs, ["date", "sid", "symbol", "carrying_value", "terminal_return", "proceeds", "source"]),
            meta={"capital": capital, "costs": opt.costs, "frictions": opt.frictions,
                  "start": index[0], "end": index[-1], "data_kind": md.data_kind,
                  "config": cfg.fingerprint()},
        )
