# Final report - TEMPLATE  (yours to write)

**Limit:** 15 pages plus appendices. **Audience:** an investment committee. **Order required by the brief:** executive summary on page 1, then design, results, stress tests, risk framework, limitations, recommendation.

This skeleton gives a page budget, the exhibits from the report pack that belong in each section, and the questions each section should answer. The text is blank on purpose. Under the course's AI policy, analysis, results and recommendations submitted as your own must *be* your own, so write these sections yourselves from your own run of the pipeline. AI may be used to edit your language, and that use must be declared.

Exhibit names refer to files in `results/<period>/tables/` and `results/<period>/figures/`. Report **training** and **test** periods separately.

The marking scheme is shown against each section.

---

## Page 1 - Executive summary

*One page. The committee may read nothing else.*

- What the strategy is, in two sentences.
- The headline test-period result, net of costs, beside the benchmark (from `test/01_performance_summary`).
- The main risk and how it is controlled.
- Capacity.
- Your recommendation, and the conditions attached to it.

> ______________________________________________________________

## 1. Strategy design and economic rationale  (about 2.5 pages - 3 marks)

*Questions to answer:* Why should momentum earn a return in Indian mid/small/micro caps, and who is on the other side of the trade? Which signal did you choose and why that one? What are the exact entry and exit rules, look-back, skip, holding and rebalancing periods, and the signal timing?

- Source for your reasoning: your completed `docs/03_design_decisions.md` (D5, D6, D8) and the papers you have read.
- Exhibit: a one-row-per-rule table of your final parameters (from `config/frozen_strategy.yaml`).

> ______________________________________________________________

## 2. Data and realism  (about 2 pages - 3 marks)

*Questions to answer:* How was the historical universe built, and how do you know it is free of survivorship bias? How is each input kept point-in-time? Which costs are included and at what rates? What liquidity limits apply, and how many signals did they block? How are circuit limits, surveillance lists and the short-selling rules respected?

- Facts about the implementation: `docs/04_methodology.md` (describe it in your own words).
- Exhibits: `data/processed/quality/by_year.csv` and `exits.csv` (survivorship); `08_cost_per_side_schedule`; `10_blocked_screening`, `10_blocked_orders`, `10_blocked_execution`; figure `09_blocked_signals`.

> ______________________________________________________________

## 3. Portfolio construction  (about 1.5 pages - part of 4 marks)

*Questions to answer:* Universe and filters; position sizing; limits per stock, sector and factor; leverage and cash; rebalancing rules. Why each choice?

- Source: `docs/03_design_decisions.md` (D4, D7, D8, D9).
- Exhibits: `10_universe_by_date`; figure `07_exposure_and_holdings`; figure `14_sector_weights`; `16_concentration`.

> ______________________________________________________________

## 4. Results  (about 3 pages - part of 4 marks)

### 4.1 How the parameters were chosen
*Questions:* What did you compare on the training period, how many combinations in total, and why did you settle where you did?

- Exhibits: `results/research/trials.csv` (state the count); the sensitivity heat-map; `results/research/frozen.json`.

### 4.2 Training period
- Exhibits: `train/01_performance_summary`, `06_paper_gross_net`; figure `01_equity_and_drawdown`.

### 4.3 Test period - untouched
*Questions:* How does out-of-sample performance compare with in-sample, and what do you conclude? Is the Sharpe ratio distinguishable from luck given the number of trials?

- Exhibits: `test/01_performance_summary`, `02_relative_to_benchmarks`, `21_sharpe_significance`; figure `01_equity_and_drawdown`; `results/research/test_access_log.csv` (state how many times the test period was opened).

### 4.4 Post-trade analysis
*Everything the brief lists:* return, volatility, Sharpe, Sortino, maximum drawdown and its duration, hit rate, turnover, costs paid, capacity, factor exposures, performance by year and by regime, attribution of P&L.

- Exhibits: `01`, `03_calendar_years`, `04_monthly_returns`, `05_regimes`, `07_turnover`, `08_costs_by_component`, `09_trade_stats`, `11_attribution_*`, `11_pnl_bridge`, `12_factor_regression_*`, `20_capacity`; figures `03`-`06`, `08`, `10`-`12`, `17`.

> ______________________________________________________________

## 5. Stress tests  (about 1.5 pages - part of 4 marks)

*Questions to answer:* What happened in each named episode, and why? What would those episodes do to today's book? How much of the book could not have been sold? What does a momentum crash look like for a long-only book?

- Exhibits: `18_stress_windows`, `18_momentum_factor_worst_periods`, `19_replay_latest_book`, `19_market_shocks`, `19_liquidity_stress`; figure `16_stress_windows`.

> ______________________________________________________________

## 6. Risk-management framework  (about 2 pages - part of 4 marks)

A condensed version of your written policy (`docs/05_risk_policy_template.md`, completed), with the full policy as an appendix.

- Exhibits: the limits table; `13_var_backtest_summary`; `14_limit_breach_summary`; `15_risk_events`; figure `13_var_backtest`.

> ______________________________________________________________

## 7. Limitations  (about 1 page)

*Questions to answer:* Which weaknesses of the data and the model matter most for *your* strategy, in which direction do they bias the results, and how large could the effect be? What did you not test?

- Starting list of model facts: `docs/04_methodology.md`, section 10. Assess them yourself; add what you found in your own data checks.

> ______________________________________________________________

## 8. Recommendation  (about 0.5 page)

*Questions to answer:* Should the committee allocate capital - how much, with what limits, reviewed when? What would make you withdraw the recommendation?

> ______________________________________________________________

---

## Appendices (not counted in the 15 pages)

| Appendix | Content |
|---|---|
| A | Full parameter table (frozen configuration) and its fingerprint |
| B | Data sources and construction of the universe |
| C | Statutory charge schedule with sources |
| D | Written risk policy in full |
| E | Additional exhibits |
| F | References (every paper, dataset and code library) |
| G | **AI use declaration - must be the last appendix** (`docs/07_ai_use_declaration.md`) |

### Code libraries to cite (appendix F)
Python; NumPy; pandas; SciPy; statsmodels; scikit-learn; Matplotlib; PyArrow; PyYAML; Requests; tqdm; Jupyter; pytest. Cite the versions recorded in `results/<period>/run_manifest.json`.

### Data sources to cite (appendix B)
National Stock Exchange of India archives (bhavcopy, PR archive, index files); RBI DBIE (risk-free rate); the factor library and any benchmark, constituent, sector or surveillance data you supplied, each with its provider and access date.

---

## Presentation (12 minutes + 8 minutes of Q&A; every member presents)

A suggested skeleton only; decide the split between you.

| Minutes | Content |
|---|---|
| 1 | The recommendation, up front |
| 2 | Why momentum should work here |
| 2 | Design: signal, universe, construction |
| 2 | Realism: data, costs, liquidity, frictions |
| 3 | Results: training, then test; costs; capacity |
| 1.5 | Stress tests and risk framework |
| 0.5 | Limitations and close |

Practise the Q&A with `docs/09_ic_qa_prep.md`. Either member may be asked to explain any number and any line of code.
