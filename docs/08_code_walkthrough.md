# Code walkthrough: what each file does, and what you should be able to explain

In Q&A either member may be asked to explain any part of the code. This guide follows the order in which the pipeline runs. For each module it gives the job, the functions that matter, and **questions to test yourself with**. Read the module's own docstring first; it says the same thing at more length.

```
raw NSE files ──► data/ ──► market.py ──► universe.py ──► signals.py ──► portfolio.py
                                                                             │
        report.py ◄── analytics / factors / stress ◄── backtest.py ◄── strategy.py + risk.py + costs.py
```

---

## 1. Configuration - `src/momo/config.py`

- `StrategyConfig` mirrors `config/strategy.yaml` section by section. A field declared with `_req()` has no default: if it is `null`, `missing()` lists it and `validate()` refuses to continue.
- `fingerprint()` is a hash of the whole parameter set. It labels every run and every logged trial.
- `with_overrides({"portfolio.n_holdings": 30})` makes a modified copy; the parameter search uses it.
- An unknown key in the YAML is an error, so a typo cannot be ignored silently.

*Can you explain:* why the pipeline will not run with a blank field? What the fingerprint is for?

## 2. Data layer - `src/momo/data/`

| File | Job |
|---|---|
| `sources.py` | The URL and local path of each exchange file for a given date, including the switch to the UDiFF bhavcopy layout on 8 July 2024. |
| `download.py` | Fetches files politely and resumably; a manifest remembers dates with no file. |
| `parsers.py` | One function per file layout. The trade date comes from the file name. All parsers of the same kind of data return the same columns. |
| `corporate_actions.py` | Turns text such as `BONUS 1:1` or `FV SPLIT RS.10/- TO RS.2/-` into a price factor, and dividend text into rupees. `build_action_table` keeps the exchange's final wording so an event is not counted twice. |
| `identifiers.py` | Gives each company one id across renames and ISIN changes with union-find; refuses to link a symbol re-used after a long gap. |
| `ingest.py` | Raw files -> yearly parquet files. No interpretation happens here. |
| `panel.py` | Computes adjusted returns (`_compute_returns`), spread-estimator inputs, market cap and circuit-lock flags; writes the audit log. |
| `external.py` | Loads the files you supply. |
| `quality.py` | The data-quality report. |
| `synthetic.py` | Test fixture generator. Not a data source. |

*Can you explain:*
- Why a bonus of 1:1 gives a factor of 0.5, and a split from Rs 10 to Rs 2 a factor of 0.2?
- How the code decides which base price to use on an ex-date, and what `ca_text_only` in the audit log means? (Table in `04_methodology.md`, section 9.)
- Why the first day's return is discarded?
- Why ETFs have to be filtered by ISIN rather than by series?
- What would go wrong in a momentum back-test if a renamed company were treated as a new one?
- How you know delisted companies are in the panel?

## 3. Market data in matrix form - `src/momo/market.py`

`MarketData` holds one (days x companies) array per field. `tri` is the total-return index; `tri_ff` is the same carried forward over days without a trade ("the last price you could have known"). Derived arrays (`adv`, `median_value`, `volatility`, `half_spread`, `traded_fraction`) are trailing-window statistics. `truncate(date)` returns a copy that ends on a date; the no-look-ahead test uses it.

*Can you explain:* the difference between `tri` and `tri_ff`, and where each is used? How the half-spread estimate is formed and why it has a floor?

## 4. Universe - `src/momo/universe.py`

`build_universe` ranks stocks by trailing average market cap, assigns segments from `rank_bounds`, then applies the filters in the order of `FILTER_ORDER`, recording the first one each stock fails. `universe_benchmark_returns` builds the cap- and equal-weighted return of the eligible set. `segment_liquidity_table` describes each segment's liquidity.

*Can you explain:* why the ranking uses a six-month average and only stocks that traded in the last five sessions? Why the order of the filters affects the "reason" counts but not who is eligible?

## 5. Signals - `src/momo/signals.py`

`component_raw` computes one component for all stocks on a decision row; `compute_signal` winsorises, z-scores against the eligible set, averages components and applies the optional screens. The window is rows `(t - J x 21, t - S x 21]`.

*Can you explain:* the 12-1 return of one stock from the numbers in the panel? Why the z-score statistics come from eligible stocks but are applied to all stocks in the segment? What residual momentum removes, and why its estimation window must be longer than its formation window?

## 6. Portfolio construction - `src/momo/portfolio.py`

`select_names` (ranking, exit buffer, sector count limit) -> `raw_weights` (four schemes) -> `apply_caps` (water-filling for stock and sector caps) -> `TrancheBook` (average of the last K fresh portfolios).

*Can you explain:* with N = 30 and `exit_rank_multiple` = 2, when is a holding sold? What happens to weight that cannot be placed under the caps? Why three tranches hold more names than one?

## 7. The decision - `src/momo/strategy.py`

`MomentumStrategy.decide(t, ...)` is the only place a trading decision is made: universe snapshot -> signal -> construction -> tranche blend -> invested fraction. It returns the target weights and a row of diagnostics for `15_decisions.csv`, and `_screen_log` records which top-ranked stocks a limit kept out.

*Can you explain:* which inputs `decide` reads, and why none is dated after `t`? What "binding overlay" means in the decisions table?

## 8. Costs - `src/momo/costs.py`

`StatutorySchedule.daily` looks up the rate in force on each date. `CostModel.fills` returns the cost of a day's fills by component. `cost_table` prints the per-side statutory cost for each regime.

*Can you explain:* the cost of a Rs 1 crore buy today, line by line? What GST is and is not charged on? Why impact per rupee doubles when the order is four times larger?

## 9. Frictions - `src/momo/frictions.py`

`circuit_lock` flags a close pinned at a price band.

*Can you explain:* why you cannot buy at an upper circuit or sell at a lower one? How a lock is detected from end-of-day data, and when that detection could be wrong?

## 10. The engine - `src/momo/backtest.py`

One loop over trading days, four steps per day: work open orders; mark to market and write off stale holdings; apply stop-loss and drawdown rules; on decision days, ask the strategy for targets and turn them into orders for the next day.

Key details:
- `place_orders` converts target weights into signed orders using the decision-day close; it replaces any open orders.
- Fills are limited by the participation cap, circuit locks, non-trading days and available cash.
- Buy orders lapse after `max_execution_days`; sell orders do not.
- Positions are units of the total-return index.
- `RunOptions(costs=False)` gives the gross run; `frictions=False` as well gives the paper run.

*Can you explain:* the timeline of one decision from the close of day t to the last fill? Why the first trade is always dated after the first decision? What `cash` as a block reason means? How a write-off is valued? The accounting identity and why it is a useful test?

## 11. Risk - `src/momo/risk.py`

`var_es` (three methods); `fixed_weight_returns` (historical simulation of today's weights); `exposure_overlays` (each limit as a multiplier, the smallest wins); `DrawdownController` (soft and hard tiers, cooling-off, re-basing); `kupiec_pof` and `christoffersen_independence` (back-tests of the VaR model); concentration and liquidity measures.

*Can you explain:* what a 99% one-day VaR of 3% means, and what ES adds? Why scaling exposure scales VaR one for one in a long-only book? Why the high-water mark is reset after the cooling-off period? What the Kupiec test's null hypothesis is?

## 12. Stress tests - `src/momo/stress.py`

Named windows, worst periods found in the data, replay of the latest book through an episode, market shock through beta, liquidity stress, drawdown episodes of an index, the daily VaR back-test and the limit monitor.

*Can you explain:* the difference between "what happened to the strategy in March 2020" and "what March 2020 would do to today's book"? What `coverage_own_history` warns about? Why the beta-based shock is a floor rather than a forecast?

## 13. Analytics and factors - `src/momo/analytics.py`, `src/momo/factors.py`

One function per exhibit. `factor_regression` runs excess strategy returns on market, size, value and momentum factors with Newey-West standard errors.

*Can you explain:* how Sortino differs from Sharpe? How one-way turnover is defined? What alpha in the factor regression means, and why a long-only momentum book loads on the market? What "contribution" means in the attribution tables?

## 14. Research workflow - `src/momo/research.py`

`grid_search` (training period only, every run logged), `freeze`, `evaluate_test` (logs each look at the test period), `deflated_sharpe`.

*Can you explain:* how many parameter sets you tried, and where that number comes from? Why the best of many trials overstates skill? What the deflated Sharpe probability in your report means in one sentence?

## 15. Charts, report pack, command line

`plots.py` draws; `report.py` writes every table, figure and log for a period plus `run_manifest.json`; `cli.py` and `run.py` provide the commands; `pipeline.py` loads everything once (`Workspace`) and runs back-tests.

## 16. Tests - `tests/`

Each test name states a property. The ones worth being able to describe:

| Test | What it proves |
|---|---|
| `test_total_returns_match_truth_*` | Splits, bonuses, rights and dividends are recovered correctly under both conventions for the exchange's previous close. |
| `test_reused_symbol_is_two_companies`, `test_renames_and_isin_changes_keep_one_company_id` | Company identity. |
| `test_delisted_companies_stay_in_the_panel`, `test_universe_includes_companies_that_later_delisted` | No survivorship bias. |
| `test_no_lookahead_truncating_the_data_does_not_change_history` | No look-ahead anywhere in the pipeline. |
| `test_accounting_identity_holds_every_day`, `test_costs_reconcile_three_ways` | The books balance. |
| `test_decide_on_t_trade_on_t_plus_one`, `test_liquidity_cap_is_respected`, `test_circuit_locks_and_non_trading_days_block_fills`, `test_no_leverage` | The realism rules. |
| `test_statutory_cost_of_one_crore_buy_and_sell_in_november_2024` | The cost model against a hand calculation. |
| `test_lockbox_logs_test_period_access` | The out-of-sample log. |
| `test_notebook_runs_end_to_end_on_the_fixture` | The notebook runs from raw files to exhibits. |

All tests run on synthetic data. They show the code does what it says; they say nothing about whether any strategy is good.
