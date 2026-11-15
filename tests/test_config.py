"""The configuration layer: blanks are reported, typos are caught, overrides work."""

from pathlib import Path

import pytest

from momo.config import ConfigError, StrategyConfig

REPO = Path(__file__).resolve().parents[1]


def test_template_reports_every_blank_design_field():
    template = StrategyConfig.load(REPO / "config" / "strategy.template.yaml")
    missing = template.missing()
    # the design decisions must be the group's: none of them may be pre-filled
    for field in ["sample.train_start", "universe.segments", "signal.components.0.lookback_months",
                  "portfolio.weighting", "portfolio.max_weight_per_stock", "execution.participation_cap",
                  "costs.brokerage_bps_per_side", "costs.impact_coefficient", "risk.var.limit_pct_nav",
                  "risk.drawdown.hard_limit", "risk.delisting_return", "capital.initial_capital_inr"]:
        assert field in missing, f"{field} should be blank in the template"
    with pytest.raises(ConfigError, match="still empty"):
        template.validate()


def test_example_config_is_flagged_as_example():
    example = StrategyConfig.load(REPO / "config" / "examples" / "smoke_test.yaml")
    example.validate()
    assert example.meta.is_example is True


def test_unknown_key_is_rejected():
    with pytest.raises(ConfigError, match="Unknown key"):
        StrategyConfig.from_dict({"portfolio": {"n_holdngs": 30}})


def test_overrides_and_fingerprint(cfg):
    other = cfg.with_overrides({"portfolio.n_holdings": 31, "signal.components.0.lookback_months": 6})
    assert other.portfolio.n_holdings == 31 and other.signal.components[0].lookback_months == 6
    assert cfg.portfolio.n_holdings != 31, "the original must not be modified"
    assert other.fingerprint() != cfg.fingerprint()
    assert StrategyConfig.from_dict(cfg.to_dict()).fingerprint() == cfg.fingerprint()
    with pytest.raises(ConfigError):
        cfg.with_overrides({"portfolio.does_not_exist": 1})


@pytest.mark.parametrize("path,value,fragment", [
    ("portfolio.weighting", "magic", "weighting"),
    ("execution.participation_cap", 1.5, "participation_cap"),
    ("signal.components.0.skip_months", 12, "skip_months"),
    ("sample.test_start", "2023-01-01", "sample dates"),
    ("risk.drawdown.hard_limit", 0.05, "hard_limit"),
    ("portfolio.exit_rank_multiple", 0.5, "exit_rank_multiple"),
])
def test_range_checks(cfg, path, value, fragment):
    with pytest.raises(ConfigError, match=fragment):
        cfg.with_overrides({path: value}).validate()
