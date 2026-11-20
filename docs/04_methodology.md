# Methodology: how the code implements each requirement of the brief

This note describes what the code does, rule by rule, so you can explain it and check it. It states facts about the implementation; it does not argue that any setting is right.

Section numbers of the brief ("Requirements for every project") are in brackets.

---

## 1. Point-in-time information only  [1, bullet 1]

**Rule in the code:** a decision taken at the close of day *t* reads nothing dated after *t*, and trades no earlier than day *t+1*.

| Input | How it is kept point-in-time |
|---|---|
| Prices, volumes | From the exchange's daily bhavcopy for each date. Signals use rows up to *t* only. |
| Market capitalisation | From the exchange's daily market-cap file for that date (January 2024 onwards). Before it, the market cap in the exchange's latest published market-cap list (from 31 March 2020), carried forward with the split-adjusted price; a list is used only from 30 days after its as-on date and for at most 400 days. Where both exist the estimate is within 5% of the daily figure on 93% of stock-days. |
| Universe | Rebuilt on every decision date from the size measures the group lists, in order (`universe.size_metrics`), and trailing liquidity. The measure used on each date is recorded. Today's index members are never applied to the past; the monthly index member lists used for `large_cap_indices` are dated month-end lists. |
| Corporate actions | Taken from the exchange's daily listing of forthcoming actions; the date each action first appeared is stored (`first_seen`), so it can be shown to have been public before its ex-date. |
| Liquidity cap | Uses average traded value up to the day *before* the trade. |
| Risk-free rate | The latest *published* yield is carried forward; no interpolation from later values. |
| Regime, VaR, volatility estimates | Computed from data up to *t*. |
| Sector labels | **Exception:** the latest known sector label is used for all dates (see limitations). |

**Proof in the tests:** `tests/test_backtest.py::test_no_lookahead_truncating_the_data_does_not_change_history` deletes all data after an intermediate date, re-runs the back-test, and requires the NAV path up to that date to be identical. `test_universe_is_point_in_time` and `test_signal_ignores_the_skipped_month_and_the_future` do the same for the universe and the signal.

**No fundamentals are used**, so there is no publication lag to model. If you add a fundamental signal you must lag it yourself.

## 2. No survivorship bias  [1, bullet 2]

**How the historical universe is built** (state this in the report):

1. Every daily bhavcopy from the start date is downloaded, so every security that traded on any day is present, including those that later delisted, merged or were suspended.
2. Lines that are not ordinary equity shares are dropped: series other than EQ/BE/BZ, and ISINs that are not equity shares (this removes ETFs, which trade in the EQ series).
3. Each company gets one permanent id across symbol changes (same ISIN, or the exchange's symbol-change list) and ISIN changes (same symbol, continuous in time). A symbol re-used by an unrelated company after a long gap is treated as a new company (`identifiers.py`).
4. On each decision date, all companies that traded within the last five sessions are ranked by the first size measure in `universe.size_metrics` that can rank deep enough on that date (see the table above); segments are cut from that ranking.

**What happens to a holding that stops trading:** it cannot be sold. It is carried at its last price; after `risk.max_stale_days` sessions without a trade it is written off at `risk.delisting_return`, or at the stock-specific outcome if you supply `data/external/delistings.csv`. Each write-off is listed in `15_writeoffs.csv`.

**Evidence pack:** `data/processed/quality/exits.csv` lists every company that left the sample.

## 3. Transaction costs  [1, bullet 3]

Each fill pays (`costs.py`):

| Component | Basis | Source of the rate |
|---|---|---|
| Brokerage | % of trade value, both sides | your assumption (`costs.brokerage_bps_per_side`) |
| Securities Transaction Tax | % of trade value, both sides (delivery) | `config/statutory_charges.yaml`, dated |
| Stamp duty | % of buy value (buyer only since 1 July 2020) | same |
| Exchange transaction charge, SEBI fee, investor-protection levy | % of trade value, both sides | same |
| GST / service tax | % of brokerage + exchange + SEBI charges (not of trade value, STT or stamp duty) | same |
| Depository charge | rupees per stock sold per day, optional | your assumption |
| Half bid-ask spread | % of trade value | estimated per stock per day (below), or fixed |
| Market impact | `k x daily volatility x sqrt(order value / ADV)` x trade value | `k` is your assumption |

**Spread estimate.** Two published estimators that need only daily high, low and close are computed for every stock-day in `data/panel.py`: Corwin & Schultz (2012) and Abdi & Ranaldo (2017). The model uses the trailing `spread_window_days` average, floored at half a tick (Rs 0.05 / 2 / price) and capped at `max_half_spread_bps`. A stock with no estimate gets that day's cross-sectional median. The value known the day before the trade is used.

**Gross and net.** The same strategy is run three times: *paper* (no costs, no liquidity limits, no circuit blocks), *gross* (limits and blocks, no costs) and *net* (everything). `06_paper_gross_net.csv` reports all three.

**Check:** `08_cost_per_side_schedule.csv` gives the statutory-plus-brokerage cost per side in basis points for each regime. Compare one row with a broker's contract note. `tests/test_costs.py` checks a Rs 1 crore trade against a hand calculation.

## 4. Liquidity limits  [1, bullet 4]

- **Minimum liquidity filter** (universe stage): median daily traded value over `liquidity_lookback_days` must be at least `min_median_traded_value_inr`, and the stock must have traded on at least `min_traded_days_fraction` of those days.
- **Participation cap** (execution stage): each day at most `participation_cap` x (average daily traded value over `adv_window_days`, as known the day before) can be traded in a stock. With `cap_on_realised_volume: true` the same share of the day's actual traded value is a second ceiling.
- **Unfilled remainder:** carried to the following sessions. A buy lapses after `max_execution_days`; a sell is worked until complete.

**"How many signals the limits blocked"** is reported at two stages:

| Table | What it counts |
|---|---|
| `10_blocked_screening.csv` | Stocks that ranked in the top N by score among all stocks in your size segments but were removed by a filter (which one), by the sector limit, or by a stop-loss cool-down. |
| `10_blocked_orders.csv`, `10_blocked_speed.csv` | Orders placed: filled completely, on the first day or later, lapsed partly filled, lapsed unfilled, superseded by the next rebalance. Value ordered vs value filled. |
| `10_blocked_execution.csv` | Order-days on which a fill fell short, by cause: liquidity cap, upper circuit, lower circuit, no trading, cash. |

"Cash" means a buy was scaled back because the sales meant to fund it were themselves held up; the engine never borrows.

## 5. Indian market frictions  [1, bullet 5]

- **Circuit limits.** A stock is treated as locked at the close when its close equals the day's high (or low) and either the exchange's band-hit file names it or its move from the previous close equals a standard band (2, 5, 10 or 20%) to within a tick (`frictions.circuit_lock`). A buy is not filled on a day the stock closes locked at its upper band; a sell is not filled on a day it closes locked at its lower band. The order waits.
- **Non-trading days.** No fill in a stock on a day it did not trade.
- **Surveillance.** Stocks in the trade-for-trade series (BE, BZ) are excluded by `allowed_series: [EQ]`; this is a point-in-time proxy, because moving a stock to trade-for-trade is one of the surveillance actions. If you supply the ASM/GSM history (`data/external/surveillance.csv`), listed stocks are excluded for the dates they were listed. An optional filter drops stocks that closed at a band on too many recent days.
- **Short selling.** The strategy is long-only in cash equities, so the rule that a cash-market short must be closed the same day is respected by construction. The optional hedge is a short position in *index futures*, not in stocks; it is off unless you enable it.
- **Price bands and F&O stocks.** Stocks with derivatives have no fixed daily band; the lock test simply never fires for them.

## 6. Out-of-sample discipline  [1, bullet 6]

- `python run.py search` runs your grid **on the training period only** and appends every run to `results/research/trials.csv`. The number of parameter combinations tried is the number of distinct fingerprints in that file.
- `python run.py freeze` records the final parameter set, when it was frozen, how many sets preceded it, and whether the test period had been opened before.
- `python run.py test` (and `full`) writes a line to `results/research/test_access_log.csv` each time, noting whether the configuration matched the frozen one.
- `21_sharpe_significance.csv` reports the probabilistic and deflated Sharpe ratios (Bailey & Lopez de Prado 2014) using the number of trials and the spread of their Sharpe ratios.

The logs record what was done; they cannot stop you doing it. If you open the test period more than once, the log will say so, and the report should too.

---

## 7. The four components  [2]

### Trading signals (`signals.py`)
- **Entry:** at each rebalance, among eligible stocks with a valid score, the top `n_holdings` (or `top_fraction`) by score that are not already held, subject to the sector limit.
- **Exit:** at a rebalance, a holding is sold when it falls below rank `exit_rank_multiple x N` or stops being eligible; between rebalances, when a stop-loss fires (if enabled) or the drawdown controller cuts exposure.
- **Timing:** scores use data to the close of the decision day; orders are worked from the next session.
- **Windows:** look-back and skip are counted in trading days (`days_per_month`, default 21).

### Portfolio construction (`portfolio.py`, `strategy.py`)
Rank -> select with the exit buffer -> size (equal / inverse volatility / rank / minimum variance) -> cap each stock and each sector by water-filling (weight above a cap goes to names with room; what cannot be placed stays in cash) -> average the last K fresh portfolios if `holding_months` exceeds 1 -> multiply by the invested fraction.

The invested fraction is `gross_exposure x (1 - cash_buffer) x` the smallest of the overlay multipliers (VaR limit, ES limit, volatility target, beta cap, regime switch, drawdown tier, futures margin). `15_decisions.csv` records every multiplier on every rebalance date, so you can see which limit was binding when.

### Risk management (`risk.py`, `stress.py`)
- **VaR / ES:** historical simulation - today's weights applied to the last `lookback_days` of the constituents' returns - or Gaussian, or Cornish-Fisher. Computed at each rebalance for the limit, and daily for the back-test of the VaR model (Kupiec and Christoffersen tests in `13_var_backtest_summary.csv`).
- **Drawdown controller:** soft and hard tiers, a cooling-off period at the hard tier, then the high-water mark is reset. A cut is acted on the next session; a restoration waits for the next rebalance.
- **Stress tests:** named historical windows; the worst periods in the data for the strategy, the benchmark and the momentum factor; the latest book replayed through each window; a market shock through beta; a liquidity stress (volume haircut and spread widening).
- **Limit monitor:** `14_limit_breaches.csv` lists every day any limit was exceeded.

### Post-trade analysis (`analytics.py`, `factors.py`)
Every item in the brief has a table: return, volatility, Sharpe, Sortino, maximum drawdown and its duration (`01`); hit rate by month and by completed holding (`01`, `09`); turnover (`07`); costs paid (`08`); capacity (`20`); factor exposures (`12`); performance by year (`03`) and regime (`05`); attribution of P&L by stock, sector, size segment and year, and the bridge from stock P&L to the change in NAV (`11`).

---

## 8. The engine's accounting

Positions are held in units of each stock's total-return index, so `value = units x index level` and splits, bonuses and dividends need no special handling. Dividends are in effect reinvested in the same stock on the ex-date at no cost.

Daily identity, checked in the tests to within a thousandth of a rupee:

```
change in NAV = sum over stocks of (change in position value - net cash put in)
                - costs + interest on cash + futures hedge P&L
```

Costs are reconciled three ways (trade log, daily totals, per-stock P&L).

## 9. How returns are adjusted for corporate actions

**Fact found on the real files:** the bhavcopy's previous-close field is *not* adjusted on ex-dates (checked across 2010-2026: of about 1,400 splits, bonuses and rights issues, fewer than ten carry an adjusted previous close). Every adjustment therefore has to come from other evidence. For each stock-day the code uses, in this order:

| Evidence | Base price for the day's return | Logged as |
|---|---|---|
| The corporate-action text gives a split / bonus / rights factor, and (for a large factor) it brings the day's move closer to zero | previous close x factor | `ca_text_only` |
| The same, but the text was cut off before the ratio ("FV SPLIT FROM RS10 TO RS"); the standard ratio that fits the price is taken | previous close x inferred factor | `ca_ratio_inferred` |
| The text's ex-date is a session off and the neighbouring day shows exactly that jump | moved to that day | `ca_text_date_shifted` |
| No text, but the exchange's daily share count changed by a standard ratio and the price moved by it (2024 onwards) | previous close x share ratio | `daily_file_shares` |
| No text, but the ISIN changed and the price moved by a face-value ratio | previous close x that ratio | `isin_change_split` |
| The exchange's previous close agrees with the text, or shows a standard ratio on its own | exchange base | `exchange+ca_text`, `exchange_only` |
| Text gives a large factor the price does not show | previous close (text ignored) | `ca_text_rejected` |
| Demerger or scheme of arrangement, price falls more than 10%, nothing else explains it | return set to zero | `demerger_neutralised` |
| Stock resumes trading after more than 30 days | no return that day | `resumed_after_long_halt` |
| Move above 25% (and more than three ticks) with nothing to explain it | left as is | `unexplained_jump` |

**Reading the texts.** The purpose field holds about 25 characters, so the exchange abbreviates and runs words together (`BON1:1/FV SPL RS.10TORE.1`, `INTDV/SPDV - RS 170 & 25`). The parser was written against every distinct text in the 2010-2026 files; 3 of about 4,200 distinct texts remain unrecognised and are listed in the quality report. The daily listings are collapsed into events by the patterns observed: a corrected text replaces the earlier one; separate events listed side by side (a bonus and a dividend) are combined; the same event listed twice in different words counts once; a listing that stopped well before its ex-date was withdrawn.

**Dividends** are added to the day's return. A dividend above 20% of the price is used only if it explains the fall (special dividends of that size do occur; so do misread amounts). Rights issues are priced at premium + face value; the face value comes from the exchange's daily file, the list of listed securities (by ISIN) and the split texts, and where it cannot be established the premium alone is used and the row is labelled.

**Independent check.** The quality report rebuilds the Nifty 50 and Nifty Next 50 from their published month-end members and weights, using these adjusted returns, and compares the result with the published indices. On the 2012-2026 data: correlation 0.9997 and 0.9992, mean daily difference 0.5 bp and 1.2 bp, one and seven days with a difference above 40 bp. A missed split in a member would show as a large one-day difference naming that stock.

Every non-trivial row is in `data/processed/adjustment_log.csv`; the judgement-based ones are collected in `quality/adjustments_to_check.csv` and the unexplained moves in `quality/unexplained_jumps.csv` (157 on the full history, mostly genuine crashes such as Yes Bank in March 2020 - check them against news before overriding any in `data/external/return_overrides.csv`).

---

## 10. Known limitations (facts about the model, for your limitations section)

Write your own assessment of which of these matter for your strategy.

1. **No exchange market cap before April 2019** in the public archive; from April 2020 to December 2023 market cap is an estimate (latest list x price change), which misses share issues and buy-backs between lists. Years before 2019 can only be ranked by traded value unless you supply AMFI lists.
2. **Sector labels are not point-in-time**, and delisted companies have no label unless you supply one (on the full history about 2,500 of 3,267 companies have none). An unlabelled company is treated as its own group for the sector limit, so the limit only binds on labelled sectors.
3. **Surveillance lists** are proxied by the trading series unless you supply the ASM/GSM history.
4. **Delisting outcomes** are one blanket assumption unless you supply stock-specific outcomes.
5. **Demergers:** the ex-date drop is neutralised and the spun-off company's later return is not captured.
6. **Corporate actions rest on the exchange's text**, cut off at 25 characters. Ratios missing from the text are inferred from the price (3 cases on the full history), and unexplained moves are flagged, not corrected. Index levels before February 2012 are not in the archive.
7. **Execution at the day's VWAP** assumes an order spread evenly through the session. A lock is judged at the close; a stock that locked only part of the day is treated as tradable.
8. **Spread estimators** are noisy for individual stocks and can read zero in quiet periods; the tick floor uses Rs 0.05 throughout.
9. **Impact** is a one-parameter square-root model with no memory: yesterday's trading does not raise today's cost.
10. **VaR by historical simulation** treats a non-trading day as a zero return, understating risk for thinly traded or recently listed holdings.
11. **Benchmarks** from the exchange file are price indices unless you supply total-return series; the universe benchmark is gross of costs.
12. **Self-built factor proxies** (when no factor library is supplied) are not the published Fama-French factors; there is no value factor without book values.
13. **Taxes on capital gains, fund expenses and cash drag from subscriptions and redemptions** are not modelled.
14. **Index-futures hedge** (optional) is approximated as a total-return swap on the index with flat cost and margin rates; basis risk and lot sizes are ignored.
15. **Only NSE** is covered; a company listed only on BSE is absent from the ranking.
