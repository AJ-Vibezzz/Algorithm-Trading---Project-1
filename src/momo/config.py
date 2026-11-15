"""Configuration objects.

Three YAML files drive the pipeline:

* ``config/data.yaml``      where files live and which date range to download
* ``config/costs.yaml``     dated statutory charges (facts) and cost assumptions
* ``config/strategy.yaml``  the strategy design - written and owned by the group

Design parameters deliberately have **no defaults**. A field left as ``null`` in
``strategy.yaml`` is reported by :meth:`StrategyConfig.missing`, and the pipeline
refuses to run a back-test until every required field has been filled in.
That keeps the choice of strategy and parameters with the people who must
defend it.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class ConfigError(ValueError):
    """Raised when a configuration file is malformed or incomplete."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _req(**extra: Any) -> Any:
    """A design field the group must fill in (no default on purpose)."""
    return field(default=None, metadata={"required": True, **extra})


def _sub(cls: type) -> Any:
    """A nested section."""
    return field(default_factory=cls, metadata={"cls": cls})


def _list_of(cls: type) -> Any:
    """A list of nested sections."""
    return field(default_factory=list, metadata={"list_of": cls})


def _to_date(value: Any, path: str) -> Optional[dt.date]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfigError(f"{path}: '{value}' is not a date in YYYY-MM-DD form") from exc


def _from_dict(cls: type, data: Any, path: str = "") -> Any:
    """Build dataclass ``cls`` from a plain mapping, rejecting unknown keys."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path or cls.__name__}: expected a mapping, got {type(data).__name__}")
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise ConfigError(
            f"Unknown key(s) {unknown} under '{path or '<root>'}'. "
            f"Valid keys are: {sorted(fields)}"
        )
    kwargs: Dict[str, Any] = {}
    for name, value in data.items():
        f = fields[name]
        here = f"{path}{name}"
        if "cls" in f.metadata:
            kwargs[name] = _from_dict(f.metadata["cls"], value, here + ".")
        elif "list_of" in f.metadata:
            if value is None:
                value = []
            if not isinstance(value, list):
                raise ConfigError(f"{here}: expected a list")
            kwargs[name] = [
                _from_dict(f.metadata["list_of"], item, f"{here}.{i}.") for i, item in enumerate(value)
            ]
        elif f.metadata.get("date"):
            kwargs[name] = _to_date(value, here)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def _to_plain(obj: Any) -> Any:
    """Dataclass tree -> plain JSON-able structure (dates become ISO strings)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    return obj


def _missing(obj: Any, path: str = "") -> List[str]:
    """Dotted paths of required fields that are still ``None``."""
    out: List[str] = []
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        here = f"{path}{f.name}"
        if dataclasses.is_dataclass(value):
            out += _missing(value, here + ".")
        elif isinstance(value, list) and value and dataclasses.is_dataclass(value[0]):
            for i, item in enumerate(value):
                out += _missing(item, f"{here}.{i}.")
        elif f.metadata.get("required") and value is None:
            out.append(here)
    return out


# --------------------------------------------------------------------------- #
# Strategy configuration (group-owned)
# --------------------------------------------------------------------------- #
@dataclass
class MetaCfg:
    strategy_name: Optional[str] = _req()
    authors: List[str] = field(default_factory=list)
    design_note: Optional[str] = None
    # True only for the shipped example configs. Outputs made with it are stamped
    # "EXAMPLE CONFIGURATION" so they cannot be mistaken for the group's own strategy.
    is_example: bool = False


@dataclass
class SampleCfg:
    """Train / test split. Parameters are fixed on train; test is reported untouched."""

    train_start: Optional[dt.date] = _req(date=True)
    train_end: Optional[dt.date] = _req(date=True)
    test_start: Optional[dt.date] = _req(date=True)
    test_end: Optional[dt.date] = _req(date=True)


@dataclass
class CapitalCfg:
    initial_capital_inr: Optional[float] = _req()
    cash_return: str = "rf"  # "rf": idle cash earns the T-bill rate; "zero": earns nothing


@dataclass
class UniverseCfg:
    source: Optional[str] = _req()  # "mcap_rank" or "index_constituents"
    segments: Optional[List[str]] = _req()  # subset of the keys of rank_bounds
    # Rank conventions: SEBI/AMFI (large 1-100, mid 101-250, small 251+) and
    # NSE Indices (Smallcap 250 = 251-500, Microcap 250 = 501-750).
    rank_bounds: Dict[str, List[int]] = field(
        default_factory=lambda: {
            "large": [1, 100],
            "mid": [101, 250],
            "small": [251, 500],
            "micro": [501, 750],
        }
    )
    mcap_average_days: int = 126  # six months, as in the NSE Indices / AMFI reviews
    # Size measures accepted, in order of preference (required when source = mcap_rank):
    # exchange_mcap | exchange_list | amfi_list | traded_value  - see universe.py
    size_metrics: Optional[List[str]] = None
    # Optional: members of these indices on the decision date take the first ranks, e.g.
    # ["NIFTY 50", "NIFTY NEXT 50"] makes the Nifty 100 the large caps. Empty = rank by size only.
    large_cap_indices: List[str] = field(default_factory=list)
    index_names: List[str] = field(default_factory=list)  # used when source = index_constituents
    allowed_series: List[str] = field(default_factory=lambda: ["EQ"])
    min_listing_days: Optional[int] = _req()
    min_price_inr: Optional[float] = _req()
    min_median_traded_value_inr: Optional[float] = _req()
    liquidity_lookback_days: Optional[int] = _req()
    min_traded_days_fraction: Optional[float] = _req()
    max_circuit_days_fraction: Optional[float] = None  # optional filter; null = off
    circuit_lookback_days: int = 60
    exclude_surveillance: bool = True


@dataclass
class SignalComponent:
    kind: Optional[str] = _req()  # return | risk_adjusted_return | residual | high_52w
    lookback_months: Optional[int] = _req()
    skip_months: Optional[int] = _req()
    weight: float = 1.0
    residual_estimation_months: int = 36  # only used by kind = residual


@dataclass
class SignalFilters:
    require_positive_momentum: bool = False
    max_volatility_percentile: Optional[float] = None  # e.g. 0.9 drops the most volatile 10%
    max_information_discreteness: Optional[float] = None  # "frog in the pan" screen; null = off


@dataclass
class SignalCfg:
    components: List[SignalComponent] = _list_of(SignalComponent)
    winsorize_pct: float = 0.01
    min_history_fraction: float = 0.8
    days_per_month: int = 21
    volatility_lookback_days: int = 252
    filters: SignalFilters = _sub(SignalFilters)


@dataclass
class PortfolioCfg:
    selection: Optional[str] = _req()  # "top_n" or "top_fraction"
    n_holdings: Optional[int] = None  # required when selection = top_n
    top_fraction: Optional[float] = None  # required when selection = top_fraction
    exit_rank_multiple: Optional[float] = _req()  # 1.0 = no buffer; 2.0 = hold until rank > 2N
    weighting: Optional[str] = _req()  # equal | inverse_vol | score | min_variance
    weighting_lookback_days: int = 126
    max_weight_per_stock: Optional[float] = _req()
    max_weight_per_sector: Optional[float] = _req()
    holding_months: Optional[int] = _req()  # K: number of overlapping monthly tranches
    rebalance: Optional[str] = _req()  # monthly | quarterly | weekly
    gross_exposure: float = 1.0
    cash_buffer: Optional[float] = _req()
    no_trade_band: float = 0.0  # a continuing position is left alone if |target - current weight| is below this


@dataclass
class ExecutionCfg:
    price: Optional[str] = _req()  # vwap | open | close  (of day t+1)
    participation_cap: Optional[float] = _req()  # share of ADV tradable per day
    adv_window_days: int = 20
    cap_on_realised_volume: bool = True
    max_execution_days: Optional[int] = _req()
    min_trade_value_inr: float = 0.0
    block_buy_at_upper_circuit: bool = True
    block_sell_at_lower_circuit: bool = True


@dataclass
class CostsCfg:
    """Cost ASSUMPTIONS the group owns. Statutory charges are facts and live in
    config/statutory_charges.yaml."""

    brokerage_bps_per_side: Optional[float] = _req()
    spread_model: Optional[str] = _req()  # corwin_schultz | abdi_ranaldo | fixed
    fixed_half_spread_bps: Optional[float] = None  # required when spread_model = fixed
    spread_window_days: int = 21
    max_half_spread_bps: float = 250.0
    impact_coefficient: Optional[float] = _req()  # k in  k x daily vol x sqrt(trade / ADV)
    impact_vol_window_days: int = 60
    dp_charge_inr_per_sell: float = 0.0  # depository charge per stock sold per day (retail accounts)


@dataclass
class VarCfg:
    confidence: Optional[float] = _req()
    horizon_days: int = 1
    lookback_days: int = 500
    method: str = "historical"  # historical | parametric | cornish_fisher
    limit_pct_nav: Optional[float] = _req()
    action: str = "scale"  # scale: cut exposure to fit the limit; flag: only log the breach


@dataclass
class EsCfg:
    confidence: Optional[float] = _req()
    limit_pct_nav: Optional[float] = _req()


@dataclass
class VolTargetCfg:
    enabled: bool = False
    annual_vol: Optional[float] = None
    lookback_days: int = 126
    min_exposure: float = 0.0
    max_exposure: float = 1.0


@dataclass
class DrawdownCfg:
    soft_limit: Optional[float] = _req()  # e.g. 0.10 means a 10% fall from the high-water mark
    soft_exposure: Optional[float] = _req()  # exposure multiplier once the soft limit is breached
    hard_limit: Optional[float] = _req()
    hard_exposure: Optional[float] = _req()
    cooloff_days: Optional[int] = _req()  # trading days out of the market before re-entry
    soft_release_level: Optional[float] = None  # drawdown at which the soft tier is lifted; null = soft_limit


@dataclass
class StopLossCfg:
    enabled: bool = False
    kind: str = "trailing"  # trailing (from peak since entry) | from_entry
    threshold: Optional[float] = None
    cooldown_days: int = 21


@dataclass
class RegimeCfg:
    enabled: bool = False
    index: Optional[str] = None
    ma_days: int = 200
    exposure_when_below: float = 0.5


@dataclass
class BetaCfg:
    max_portfolio_beta: Optional[float] = None  # null = no beta cap
    lookback_days: int = 252
    benchmark: Optional[str] = None  # index name; null = universe cap-weighted return


@dataclass
class HedgeCfg:
    """Optional index-futures overlay. Off unless the group switches it on."""

    enabled: bool = False
    index: Optional[str] = None
    ratio: float = 1.0  # share of estimated beta exposure to hedge
    margin_rate: float = 0.20
    cost_bps_per_side: float = 2.0
    roll_cost_bps_per_month: float = 2.0


@dataclass
class LiquidityLimitCfg:
    """Portfolio-level liquidity limit (monitored and reported; breaches are flagged)."""

    horizon_days: int = 5
    min_sellable_fraction: Optional[float] = None  # e.g. 0.8: 80% of the book sellable within the horizon


@dataclass
class RiskCfg:
    var: VarCfg = _sub(VarCfg)
    es: EsCfg = _sub(EsCfg)
    vol_target: VolTargetCfg = _sub(VolTargetCfg)
    drawdown: DrawdownCfg = _sub(DrawdownCfg)
    stop_loss: StopLossCfg = _sub(StopLossCfg)
    regime_filter: RegimeCfg = _sub(RegimeCfg)
    beta: BetaCfg = _sub(BetaCfg)
    hedge: HedgeCfg = _sub(HedgeCfg)
    liquidity: LiquidityLimitCfg = _sub(LiquidityLimitCfg)
    delisting_return: Optional[float] = _req()  # assumed return when a holding stops trading for good
    max_stale_days: Optional[int] = _req()  # days without a trade before a holding is written off


@dataclass
class StressWindow:
    name: Optional[str] = _req()
    start: Optional[dt.date] = _req(date=True)
    end: Optional[dt.date] = _req(date=True)


@dataclass
class StressCfg:
    windows: List[StressWindow] = _list_of(StressWindow)
    market_shocks: List[float] = field(default_factory=list)  # e.g. [-0.10, -0.20, -0.30]
    adv_haircuts: List[float] = field(default_factory=list)  # e.g. [0.5, 0.75]
    spread_multipliers: List[float] = field(default_factory=list)  # e.g. [2, 3]


@dataclass
class ResearchCfg:
    """Parameter search on the TRAINING period only. Every trial is logged."""

    grid: Dict[str, List[Any]] = field(default_factory=dict)
    selection_metric: Optional[str] = None
    capacity_aum_inr: List[float] = field(default_factory=list)


@dataclass
class BenchmarkCfg:
    primary: Optional[str] = _req()  # an index name, or "universe_cap" / "universe_equal"
    others: List[str] = field(default_factory=list)


@dataclass
class StrategyConfig:
    meta: MetaCfg = _sub(MetaCfg)
    sample: SampleCfg = _sub(SampleCfg)
    capital: CapitalCfg = _sub(CapitalCfg)
    benchmark: BenchmarkCfg = _sub(BenchmarkCfg)
    universe: UniverseCfg = _sub(UniverseCfg)
    signal: SignalCfg = _sub(SignalCfg)
    portfolio: PortfolioCfg = _sub(PortfolioCfg)
    execution: ExecutionCfg = _sub(ExecutionCfg)
    costs: CostsCfg = _sub(CostsCfg)
    risk: RiskCfg = _sub(RiskCfg)
    stress: StressCfg = _sub(StressCfg)
    research: ResearchCfg = _sub(ResearchCfg)

    # ----- construction ---------------------------------------------------
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyConfig":
        return _from_dict(cls, data)

    @classmethod
    def load(cls, path: str | Path) -> "StrategyConfig":
        path = Path(path)
        if not path.exists():
            raise ConfigError(
                f"{path} not found. Copy config/strategy.template.yaml to {path.name} "
                "and fill in your own design."
            )
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh) or {})

    # ----- introspection --------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return _to_plain(self)

    def fingerprint(self) -> str:
        """Short hash that identifies this exact parameter set in logs."""
        blob = json.dumps(self.to_dict(), sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def with_overrides(self, overrides: Dict[str, Any]) -> "StrategyConfig":
        """Return a copy with dotted-path overrides, e.g. ``{"portfolio.n_holdings": 30}``."""
        data = copy.deepcopy(self.to_dict())
        for dotted, value in overrides.items():
            node: Any = data
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node[int(part)] if isinstance(node, list) else node[part]
            last = parts[-1]
            if isinstance(node, list):
                node[int(last)] = value
            else:
                if last not in node:
                    raise ConfigError(f"Override path '{dotted}' does not exist in the config")
                node[last] = value
        return StrategyConfig.from_dict(data)

    # ----- validation -----------------------------------------------------
    def missing(self) -> List[str]:
        """Required design fields that are still empty."""
        out = _missing(self)
        p = self.portfolio
        if p.selection == "top_n" and p.n_holdings is None:
            out.append("portfolio.n_holdings")
        if p.selection == "top_fraction" and p.top_fraction is None:
            out.append("portfolio.top_fraction")
        if not self.signal.components:
            out.append("signal.components (at least one)")
        r = self.risk
        if r.vol_target.enabled and r.vol_target.annual_vol is None:
            out.append("risk.vol_target.annual_vol")
        if r.stop_loss.enabled and r.stop_loss.threshold is None:
            out.append("risk.stop_loss.threshold")
        if r.regime_filter.enabled and r.regime_filter.index is None:
            out.append("risk.regime_filter.index")
        if r.hedge.enabled and r.hedge.index is None:
            out.append("risk.hedge.index")
        if self.universe.source == "index_constituents" and not self.universe.index_names:
            out.append("universe.index_names")
        if self.universe.source == "mcap_rank" and not self.universe.size_metrics:
            out.append("universe.size_metrics (at least one)")
        if self.costs.spread_model == "fixed" and self.costs.fixed_half_spread_bps is None:
            out.append("costs.fixed_half_spread_bps")
        return out

    def validate(self) -> None:
        """Raise :class:`ConfigError` listing every problem found."""
        problems: List[str] = []
        missing = self.missing()
        if missing:
            problems.append(
                "These design fields are still empty - they are yours to decide "
                "(see docs/03_design_decisions.md):\n    - " + "\n    - ".join(missing)
            )
        problems += self._range_checks()
        if problems:
            raise ConfigError("Strategy configuration is not ready:\n" + "\n".join(problems))

    def _range_checks(self) -> List[str]:
        errs: List[str] = []

        def choice(value: Any, allowed: List[str], name: str) -> None:
            if value is not None and value not in allowed:
                errs.append(f"{name} = '{value}' is not one of {allowed}")

        def between(value: Any, lo: float, hi: float, name: str) -> None:
            if value is not None and not (lo <= float(value) <= hi):
                errs.append(f"{name} = {value} must lie in [{lo}, {hi}]")

        u, s, p, e, r = self.universe, self.signal, self.portfolio, self.execution, self.risk
        choice(u.source, ["mcap_rank", "index_constituents"], "universe.source")
        allowed_sizes = ["exchange_mcap", "exchange_list", "amfi_list", "traded_value"]
        for i, m in enumerate(u.size_metrics or []):
            choice(m, allowed_sizes, f"universe.size_metrics.{i}")
        if len(set(u.size_metrics or [])) != len(u.size_metrics or []):
            errs.append("universe.size_metrics lists the same measure twice")
        known_indices = ["NIFTY 50", "NIFTY NEXT 50"]
        for name in u.large_cap_indices or []:
            if str(name).strip().upper() not in known_indices:
                errs.append(f"universe.large_cap_indices: '{name}' - monthly member lists exist only for {known_indices}")
        if u.segments:
            bad = [seg for seg in u.segments if seg not in u.rank_bounds]
            if bad:
                errs.append(f"universe.segments {bad} have no entry in universe.rank_bounds")
        between(u.min_traded_days_fraction, 0, 1, "universe.min_traded_days_fraction")
        between(u.max_circuit_days_fraction, 0, 1, "universe.max_circuit_days_fraction")
        for i, c in enumerate(s.components):
            choice(c.kind, ["return", "risk_adjusted_return", "residual", "high_52w"],
                   f"signal.components.{i}.kind")
            if c.lookback_months is not None and c.skip_months is not None:
                if c.skip_months >= c.lookback_months:
                    errs.append(f"signal.components.{i}: skip_months must be below lookback_months")
        between(s.winsorize_pct, 0, 0.25, "signal.winsorize_pct")
        between(s.min_history_fraction, 0, 1, "signal.min_history_fraction")
        choice(p.selection, ["top_n", "top_fraction"], "portfolio.selection")
        choice(p.weighting, ["equal", "inverse_vol", "score", "min_variance"], "portfolio.weighting")
        choice(p.rebalance, ["monthly", "quarterly", "weekly"], "portfolio.rebalance")
        between(p.top_fraction, 0, 1, "portfolio.top_fraction")
        between(p.max_weight_per_stock, 0, 1, "portfolio.max_weight_per_stock")
        between(p.max_weight_per_sector, 0, 1, "portfolio.max_weight_per_sector")
        between(p.cash_buffer, 0, 1, "portfolio.cash_buffer")
        if p.exit_rank_multiple is not None and p.exit_rank_multiple < 1:
            errs.append("portfolio.exit_rank_multiple must be >= 1 (1 means no buffer)")
        if p.gross_exposure > 1.0 + 1e-9 and not r.hedge.enabled:
            errs.append("portfolio.gross_exposure above 1 means leverage - not supported long-only")
        choice(self.costs.spread_model, ["corwin_schultz", "abdi_ranaldo", "fixed"], "costs.spread_model")
        for i, c in enumerate(s.components):
            if c.kind == "residual" and c.lookback_months is not None and c.residual_estimation_months <= c.lookback_months:
                errs.append(f"signal.components.{i}: residual_estimation_months must exceed lookback_months")
        choice(e.price, ["vwap", "open", "close"], "execution.price")
        between(e.participation_cap, 0, 1, "execution.participation_cap")
        choice(r.var.method, ["historical", "parametric", "cornish_fisher"], "risk.var.method")
        choice(r.var.action, ["scale", "flag"], "risk.var.action")
        between(r.var.confidence, 0.5, 0.9999, "risk.var.confidence")
        between(r.es.confidence, 0.5, 0.9999, "risk.es.confidence")
        between(r.delisting_return, -1, 0.5, "risk.delisting_return")
        choice(r.stop_loss.kind, ["trailing", "from_entry"], "risk.stop_loss.kind")
        choice(self.capital.cash_return, ["rf", "zero"], "capital.cash_return")
        d = r.drawdown
        if d.soft_limit is not None and d.hard_limit is not None and d.hard_limit < d.soft_limit:
            errs.append("risk.drawdown.hard_limit must not be tighter than soft_limit")
        sm = self.sample
        dates = [sm.train_start, sm.train_end, sm.test_start, sm.test_end]
        if all(x is not None for x in dates):
            if not (sm.train_start < sm.train_end < sm.test_start < sm.test_end):
                errs.append("sample dates must satisfy train_start < train_end < test_start < test_end")
        for i, w in enumerate(self.stress.windows):
            if w.start is not None and w.end is not None and w.end < w.start:
                errs.append(f"stress.windows.{i}: end is before start")
        return errs


# --------------------------------------------------------------------------- #
# Data configuration
# --------------------------------------------------------------------------- #
@dataclass
class DataConfig:
    root: str = "data"
    download_start: Optional[dt.date] = field(default=None, metadata={"date": True})
    download_end: Optional[dt.date] = field(default=None, metadata={"date": True})
    datasets: List[str] = field(default_factory=lambda: ["cm_bhav", "pr", "ind_close", "mcwb", "mcap_lists"])
    archive_base_url: str = "https://nsearchives.nseindia.com"
    max_workers: int = 4
    request_timeout_s: int = 30
    max_retries: int = 4
    pause_s: float = 0.25
    equity_series: List[str] = field(default_factory=lambda: ["EQ", "BE", "BZ"])
    require_equity_isin: bool = True
    # ISIN layout: "IN" + "E" (company) + 4-character issuer + 2-digit security type
    # ("01" = equity share) + 2-digit serial + check digit. ETFs / fund units start "INF".
    equity_isin_regex: str = "^INE.{4}01"
    price_ratio_tolerance: float = 0.005
    shares_ffill_days: int = 10
    # Exchange (and AMFI) size lists: a list is used from its as-on date plus this many calendar
    # days, and for at most ``size_list_max_age_days`` after its as-on date.
    size_list_lag_days: int = 30
    size_list_max_age_days: int = 400
    index_list_max_age_days: int = 100  # monthly index member lists
    demerger_treatment: str = "neutralise"  # neutralise | raw
    include_dividends: bool = True
    symbol_reuse_gap_days: int = 180
    data_kind: str = "real"  # "synthetic" only for the test fixture

    @classmethod
    def load(cls, path: str | Path) -> "DataConfig":
        with open(path, "r", encoding="utf-8") as fh:
            return _from_dict(cls, yaml.safe_load(fh) or {})

    # Directory helpers -----------------------------------------------------
    def path(self, *parts: str) -> Path:
        return Path(self.root).joinpath(*parts)

    @property
    def raw(self) -> Path:
        return self.path("raw")

    @property
    def interim(self) -> Path:
        return self.path("interim")

    @property
    def processed(self) -> Path:
        return self.path("processed")

    @property
    def external(self) -> Path:
        return self.path("external")

    def to_dict(self) -> Dict[str, Any]:
        return _to_plain(self)


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
