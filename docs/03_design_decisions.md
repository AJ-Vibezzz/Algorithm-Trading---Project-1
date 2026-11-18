# Design decisions — Slow Diffusion Residual Momentum

**How these decisions were made.** Every decision below was made by our group in a structured review with an AI assistant (Claude, 17–18 November 2026). The assistant explained the options, evidence and trade-offs; we chose. One exception is marked **[AI-suggested, adopted]**: the holdings count. All AI use is declared in the appendix. Before submission, rewrite every "Why" in your own words.

The full menu of options, evidence and facts behind each decision is in `docs/03_design_options.md`.

**Objective.** A consistent return generator for a closely held ₹50 crore fund (family, friends and known associates).

**Economic rationale (the edge).** Slow information diffusion (Hong, Lim & Stein 2000). Indian mid and small caps get little analyst and retail attention, so firm-specific news reaches their prices over months rather than days. *To add: one sourced figure on analyst coverage of Indian small caps.*

---

## D1. Sample period and split

**Our decision:** Training runs from February 2012, the first date the 24-month residual signal exists, to December 2016 (58 monthly decisions, 29 Feb 2012 – 30 Nov 2016). The test runs from January 2017 to October 2026 (117 decisions, Jan 2017 – Sep 2026).

**Why:** We compare only four settings on training data, so this period is enough to choose on, and it already contains two small-cap falls (2013 and 2015–16). A ten-year test gives the most convincing out-of-sample record and keeps every episode the brief names unseen. It also spans both the pre- and post-2020 retail regimes, which lets us test our prediction that momentum weakens after the retail boom: compare 2017–19 with 2020–26 inside the test.

## D2. Fund size

**Our decision:** ₹50 crore, a closely held fund for family, friends and known associates. In practice this would be a [PMS / Category III AIF]; check the current SEBI minimums.

**Why:** This is the realistic size of the mandate we are pitching. At this size, a full position (₹1.67 crore) in the median eligible small cap took about 7 trading days to build in 2012 and 9 in 2013, but under half a day by 2024–26; mid caps took about 2 days early on and under 1 later. Capacity is therefore mainly a training-period constraint. The capacity sweep (₹50 cr to ₹1,000 cr) shows where returns begin to erode (Korajczyk & Sadka 2004).

## D3. Benchmark

**Our decision:** Nifty Midsmallcap 400 TRI as the primary benchmark, and the equal-weight eligible universe as the second.

**Why:** The index covers the same ranks (101–500) as our universe, so beating it means our selection added value. The total-return version includes dividends, as our strategy does. The equal-weight universe holds the same stocks without the signal, which isolates the signal's contribution.

## D4. Universe and filters

**Segments:** Ranks 101–500 (mid and small caps), matching the Nifty Midsmallcap 400.

**Why:** Both mid and small caps receive little analyst and retail attention, so news reaches their prices slowly. Micro caps are excluded because, without surveillance data, we cannot separate information-driven moves from manipulation. About 400 stocks give the ranking enough breadth. Because low retail participation contributed to slow diffusion, we expect momentum to weaken after the post-2020 retail boom. *To add: a sourced figure on demat account growth.*

**Size measure:** Market cap where it exists (April 2019 onwards), traded value before that, with the top 100 fixed from the Nifty 50 and Nifty Next 50 lists.

**Why:** Attention, and so the speed of diffusion, is closely linked to size, because analyst coverage follows size. Before 2019 the archive has no market-cap data, so traded value stands in. Training and test therefore pick stocks by different rules: in the overlap the two rankings agree on only 42–54% of small caps. We report a robustness run that ranks by traded value throughout.

**Liquidity floor:** A median daily traded value of ₹1.1 crore over six months.

**Why:** The floor is derived rather than chosen. A full position (₹50 cr ÷ 30) must be fillable within 15 days at a 10% participation cap. In 2012–13 this excludes most small caps (about 195 eligible stocks in 2012 and 168 in 2013, against about 350 later), so the early book leans towards mid caps.

**Other filters:**

- At least two years of listing, which the residual signal needs anyway.
- A minimum price of ₹20 (a penny-stock screen).
- Traded on at least 90% of days.
- Out if locked at a price band on more than 10% of the last 60 days.
- EQ series only. This is the only surveillance screen in effect, because no ASM/GSM list is available.

## D5. Signal

**Our decision:** Market-residual momentum. Each stock's daily returns are regressed on the market return over the 24 months ending on the decision date. Its score in each window is the sum of residuals divided by (their standard deviation × √days), a t-statistic, so the signal is scaled by residual volatility. Two windows are equally weighted: months 12 to 2 and months 7 to 2 before the decision, skipping the most recent month. Only stocks whose own total return over months 12 to 2 is positive are eligible, and an information-discreteness filter with cutoff 0 (same window, raw daily returns) is applied to them. No volatility filter.

**Why:**

- **Residual returns.** The residual removes the part of a stock's return driven by the market, leaving the firm-specific drift that slow diffusion predicts (Blitz, Huij & Martens 2011).
- **24-month estimation.** In mid and small caps most return variation is stock-specific: in our universe the market explains a median 11–24% of a stock's daily variation, depending on the year (75th percentile 19–36%). A shorter window therefore costs little precision. It also keeps 2012 in training and lets the strategy trade a new listing after two years rather than three. A 36-month version is reported as a robustness check.
- **Two windows.** News from the past six months is only partly reflected in prices, while older news has largely diffused. The blend therefore gives recent drift more weight while keeping a full year of evidence, as NSE's momentum indices do with 6 and 12 months.
- **Skipping the latest month.** Very short-term returns tend to reverse (Jegadeesh 1990).
- **Positive raw return required.** Residual momentum can rank a stock highly just because it fell less than the market, and a long-only fund for family investors should own stocks that are actually rising. The screen also makes the smoothness filter work as intended: the discreteness measure flips sign for stocks with a negative raw return, and without the screen the filter doubled the number of steady decliners in the top 30 in weak years (2012, 2019). Combining relative and absolute strength also beats plain price momentum in India (Sehgal, Pandey & Sen 2024). Cost: in weak markets fewer than 30 stocks may qualify. The fund then stays fully invested in fewer, larger positions, because weights are rescaled to 100% across the names that qualify. The fewest candidates on any date in our data is 22 (about 4.5% each); the 6% cap holds as long as at least 17 names qualify.
- **Smoothness filter.** We want winners whose rise came from a steady flow of news. Momentum concentrates in stocks with continuous information (Da, Gurun & Warachka 2014). A cutoff of 0 removes only winners that fell on more days than they rose, typically band jumps or one-off announcements.
- **No volatility filter.** Excluding volatile stocks would remove strong momentum names (Zhang 2006), so volatility is handled through position sizing instead.
- **Limitation.** The market return used in the residual regression changes source twice: traded-value-weighted until Feb 2012, the Nifty 500 price index until Apr 2020, and cap-weighted after that.

## D6. Holdings and sell rule

**Our decision:** 30 stocks **[AI-suggested, adopted]**, compared with 40 on training data. A sell buffer of 1.5.

**Why:**

- **30 stocks.** A closely held fund needs single-stock losses its investors can tolerate: a 50% fall in one name costs about 1.7% of the fund. It also needs a portfolio they can follow, while staying concentrated enough to express the signal. That is about 9% of the eligible universe in later years, but 15–18% in 2012–13. Forty is tested to see whether broader diversification and smaller positions (₹1.25 crore, so faster fills) beat concentration. The liquidity floor stays at ₹1.1 crore in both runs, so they compare the same universe.
- **Buffer of 1.5.** A stock slipping a few ranks may still be drifting, so we hold it until it falls below rank 45 rather than selling at once. Banding is the most effective way to cut momentum turnover (Novy-Marx & Velikov 2019). It mirrors NSE's 30-stock momentum index [verify the index buffer].

## D7. Position sizing

**Our decision:** Inverse-volatility weights over six months, a 6% maximum per stock, and no sector limit.

**Why:**

- **Inverse volatility.** We keep volatile stocks but hold less of them, so each stock contributes a similar amount of risk.
- **6% cap.** It lets the steadiest climbers, where gradual diffusion is clearest, carry more of the book than NSE's 5% would. Only the least volatile names reach the cap, which keeps single-stock losses acceptable.
- **No sector limit.** 2,520 of 3,267 companies have no sector label, so a sector cap would bind only on a minority. Sector concentration is reported, not limited.
- **Side effects to state.** The early book tilts further towards mid caps. Volatility enters twice: the residual score is divided by residual volatility, and the weights use inverse total volatility. Both push towards steadier stocks.

## D8. Rebalancing, holding period and cash

**Our decision:** Monthly review with a 0.5% no-trade band, compared with a quarterly review on training data. A single portfolio, with no overlapping tranches. A 1% cash buffer.

**Why:** Our objective is a consistent return generator, so we trade only when the portfolio has drifted meaningfully from target. Each month we review the ranking but adjust only positions more than 0.5 percentage points (₹25 lakh) away from their target weight. Holdings are kept until they fall below rank 45, so both buying and selling are threshold-driven rather than calendar-driven. The pipeline cannot trigger a rebalance between monthly reviews, which we state as a limitation. Quarterly review is tested because theory does not settle whether the extra responsiveness is worth its cost. A single portfolio keeps the holdings readable for our investors. The 1% cash buffer covers trading costs and price moves while orders are worked over several days; the fund is otherwise fully invested (exposure is 99% of whatever the risk layers allow), so a larger buffer would be a permanent drag. Idle cash earns the T-bill rate.

## D9. Execution

**Our decision:** Trade at the next day's VWAP, at most 10% of 20-day average traded value and of that day's actual volume. Buy orders are worked for up to 15 days, and buys at upper circuits and sells at lower circuits are blocked.

**Why:**

- **VWAP.** Our orders are built patiently over several days, which is what VWAP represents.
- **10% cap.** It follows the brief and keeps our footprint small in thin stocks.
- **Day's actual volume.** Capping at it prevents trades that could not have been executed on a quiet day.
- **15-day window.** Our edge plays out over months, so a slow build loses little of it. A full position in the median eligible small cap needed about 7–9 days in 2012–13 and under half a day by 2024–26. The 15-day limit covers the thinner eligible stocks.
- **Circuit blocks.** A stock locked at its price band cannot be traded in reality.

## D10. Costs

**Our decision:** Brokerage of 10 bps per side. Spreads from the Abdi–Ranaldo estimator (21-day average, half-spread capped at 2.5%). Market impact from the square-root rule with k = 1.0, plus robustness runs at k = 0.5 and k = 2.0.

**Why:**

- **Brokerage.** SEBI capped the brokerage mutual funds may pay on cash-market trades at 12 bps for most of our sample, and cut it to 6 bps in December 2025 [confirm the final figure in the notified regulation]. A ₹50 crore fund has less bargaining power than a large fund house, so we assume a rate between the old and new caps.
- **Spread.** Our data has no bid–ask quotes, so the spread must be inferred from daily prices. Abdi & Ranaldo (2017) show their estimator is more accurate than Corwin–Schultz and rarely gives impossible negative values in thinly traded stocks. On days a stock is locked at its band both estimators read zero, but our circuit rule blocks trading on those days.
- **Impact.** Published estimates put k near 1 (Almgren et al. 2005; Tóth et al. 2011), but they come from large, liquid markets. No reliable figure exists for Indian small caps, so rather than claim precision we show results at half and double the base.

## D11. Risk framework

**How the layers combine.** Each month, exposure = 99% × the smallest of four limits: the volatility target, the VaR limit, the ES limit and the drawdown tier. They do not multiply. Volatility, VaR and ES are recalculated at the monthly decision; the drawdown tiers are checked daily and can cut exposure between decisions.

**Volatility targeting:** On. The target is 16.78% a year, the Nifty Midsmallcap 400 TRI's annualised volatility over February 2012 – December 2016, with volatility measured over six months. This layer alone allows exposure between 50% and 100%.

**Why:** Our objective is a consistent return generator. Targeting the benchmark's own long-run volatility follows the convention of matching the strategy's average risk (Moreira & Muir 2017), so only the timing of exposure changes. Volatility alone never cuts exposure below half, so the fund takes part in the sharp rebounds after panics, where momentum strategies have historically lost most by being out (Daniel & Moskowitz 2016). Because the smallest limit wins, overall exposure can still fall to 25% at the hard drawdown tier, or lower if a VaR or ES limit binds harder. We note the counter-evidence of Cederburg et al. (2020).

**VaR and ES:** 99% one-day historical VaR over 500 days, limit 4.5% of NAV. 97.5% ES, limit 6%. Exposure is scaled down automatically to fit.

**Why:** These are hard backstops for when volatility jumps faster than the six-month measure reacts. At our 16.8% volatility target a typical 99% one-day VaR is roughly 2.5–3.5%, allowing for fat tails, so the limits should bind rarely, and we report how often they did. ES is included because it measures the tail beyond VaR (Acerbi & Tasche 2002).

**Drawdown tiers:** At 15% below the high-water mark, exposure is halved, and restored once the drawdown recovers to 10%. At 25%, exposure falls to 25% for at least 42 trading days.

**Why:** Drawdown is the risk our investors feel most directly, and our data contains a 60% small-cap fall (2018–20). The tiers cap the damage in a prolonged slide. They never go fully to cash, because momentum's worst losses come in sharp rebounds. The gap between the 15% trigger and the 10% release stops exposure flipping back and forth.

**Other settings:**

- At least 90% of the book must be sellable within 15 days at our participation cap. This is monitored: on a breach, the manager trims the least liquid positions at the next rebalance.
- Policy rule, not simulated in the back-test: any position that drifts above 9% (1.5 × the cap) between monthly reviews is trimmed by the manager. The back-test only changes positions at monthly reviews and when a drawdown tier cuts exposure. How often a position actually exceeded 9% is counted from the daily weights in `logs/positions.csv`; `14_limit_breach_summary.csv` counts days above the 6% cap.
- No per-stock stop-loss, market-timing filter, beta cap or futures hedge.

**Why:** The liquidity standard matches the 15-day window we use to build positions, while the liquidity stress test covers a crisis exit. Stop-losses and timing filters would duplicate protection already given by the sell buffer, the positive-momentum screen, volatility targeting and the drawdown tiers, and would add parameters. The residual signal already removes market exposure from the ranking, so beta is reported rather than limited. The mandate is long-only. Breaches are acted on by the fund manager within one trading day and reported to investors in the monthly update.

## D12. Delisting

**Our decision:** A holding with no trade for 42 trading days is written off at −30%, unless its real outcome is known and entered in `delistings.csv`.

**Why:** Performance-related delistings lose about 30% on average (Shumway 1997), though outcomes range from small losses in share-swap mergers to total loss in insolvency. Our filters (positive momentum, liquidity, two-year listing) make holding a delisting company unlikely; `15_writeoffs.csv` lists any that occurred.

## D13. Stress tests

**Our decision:**

- Historical windows: every small-cap fall of 25% or more in our data (2013, 2015–16, 2018–20, 2022 and 2024–25), the 2020 Covid crash, and the Covid rebound as the momentum-crash episode.
- Instant market shocks: −10%, −20% and −30%.
- Volume cuts: 50% and 75%.
- Spread widening: 2× and 3×.

**Why:** Episodes are dated from our data with `run.py episodes`, not from memory. The windows run from the start of the peak month to the end of the trough month. The 2018–20 fall runs into Covid, so Covid is also shown on its own. The rebound tests the mechanism behind momentum crashes, in which past losers rally (Daniel & Moskowitz 2016); its end date is taken from `18_momentum_factor_worst_periods.csv` [fill after train]. A 75% volume cut divides traded value by four, roughly the liquidity of 2020–21; returning to pre-2016 levels would need a cut of about 90%.

## D14. What the training data decides

**Our decision:** Four runs: 30 vs 40 holdings × monthly vs quarterly review, ranked by Sharpe ratio net of costs, with Calmar also reported. We prefer a setting whose neighbours in the grid also perform well over an isolated best.

**Why:** Everything else follows from our economic argument and our investors' needs, which keeps the trial count small. Testing more settings would make the training winner look better through luck alone, and the deflated Sharpe ratio penalises it (Bailey & López de Prado 2014). These two settings turn on costs, which theory cannot settle. Sharpe matches our objective of consistent returns; Calmar (return ÷ worst drawdown) is what our investors feel.

**Trial count to report:** the 8 runs of our earlier "Smooth Mid-Small" design remain in `trials.csv`. The total at freeze is 8, plus every run of this design, plus any calibration runs. We report the full total.

**Robustness runs, reported but not used for selection:**

- traded-value ranking throughout
- 36-month beta estimation
- impact k = 0.5 and k = 2.0
- the capacity sweep from ₹50 crore to ₹1,000 crore

---

## Still to do

- **Before `train`:** `git pull` (for the positive-momentum screen fix); add both names under `authors`; run `python run.py check`.
- **After `train` and `search`:** read `10_universe_by_date.csv`; fill the momentum-crash end date (D13) from `18_momentum_factor_worst_periods.csv`; add a row per run to `docs/11_research_log.md`.
- **Before `freeze`:** verify the six statutory charges; fill the sourced figures marked *To add* (analyst coverage, demat growth, PMS/AIF minimums, SEBI brokerage cap, NSE index buffer); rewrite every "Why" in your own words.

## Limitations the report must state

- Before April 2019, "mid" and "small" mean liquidity tiers, not market-cap tiers.
- The only surveillance screen is the EQ-series rule.
- There is no sector limit, because sector labels are missing.
- In 2012–13 the universe is small (about 170–195 stocks) and the book leans towards mid caps.
- The market series in the residual regression changes source in 2012 and 2020.
- Positions change only at monthly reviews (and at drawdown cuts). The 9% drift trim is a policy rule the back-test does not simulate.
- In weak markets the fund holds fewer, larger positions (as few as 22) rather than cash.
- The residual regression uses the market only, not size and value factors.
- The training period is short (58 monthly decisions).
