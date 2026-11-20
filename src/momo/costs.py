"""Transaction-cost model for NSE cash-market delivery trades.

Cost of one fill = statutory and exchange charges  (facts, dated schedule)
                 + brokerage and the tax on it      (assumption: your broker's rate)
                 + half the bid-ask spread          (estimated per stock per day)
                 + market impact                    (square-root law)

Statutory part (config/statutory_charges.yaml - each line has a source)
-----------------------------------------------------------------------
* Securities Transaction Tax (STT) on both sides of a delivery trade
* stamp duty (buyer only since 1 July 2020)
* NSE transaction charge, SEBI turnover fee, investor-protection-fund levy
* GST (service tax before July 2017) on brokerage and the exchange/SEBI fees -
  not on the trade value, STT or stamp duty

Market impact
-------------
The cost of demanding liquidity rises with the size of the order relative to
normal volume. The widely used square-root law (Torre 1997; Grinold and Kahn
2000; Almgren et al. 2005; Toth et al. 2011) says::

    impact (fraction of price) = k x daily volatility x sqrt(order value / ADV)

``k`` is an assumption the group must choose and defend (literature values are
roughly 0.1 to 1). Because impact grows with fund size, this term is what
makes the strategy's capacity finite.

Results are reported gross (no costs) and net (all of the above).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from .config import ConfigError, CostsCfg, load_yaml

COMPONENTS = ["brokerage", "stt", "stamp_duty", "exchange", "sebi", "ipft", "gst", "dp", "spread", "impact"]


def _as_date(x) -> dt.date:
    return x if isinstance(x, dt.date) and not isinstance(x, dt.datetime) else pd.Timestamp(x).date()


class StatutorySchedule:
    """Dated statutory rates; ``daily(dates)`` gives the rate in force on each trading day."""

    def __init__(self, raw: Dict):
        self.raw = raw
        required = ["stt_delivery", "stamp_duty", "exchange_transaction", "sebi_turnover_fee", "ipft",
                    "tax_on_services", "gst_on_sebi_fee_from"]
        missing = [k for k in required if k not in raw]
        if missing:
            raise ConfigError(f"statutory_charges.yaml lacks section(s): {missing}")

    @classmethod
    def load(cls, path: str | Path) -> "StatutorySchedule":
        return cls(load_yaml(path))

    def _series(self, section: str, field: str, dates: pd.DatetimeIndex) -> np.ndarray:
        entries = sorted(self.raw[section], key=lambda e: _as_date(e["from"]))
        starts = np.array([np.datetime64(_as_date(e["from"])) for e in entries])
        values = np.array([float(e[field]) for e in entries])
        idx = np.searchsorted(starts, dates.values.astype("datetime64[D]"), side="right") - 1
        if (idx < 0).any():
            first = dates[idx < 0][0].date()
            raise ConfigError(
                f"statutory_charges.yaml: no '{section}' rate covers {first}. "
                f"Add an entry with an earlier 'from' date (and its source)."
            )
        return values[idx]

    def daily(self, dates: pd.DatetimeIndex) -> Dict[str, np.ndarray]:
        gst_on_sebi = dates.values.astype("datetime64[D]") >= np.datetime64(
            _as_date(self.raw["gst_on_sebi_fee_from"]["date"]))
        return {
            "stt_buy": self._series("stt_delivery", "buy", dates),
            "stt_sell": self._series("stt_delivery", "sell", dates),
            "stamp_buy": self._series("stamp_duty", "buy", dates),
            "stamp_sell": self._series("stamp_duty", "sell", dates),
            "exchange": self._series("exchange_transaction", "rate", dates),
            "sebi": self._series("sebi_turnover_fee", "rate", dates),
            "ipft": self._series("ipft", "rate", dates),
            "service_tax": self._series("tax_on_services", "rate", dates),
            "gst_on_sebi": gst_on_sebi.astype(float),
        }

    def unverified(self) -> List[str]:
        """Entries not yet confirmed against a cited source - listed in the run log."""
        out = []
        for section, entries in self.raw.items():
            for e in (entries if isinstance(entries, list) else [entries]):
                # YAML reads a bare `no` as False, so accept both spellings
                if e.get("checked", False) is False or str(e.get("checked")).lower() in ("no", "false"):
                    out.append(f"{section} (from {e.get('from', e.get('date'))})")
        return out


@dataclass
class FillCosts:
    """Costs of one day's fills: per-stock total and each component summed over stocks."""

    per_stock: np.ndarray
    by_component: Dict[str, float]


class CostModel:
    """Computes the cost of a set of fills on one day."""

    def __init__(self, schedule: StatutorySchedule, cfg: CostsCfg, dates: pd.DatetimeIndex, enabled: bool = True):
        self.cfg = cfg
        self.enabled = enabled
        self.rates = schedule.daily(dates)
        self.brokerage = float(cfg.brokerage_bps_per_side) / 1e4
        self.k = float(cfg.impact_coefficient)

    def per_side_statutory_bps(self, t: int) -> Dict[str, float]:
        """Statutory + brokerage cost of a Rs 1 buy and a Rs 1 sell on day ``t``, in basis points."""
        r = self.rates
        fees = self.brokerage + r["exchange"][t] + r["ipft"][t]
        gst = r["service_tax"][t] * (fees + r["sebi"][t] * r["gst_on_sebi"][t])
        common = fees + r["sebi"][t] + gst
        return {"buy_bps": 1e4 * (common + r["stt_buy"][t] + r["stamp_buy"][t]),
                "sell_bps": 1e4 * (common + r["stt_sell"][t] + r["stamp_sell"][t])}

    def fills(
        self,
        t: int,
        buy_value: np.ndarray,
        sell_value: np.ndarray,
        adv: np.ndarray,
        daily_vol: np.ndarray,
        half_spread: np.ndarray,
    ) -> FillCosts:
        """Cost in rupees of today's fills.

        All arrays are per traded stock: rupees bought, rupees sold, average
        daily traded value, daily return volatility and half-spread (fractions).
        """
        n = len(buy_value)
        if not self.enabled or n == 0:
            return FillCosts(np.zeros(n), {c: 0.0 for c in COMPONENTS})
        r = self.rates
        value = buy_value + sell_value
        brokerage = self.brokerage * value
        stt = r["stt_buy"][t] * buy_value + r["stt_sell"][t] * sell_value
        stamp = r["stamp_buy"][t] * buy_value + r["stamp_sell"][t] * sell_value
        exchange = r["exchange"][t] * value
        sebi = r["sebi"][t] * value
        ipft = r["ipft"][t] * value
        dp_base = self.cfg.dp_charge_inr_per_sell * (sell_value > 0)
        gst = r["service_tax"][t] * (brokerage + exchange + ipft + sebi * r["gst_on_sebi"][t] + dp_base)
        spread = half_spread * value
        with np.errstate(divide="ignore", invalid="ignore"):
            participation = np.where(adv > 0, value / adv, 1.0)  # no volume history: assume a full day's volume
        vol = np.where(np.isfinite(daily_vol), daily_vol, np.nanmedian(daily_vol) if np.isfinite(daily_vol).any() else 0.03)
        impact = self.k * vol * np.sqrt(np.clip(participation, 0.0, None)) * value
        parts = {"brokerage": brokerage, "stt": stt, "stamp_duty": stamp, "exchange": exchange, "sebi": sebi,
                 "ipft": ipft, "gst": gst, "dp": dp_base, "spread": spread, "impact": impact}
        per_stock = sum(parts.values())
        return FillCosts(per_stock, {k: float(np.sum(v)) for k, v in parts.items()})


def cost_table(model: CostModel, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Statutory + brokerage cost per side, in basis points, on the first day of each regime.

    A sanity table for the report appendix: check one row by hand against a
    contract note from your broker.
    """
    rows = []
    last = None
    for t, d in enumerate(dates):
        bps = model.per_side_statutory_bps(t)
        key = (round(bps["buy_bps"], 4), round(bps["sell_bps"], 4))
        if key != last:
            rows.append({"from": d.date(), **{k: round(v, 3) for k, v in bps.items()},
                         "round_trip_bps": round(bps["buy_bps"] + bps["sell_bps"], 3)})
            last = key
    return pd.DataFrame(rows)
