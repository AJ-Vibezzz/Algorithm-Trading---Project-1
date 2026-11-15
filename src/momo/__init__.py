"""momo - a point-in-time momentum back-testing framework for NSE mid-, small- and micro-caps.

The package is organised in the order the pipeline runs:

    data/        raw NSE files -> clean, survivorship-free daily panel
    universe     point-in-time investable universe and its filters
    signals      momentum scores computed from data known on the decision date
    portfolio    selection, sizing and limits -> target weights
    costs        Indian statutory charges plus spread and market impact
    frictions    circuit locks, tradability, liquidity (ADV) caps
    backtest     daily event loop: decide on t, trade on t+1
    risk         VaR / ES, drawdown, limits monitor
    stress       historical, replay, factor and liquidity stress tests
    analytics    post-trade analysis and attribution
    factors      factor construction and exposure regressions
    research     train/test discipline, parameter trials log, deflated Sharpe
    plots        charts
    pipeline     glue that runs the stages end to end

Nothing in this package chooses a strategy. Every design parameter comes from
``config/strategy.yaml``, which the project group writes and owns.
"""

__version__ = "0.1.0"
