# Investment-committee Q&A: questions to prepare

Eight minutes of Q&A, and either member may be asked anything. These are questions a committee is likely to ask about a momentum strategy in Indian mid, small and micro caps. There are no answers here: under the course rules the analysis must be yours. Each question points to where the evidence is.

Practise answering each one in under a minute, with one number and one exhibit.

## The idea
1. Why should momentum exist at all? Who is on the other side of your trades, and why do they keep losing? *(Literature digest, section 2.)*
2. Why should it be stronger - or weaker - in Indian small caps than in large caps? *(Hong, Lim & Stein 2000; Israel & Moskowitz 2013; your own results by segment in `11_attribution_by_segment`.)*
3. Momentum has been public since 1993. Why has it not been arbitraged away? *(McLean & Pontiff 2016; your test-period result.)*
4. Why this signal rather than plain 12-1? What did you compare, and how many alternatives? *(`trials.csv`; D5 of your design sheet.)*
5. Why not simply buy a momentum index fund? What does your strategy do that the index does not, net of costs? *(Benchmark comparison in `02_relative_to_benchmarks`.)*

## The data
6. How do you know there is no survivorship bias? Show me a company that delisted and tell me what happened to your position. *(`quality/exits.csv`, `15_writeoffs`.)*
7. How do you know there is no look-ahead? *(The truncation test; methodology section 1.)*
8. How are splits and bonuses handled? Show me one you checked. *(`adjustment_log.csv`.)*
9. What happens to your results if delisted holdings lose everything rather than what you assumed? *(Re-run with a different `risk.delisting_return`.)*
10. Your sector labels are today's labels. Does that matter?

## Costs and capacity
11. What does a round trip cost, all in? Break it down. *(`08_costs_by_component`, `08_cost_per_side_schedule`.)*
12. Where does your market-impact number come from? What if it is twice as large? *(Re-run with a higher `impact_coefficient`.)*
13. What is the gap between paper, gross and net returns, and what does it tell you? *(`06_paper_gross_net`.)*
14. How much money can this strategy run? What breaks first as it grows? *(`20_capacity`.)*
15. How many of your signals could you not trade, and why? *(`10_blocked_*`.)*
16. What is your turnover, and what did you do to control it? *(`07_turnover`; exit buffer.)*

## Risk
17. What is the worst loss you should expect in a day, a month, a year? *(`01_performance_summary`, `13_var_backtest_summary`, `18_worst_periods_strategy`.)*
18. Is your VaR model any good? How many breaches against how many expected? *(`13_var_backtest_summary`.)*
19. What happened in March 2020? In the 2018 small-cap correction? *(`18_stress_windows`.)*
20. What is a momentum crash, and what does one do to a long-only book? When did the momentum factor do worst in your sample, and how did you fare? *(Daniel & Moskowitz 2016; `18_momentum_factor_worst_periods`.)*
21. If half your book locks at the lower circuit, what do you do? How long would it take to get out? *(`19_liquidity_stress`, `19_replay_latest_book`.)*
22. Your drawdown rule sells after losses. What if the market bounces the next week? What did the rule cost you in the back-test? *(`15_risk_events`; compare a run with the rule off.)*
23. Who is allowed to override a limit, and how is that recorded? *(Your risk policy, section 2.)*
24. How concentrated is the portfolio? What is the largest sector bet you have ever held? *(`16_sector_weights_avg_peak`, `16_concentration`.)*

## Performance
25. Where did the money come from - which sectors, which years, which size segment? Is it a handful of stocks? *(`11_attribution_*`, `09_trade_stats`: share of P&L from the top ten holdings.)*
26. How much of the return is just market and small-cap beta? What is left? *(`12_factor_regression_*`.)*
27. In what kind of market does the strategy make money, and in what kind does it lose? *(`05_regimes`.)*
28. Your test-period Sharpe ratio is X. How confident are you that the true value is above zero? *(`21_sharpe_significance`; standard error in `01`.)*
29. Why is the test period worse (or better) than the training period?
30. What is your hit rate, and what does the average winner make against the average loser? *(`09_trade_stats`.)*

## Process and integrity
31. How many parameter combinations did you try before settling? *(`trials.csv`, `frozen.json`.)*
32. Did you look at the test period before freezing your parameters? *(`test_access_log.csv`.)*
33. Which parts of this work did an AI tool produce, and how did you check them? *(Your AI-use declaration.)*
34. Walk me through this function. *(Any function - see `08_code_walkthrough.md`.)*
35. Run it. Change the number of holdings to 20 and show me what happens.

## The decision
36. What would make you shut the strategy down?
37. What is the single weakest assumption in your back-test?
38. If you had three more months, what would you test next?
