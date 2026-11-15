# Design decisions: the choices that are yours

The course rules say each group must design its own strategy and parameters, and that AI-generated analysis or recommendations may not be submitted as your own. This sheet is therefore a **menu, not a meal**. For every decision it gives:

- **What it controls** and the field in `config/strategy.yaml`;
- **Options the code implements**;
- **Evidence**: what the literature says, with references you can open (full citations in `02_literature_review.md`);
- **Facts to look at** in your own data before deciding;
- a blank **Our decision / Why**, to fill in yourselves.

Nothing below tells you which option to pick. Where the evidence points two ways, both sides are stated. Your reasons, written here, become the "strategy design and economic rationale" section of the report and your answers in Q&A.

**Order of work.** Decide the items marked **[before data]** first (they fix what you download), then use the training period to inform the rest. Every parameter value you compare on the training data is logged by `python run.py search`, so keep the comparisons few and motivated.

---

## D1. Sample period and the train/test split  **[before data]**

**Controls:** `sample.train_start`, `train_end`, `test_start`, `test_end`; `download_start` in `config/data.yaml`.

**What the brief requires:** fix parameters on a training period, report the untouched test period separately, and say how many combinations you tried.

**Things to weigh**
- The data must start a full signal look-back (plus your minimum listing age) before `train_start`.
- A longer training period gives more market regimes to learn from; a longer test period gives a more convincing out-of-sample result. You cannot have both.
- Which period contains the stress episodes you want to discuss? An episode in the training period has shaped your choices; one in the test period has not.
- The exchange's daily market-cap and corporate-action files must exist for your whole sample. After downloading, check `data/processed/quality/by_year.csv`, column `share_rows_with_shares_outstanding`; if early years are empty, either start later or accept the weaker traded-value size proxy for those years and say so.
- Expect weaker results out of sample: McLean & Pontiff (2016) document this for published anomalies generally.

**Our decision:** ______________________  **Why:** ______________________

## D2. Fund size  **[before data]**

**Controls:** `capital.initial_capital_inr`; `research.capacity_aum_inr` for the capacity sweep.

Fund size is not a detail: it sets how large each order is relative to daily volume, and therefore how often the liquidity cap binds and how much market impact you pay (the impact term grows with the square root of order size over volume).

**Facts to look at:** `python run.py liquidity` prints, for each size segment at each year-end, the spread of typical daily traded value. Compare a typical position (fund size x weight per stock) with `participation_cap x` that traded value.

**Evidence:** Korajczyk & Sadka (2004) show momentum profits survive only up to a finite fund size; Frazzini, Israel & Moskowitz (2018) find lower costs for a large manager in liquid markets.

**Our decision:** ______________________  **Why:** ______________________

## D3. Benchmark

**Controls:** `benchmark.primary`, `benchmark.others`.

**Options:** any index in the NSE index file (`python run.py episodes` lists the names), a total-return series you supply (`data/external/benchmarks/`), or the eligible universe itself (`UNIVERSE_CAP`, `UNIVERSE_EQUAL`).

**Things to weigh**
- The exchange file carries *price* indices; your strategy earns dividends. A total-return index is the fair comparison. If you use a price index, say so.
- The universe benchmark holds the same stocks without the signal, so it isolates stock selection; but it is gross of costs.
- An investment committee will ask "why not buy a momentum index fund instead?" Decide whether a Nifty momentum index belongs among your benchmarks.

**Our decision:** ______________________  **Why:** ______________________

## D4. Universe and filters

**Controls:** `universe.*`.

**Options:** `source: mcap_rank` (rebuild segments from the exchange's daily market-cap file) or `index_constituents` (your own point-in-time membership file); any subset of mid / small / micro; filters on listing age, price, liquidity, share of days traded, share of days at a price band, surveillance status.

**Conventions (facts):** SEBI defines large cap as ranks 1-100, mid cap 101-250 and small cap 251 onwards by full market capitalisation. NSE's Smallcap 250 and Microcap 250 indices cover ranks 251-500 and the next 250.

**Evidence**
- Momentum is stronger in smaller and less-covered firms (Hong, Lim & Stein 2000; Zhang 2006), which is the case for going down the size ladder.
- Those are also the stocks that are costly to trade (Lesmond, Schill & Zhou 2004), which is the case against.
- Israel & Moskowitz (2013) find no reliable relation between momentum profits and size.

**Indian frictions the filters address:** trade-for-trade series (BE/BZ) and surveillance lists restrict trading; narrow price bands lock stocks at a limit; low-priced stocks have a wide spread in percentage terms because the tick is fixed in paise.

**Facts to look at:** `python run.py liquidity`; the per-date counts in `results/train/tables/10_universe_by_date.csv` (how many stocks each filter removes); `10_blocked_screening.csv` (how many top-ranked signals each filter removed).

**Our decision:** ______________________  **Why:** ______________________

## D5. The momentum signal

**Controls:** `signal.components` (kind, look-back, skip, weight) and `signal.filters`.

| Option | Idea | Reference |
|---|---|---|
| `return`, look-back J, skip S | cumulative total return from J months ago to S months ago | Jegadeesh & Titman (1993) |
| skip the latest month | avoids one-month reversal | Jegadeesh (1990); Lehmann (1990) |
| longer skip | momentum may come mainly from months 12 to 7 | Novy-Marx (2012) |
| `risk_adjusted_return` | return divided by volatility | NSE Indices momentum methodology |
| `residual` | momentum in market-residual returns; lower volatility, steadier factor exposure | Blitz, Huij & Martens (2011) |
| `high_52w` | nearness to the highest price in the window | George & Hwang (2004) |
| several components | average of z-scores, e.g. two look-backs | NSE Indices methodology combines 6 and 12 months |
| `require_positive_momentum` | hold only stocks with positive absolute momentum | Moskowitz, Ooi & Pedersen (2012) |
| `max_information_discreteness` | prefer momentum built from many small moves | Da, Gurun & Warachka (2014) |
| `max_volatility_percentile` | drop the most volatile stocks | a design choice; test it |

**Things to weigh**
- A shorter look-back reacts faster and trades more; costs rise with turnover.
- `residual` needs a long history (set by `residual_estimation_months`), which excludes recent listings.
- State the *economic reason* your chosen signal should work in Indian small caps (section 2 of the literature digest lists the candidate mechanisms). A signal chosen only because it had the best back-test is the weakest possible answer.
- Each extra option you compare is another trial.

**Our decision:** ______________________  **Why:** ______________________

## D6. How many stocks, and when to sell

**Controls:** `portfolio.selection`, `n_holdings` / `top_fraction`, `exit_rank_multiple`.

**Things to weigh**
- Fewer names: a purer signal, more stock-specific risk, larger positions relative to volume.
- More names: diversification, a diluted signal, more small trades.
- The exit buffer (`exit_rank_multiple` above 1) keeps a holding until it has fallen well down the ranking. Novy-Marx & Velikov (2019) find this "banding" the most effective way to cut costs; the price is holding stocks whose signal has faded.

**Facts to look at:** in the training pack, `07_turnover.csv`, `08_costs_by_component.csv`, `09_trade_stats.csv`.

**Our decision:** ______________________  **Why:** ______________________

## D7. Position sizing and concentration limits

**Controls:** `portfolio.weighting`, `max_weight_per_stock`, `max_weight_per_sector`; optional `risk.beta.max_portfolio_beta` (a factor limit).

| Option | Property | Reference |
|---|---|---|
| `equal` | simple; no estimation error; trades small stocks as hard as large ones | DeMiguel, Garlappi & Uppal (2009) |
| `inverse_vol` | equalises risk roughly; tilts to low-volatility names | volatility-scaling literature |
| `score` | more weight to stronger signals; more concentration | a design choice |
| `min_variance` | lowest estimated portfolio variance (Ledoit-Wolf covariance); depends on a noisy estimate | Ledoit & Wolf (2004) |

**Things to weigh**
- Momentum portfolios concentrate in whichever sectors have been rising (Moskowitz & Grinblatt 1999). A sector limit trades some return for diversification; `10_blocked_screening.csv` shows how many signals it blocks.
- Weights drift between rebalances, so a position can exceed its cap on the way up. `14_limit_breach_summary.csv` reports how often; your risk policy should say what tolerance is acceptable.

**Our decision:** ______________________  **Why:** ______________________

## D8. Holding period, rebalancing and cash

**Controls:** `portfolio.holding_months`, `rebalance`, `no_trade_band`, `cash_buffer`.

- `holding_months: K` above 1 runs K overlapping portfolios, each held for K rebalances, as in Jegadeesh & Titman (1993). It smooths turnover and timing luck.
- Rebalancing less often lowers costs but, by itself, tends to lose as much in return (Novy-Marx & Velikov 2019).
- The cash buffer pays costs and absorbs price moves between decision and execution; it also drags on return.

**Our decision:** ______________________  **Why:** ______________________

## D9. Execution and liquidity limits

**Controls:** `execution.*`.

**What the brief requires:** decide on day t, trade on day t+1; cap each trade at a stated share of average daily traded value (it suggests 10% of 20-day ADV); report how many signals the limits blocked.

**Options:** execution at the next day's VWAP, open or close; the participation cap; whether the cap also applies to the day's actual volume; how many days a buy order is worked before it lapses (sell orders are always worked until done).

**Things to weigh**
- VWAP is what a patient order spread over the day would achieve; the open is the first price after the decision; the close gives the market a full day to move.
- A tighter cap means less impact and more unfilled orders; a looser cap means the reverse. `10_blocked_orders.csv` and `10_blocked_execution.csv` show the effect.

**Our decision:** ______________________  **Why:** ______________________

## D10. Cost assumptions

**Controls:** `costs.*`. (Statutory charges are facts in `config/statutory_charges.yaml`; verify them, do not choose them.)

- **Brokerage:** what would this fund actually pay? Find a rate card or an institutional quote and cite it.
- **Spread:** `corwin_schultz` or `abdi_ranaldo` estimate each stock's spread from its own daily prices; `fixed` applies one number to all. Abdi & Ranaldo (2017) argue their estimator is more reliable in thin stocks.
- **Impact coefficient k:** in `impact = k x daily volatility x sqrt(order / ADV)`. Published estimates come mostly from large, liquid markets (Almgren et al. 2005; Toth et al. 2011; the Grinold-Kahn rule of thumb corresponds to k near 1). Whether Indian small caps deserve a higher or lower figure is for you to argue. Showing results for a low and a high k is one way to be honest about the uncertainty.

**Facts to look at:** `08_costs_by_component.csv`, `06_paper_gross_net.csv`, the capacity table.

**Our decision:** ______________________  **Why:** ______________________

## D11. Risk limits and overlays

**Controls:** `risk.*`. Write the policy itself in `05_risk_policy_template.md`.

| Element | Options | Evidence |
|---|---|---|
| VaR and ES limits | confidence, look-back, method; `scale` (cut exposure to fit) or `flag` (report only) | ES is the coherent measure (Artzner et al. 1999; Acerbi & Tasche 2002) |
| Volatility target | off, or a target annual volatility | for: Barroso & Santa-Clara (2015), Moreira & Muir (2017); against: Cederburg et al. (2020) |
| Drawdown tiers | soft and hard limits, exposure at each, cooling-off period | a policy choice; test its cost on training data |
| Per-stock stop-loss | off, trailing or from entry, threshold, cool-down | for: Han, Zhou & Zhu (working paper); conditions: Kaminski & Lo (2014) |
| Regime switch | off, or cut exposure when an index is below its moving average | Cooper, Gutierrez & Hameed (2004); Daniel & Moskowitz (2016) |
| Beta cap | off, or a maximum portfolio beta | Grundy & Martin (2001) |
| Index-futures hedge | off, or hedge a share of beta | turns the book towards market-neutral; costs margin and carry |
| Liquidity limit | minimum share of the book sellable within N days | monitored; decide the action |

**Things to weigh**
- Every overlay that sells after losses protects against a crash that continues and hurts in one that reverses. Daniel & Moskowitz (2016) show momentum's worst episodes are sharp rebounds.
- Limits that are never reached constrain nothing; limits breached weekly are not limits. `13_var_backtest_summary.csv` and `14_limit_breach_summary.csv` show how yours behave.
- Who decides what when a limit is breached is part of the policy, not of the code.

**Our decision:** ______________________  **Why:** ______________________

## D12. Delisting and suspension

**Controls:** `risk.delisting_return`, `risk.max_stale_days`; optional `data/external/delistings.csv`.

A holding that stops trading cannot be sold. The engine carries it at its last price and, after `max_stale_days`, writes it off at `delisting_return` (or at the stock-specific outcome in your `delistings.csv`).

**Evidence:** Shumway (1997) finds that ignoring delisting outcomes biases returns upward, and that performance-related delistings lose about 30% on average in US data. Outcomes differ by cause: a merger paid in shares loses little; an insolvency can lose everything.

**Facts to look at:** `data/processed/quality/exits.csv` lists every company that stopped trading, with its last price and return over its final six months. `15_writeoffs.csv` in a report pack lists the ones your strategy actually held.

**Our decision:** ______________________  **Why:** ______________________

## D13. Stress tests

**Controls:** `stress.windows`, `market_shocks`, `adv_haircuts`, `spread_multipliers`.

The brief suggests March 2020, the 2018 small-cap correction and a momentum crash. Date the episodes from data rather than from memory:

```bash
python run.py episodes --index "NIFTY SMALLCAP 250" --min-depth 0.15
```

lists every fall of that size with its peak, trough and recovery dates. For a momentum crash, the report pack's `18_momentum_factor_worst_periods.csv` lists the worst months and quarters of the momentum factor in your sample, with what your strategy and benchmark did in each.

**Our decision:** ______________________  **Why:** ______________________

## D14. What to compare on the training data

**Controls:** `research.grid`, `research.selection_metric`.

- List only the alternatives you have a reason to compare.
- `python run.py search` logs every combination; the count goes into the report and into the deflated Sharpe ratio (Bailey & Lopez de Prado 2014), which falls as the count rises.
- Prefer a setting whose neighbours perform similarly over an isolated best (the search writes a heat-map for the first two grid parameters).
- When the design is final: `python run.py freeze`, then, once, `python run.py test`.

**Our decision:** ______________________  **Why:** ______________________
