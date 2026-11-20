# Risk-management policy - TEMPLATE  (yours to write)

The brief asks for "a written policy: VaR and Expected Shortfall limits, maximum drawdown and stop-loss rules, exposure and concentration limits, liquidity limits, stress tests, and who decides what when a limit is breached". Portfolio construction plus this policy carry 4 of the 20 marks.

This file is a skeleton. The headings follow the brief. Under each heading are the **config field** that implements the rule, the **exhibit** that shows how it behaved, and **questions the policy should answer**. The limit values, the reasoning and the governance are deliberately blank: they are design decisions and judgement, which the course rules reserve for you.

Write it as a policy a risk committee would sign: what is measured, how often, against what limit, who is told, what must happen and by when.

---

## 1. Purpose, scope and mandate

*Questions to answer:* What is the fund's objective and benchmark? What may it hold (long-only cash equities in which segments; any futures)? What may it never do? Over what horizon is it judged?

> ______________________________________________________________

## 2. Roles and escalation - who decides what

*Questions to answer:* Who owns the strategy day to day? Who monitors limits independently of that person? Who may approve an exception, for how long, and how is it recorded? Who can halt trading? With two group members, say explicitly how duties are split so that the person taking risk is not the only one checking it.

| Role | Person | Decides | Is informed of |
|---|---|---|---|
| | | | |
| | | | |

> ______________________________________________________________

## 3. Market-risk limits

### 3.1 Value-at-Risk
- **Config:** `risk.var.confidence`, `horizon_days`, `lookback_days`, `method`, `limit_pct_nav`, `action`
- **Exhibits:** `13_var_backtest_summary.csv`, figure `13_var_backtest`, `15_decisions.csv` (columns `var_full`, `mult_var_limit`)
- *Questions:* Why this confidence level, horizon and look-back? Why this estimation method for this portfolio? Is the limit hard (exposure is cut automatically) or a trigger for review? How often is it measured? What did the back-test of the VaR model show (number of breaches against the number expected; did they cluster), and what follows from that?

| Limit | Value | Measured | On breach |
|---|---|---|---|
| 1-day VaR at ___% | ___% of NAV | | |

### 3.2 Expected Shortfall
- **Config:** `risk.es.confidence`, `limit_pct_nav`
- *Questions:* Why have ES as well as VaR? Why this confidence level?

| Limit | Value | Measured | On breach |
|---|---|---|---|
| 1-day ES at ___% | ___% of NAV | | |

### 3.3 Volatility (if used)
- **Config:** `risk.vol_target.*`
- *Questions:* Is volatility targeted or only monitored? What does targeting cost in turnover?

> ______________________________________________________________

## 4. Drawdown and stop-loss rules

### 4.1 Portfolio drawdown
- **Config:** `risk.drawdown.soft_limit`, `soft_exposure`, `hard_limit`, `hard_exposure`, `cooloff_days`, `soft_release_level`
- **Exhibits:** `15_risk_events.csv`, `14_limit_breach_summary.csv`, figure `01_equity_and_drawdown`
- *Questions:* From what reference is drawdown measured? What happens at each tier, and how fast? What is the re-entry rule after the hard tier, and who approves re-entry? What did these rules cost and save in the training period? How do they behave if the market rebounds sharply right after a cut?

| Tier | Drawdown | Action | Who acts | Deadline |
|---|---|---|---|---|
| Soft | | | | |
| Hard | | | | |

### 4.2 Stock-level stop-loss (if used)
- **Config:** `risk.stop_loss.*`
- *Questions:* Trailing or from entry? What about a stock locked at its lower circuit, where the stop cannot be executed? When may the stock be bought again?

> ______________________________________________________________

## 5. Exposure and concentration limits

- **Config:** `portfolio.max_weight_per_stock`, `max_weight_per_sector`, `gross_exposure`, `cash_buffer`, `risk.beta.max_portfolio_beta`
- **Exhibits:** `16_concentration.csv`, `16_sector_weights_avg_peak.csv`, figure `14_sector_weights`, `14_limit_breach_summary.csv`
- *Questions:* The caps apply when a portfolio is formed, and weights drift afterwards. What drift is tolerated, and what forces a trim between rebalances? Is there a limit on factor exposure (beta; size)? Is leverage ever permitted?

| Limit | At formation | Tolerated drift | On breach |
|---|---|---|---|
| Single stock | | | |
| Sector | | | |
| Portfolio beta | | | |
| Gross exposure / cash | | | |

## 6. Liquidity limits

- **Config:** `universe.min_median_traded_value_inr`, `min_traded_days_fraction`, `execution.participation_cap`, `max_execution_days`, `risk.liquidity.*`
- **Exhibits:** `10_blocked_*.csv`, `17_liquidity_profile_last_day.csv`, `19_liquidity_stress.csv`, figure `15_liquidity_profile`
- *Questions:* What is the minimum liquidity to enter the universe? What share of daily volume may be traded? How long may the whole book take to liquidate, in normal and in stressed conditions? What is done with a holding that becomes illiquid, is moved to trade-for-trade, or is locked at a circuit?

| Limit | Value | Measured | On breach |
|---|---|---|---|
| Participation per day | | | |
| Share of book sellable in ___ days | | | |

## 7. Stress testing

- **Config:** `stress.*`
- **Exhibits:** `18_stress_windows.csv`, `18_worst_periods_*.csv`, `18_momentum_factor_worst_periods.csv`, `19_replay_latest_book.csv`, `19_market_shocks.csv`, `19_liquidity_stress.csv`
- *Questions:* Which scenarios are run, how often, and why those? What loss in a stress scenario is acceptable, and what action follows if a scenario shows more? Which scenario is the binding one for this strategy?

| Scenario | Dates / definition | Result | Acceptable? | Action if not |
|---|---|---|---|---|
| | | | | |
| | | | | |
| | | | | |

## 8. Operational and model risk

*Questions:* What data checks run before each rebalance (the data-quality report; unexplained jumps)? What is done when a file is missing or late? Who reviews changes to the code or parameters, and how are they logged (the config fingerprint and trial log)? What would make you stop trusting the model?

> ______________________________________________________________

## 9. Reporting

*Questions:* What goes to the investment committee, how often, and in what form? Which exhibits from the report pack are standing items?

> ______________________________________________________________

## 10. Review of this policy

*Questions:* When are the limits reviewed, and by whom? What evidence would lead you to tighten or loosen one?

> ______________________________________________________________
