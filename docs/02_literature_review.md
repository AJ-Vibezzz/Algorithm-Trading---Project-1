# Literature digest: momentum, and what it implies for Indian mid-, small- and micro-caps

**What this is.** A reading guide prepared with an AI assistant: the papers that matter for each design decision in this project, a one-paragraph note on what each found, and a pointer to the part of the configuration it bears on.

**What this is not.** It is not your literature review. The course's AI policy allows AI to *find and summarise* papers, and requires you to (a) check every AI-suggested paper against the original and (b) write your own analysis. So:

1. Open each paper you intend to cite and confirm the bibliographic details and the finding yourself.
2. Write your literature section in your own words from the papers, not from this file.
3. Record this file in your AI-use declaration (`07_ai_use_declaration.md` already lists it).

**Verification status** (last column of every table):

| Mark | Meaning |
|---|---|
| **W** | Authors, title, journal, volume and pages were confirmed by web search on 15 November 2026. The *finding* is still the assistant's paraphrase: read the paper before quoting it. |
| **M** | From the assistant's memory; not web-checked. These are well-known papers and the details are very likely right, but treat them as unverified until you have opened the source. |
| **!** | A discrepancy or gap was found; see the note. |

One paper the assistant initially recalled (a working paper on long-only momentum in India attributed to Raju and Chandrasekaran) **could not be found** by search and has been left out. Do not cite anything you cannot open.

---

## 1. The momentum effect: discovery and robustness

| Paper | What it found | Bears on | Status |
|---|---|---|---|
| Jegadeesh & Titman (1993) | Buying US stocks that did well over the past 3-12 months and selling those that did poorly earned significant profits over the next 3-12 months (1965-1989). Introduced the J-month formation / K-month holding design with overlapping portfolios. | `signal.components`, `portfolio.holding_months` | M |
| Jegadeesh & Titman (2001) | The profits persisted in the 1990s, after the first paper was published, which argues against data-mining. Returns turn negative in months 13-60, consistent with delayed overreaction. | Economic rationale; holding period | M |
| Jegadeesh (1990); Lehmann (1990) | Returns reverse at the one-month and one-week horizon. This is why most momentum signals skip the most recent month. | `signal.components[].skip_months` | M |
| Carhart (1997) | Added a momentum factor to the Fama-French three factors; the four-factor model became the standard for performance attribution. | Factor regression in `factors.py` | M |
| Rouwenhorst (1998) | Momentum of similar size in 12 European markets (1980-1995); present in every country and stronger in smaller firms. | External validity | M |
| Fama & French (2012) | Momentum in North America, Europe and Asia-Pacific but not Japan; stronger among small stocks. | Universe segment | M |
| Asness, Moskowitz & Pedersen (2013) | Value and momentum premia appear across eight markets and asset classes and are negatively correlated with each other. | Rationale; diversification with value | M |
| Moskowitz, Ooi & Pedersen (2012) | "Time-series" momentum: an asset's own past 12-month return predicts its next-month return. Basis for absolute-momentum screens. | `signal.filters.require_positive_momentum` | M |

## 2. Why should it work? The economic rationale

The brief asks for "the economic reason the signal should work". The literature offers two families of explanation; your report should say which you find persuasive for Indian small caps and why.

| Paper | Mechanism | Status |
|---|---|---|
| Hong & Stein (1999) | Information diffuses gradually across investors, so prices under-react; trend-chasers then push prices too far. | M |
| Daniel, Hirshleifer & Subrahmanyam (1998) | Overconfidence and self-attribution bias produce delayed overreaction: momentum first, reversal later. | M |
| Barberis, Shleifer & Vishny (1998) | Conservatism makes investors under-react to single news items; representativeness makes them over-react to a run of similar news. | M |
| Grinblatt & Han (2005) | The disposition effect (selling winners early, holding losers) keeps prices from adjusting fully to news, creating predictable drift. | M |
| Hong, Lim & Stein (2000) | Momentum is stronger in smaller firms and in firms with little analyst coverage, especially for bad news ("bad news travels slowly"). Direct support for the slow-diffusion story - and the main reason to expect momentum in less-covered Indian mid and small caps. | M |
| Zhang (2006) | Greater information uncertainty (small, young, volatile, thinly covered firms) goes with stronger momentum. | M |
| Chui, Titman & Wei (2010) | Across countries, momentum profits are larger where the culture is more individualistic. Look up where India sits before using this. | M |
| Conrad & Kaul (1998); Johnson (2002) | Risk-based alternatives: momentum reflects cross-sectional differences in expected returns, or time-varying growth risk. Jegadeesh & Titman (2001) argue against the first. | M |

## 3. Variations on the signal

| Paper | Idea | Config option | Status |
|---|---|---|---|
| George & Hwang (2004) | Nearness to the 52-week high predicts returns as well as past returns do, and without long-run reversal. | `kind: high_52w` | M |
| Novy-Marx (2012) | Momentum comes mainly from performance 12 to 7 months ago, not from the most recent six months. Motivates testing a longer skip. | `lookback_months`, `skip_months` | M |
| Blitz, Huij & Martens (2011) | Residual momentum - ranking on the part of returns not explained by factor exposures - earns similar returns with roughly half the volatility and smaller, steadier factor bets. | `kind: residual` | M |
| Da, Gurun & Warachka (2014) | "Frog in the pan": momentum is stronger and more persistent when the past return built up in many small steps than when it came in a few jumps. | `signal.filters.max_information_discreteness` | M |
| Moskowitz & Grinblatt (1999) | Much of stock momentum is industry momentum, so a momentum book concentrates by sector unless it is limited. | `portfolio.max_weight_per_sector` | M |
| Lee & Swaminathan (2000) | Past trading volume predicts how large and how persistent momentum is. | A possible extension | M |
| NSE Indices, momentum index methodology and white papers | The exchange's momentum indices rank on 6- and 12-month price returns divided by volatility, combined as z-scores into a "normalised momentum score"; rebalanced semi-annually. This is the basis of `kind: risk_adjusted_return`, and the natural passive alternative an investment committee will ask about. | `kind: risk_adjusted_return`; benchmark choice | W (construction confirmed in outline; read the methodology document for the exact formula) |

## 4. Long-only implementation, costs and capacity

A cash-market short must be closed the same day in India, and overnight shorts need stock futures or securities lending, which barely exist for small caps. The literature on long-only momentum and on costs is therefore central.

| Paper | What it found | Bears on | Status |
|---|---|---|---|
| Israel & Moskowitz (2013) | About half of momentum profits come from the long side, and momentum profits show no reliable relation with firm size. Long-only momentum is a defensible design. | Long-only mandate | W |
| Asness, Frazzini, Israel & Moskowitz (2014), "Fact, Fiction and Momentum Investing" | A practitioner rebuttal of common objections (that momentum is too small, short-side only, small-cap only, or killed by costs). Useful Q&A preparation. | Q&A | M |
| Lesmond, Schill & Zhou (2004) | Momentum profits concentrate in stocks that are expensive to trade; after realistic costs they are largely illusory. The sceptical benchmark. | `costs.*`, liquidity filters | M |
| Korajczyk & Sadka (2004) | With price impact modelled, momentum survives up to a finite fund size; equal-weighted versions look best before costs and worst after. The template for a capacity analysis. | `research.capacity_aum_inr`, `portfolio.weighting` | M |
| Frazzini, Israel & Moskowitz (2018), "Trading Costs" (working paper) | Using a large manager's live trades, realised costs are far lower than earlier academic estimates. The optimistic benchmark; note it is institutional execution in liquid markets. | `costs.impact_coefficient` | ! (the search did not return the SSRN record; locate the paper yourself before citing) |
| Novy-Marx & Velikov (2016) | A taxonomy of anomalies by turnover and cost; mid-turnover strategies such as momentum survive, and cost mitigation matters. | Turnover control | M |
| Novy-Marx & Velikov (2019) | Of three cost-mitigation techniques, *banding* (a wider threshold to sell than to buy) is the most effective; simply rebalancing less often gains nothing on net. | `portfolio.exit_rank_multiple`, `portfolio.rebalance` | W |
| Patton & Weller (2020) | Inferring costs from fund returns, real-world momentum falls well short of the paper portfolio. | Paper vs gross vs net | M |
| Almgren, Thum, Hauptmann & Li (2005) | From a bank's order data, impact follows a power law in order size relative to volume (exponent near 0.6). | `costs.impact_coefficient` | ! (page range is cited as 57-62 and 58-62 in different places) |
| Toth, Lemperiere, Deremble, de Lataillade, Kockelkoren & Bouchaud (2011) | The square-root impact law holds across markets; offers a theory for it. | Impact model form | W |
| Grinold & Kahn (2000), *Active Portfolio Management*; Torre (1997), *BARRA Market Impact Model Handbook* | The practitioner rule behind the square-root model: trading a day's volume costs roughly a day's volatility. | Impact model form | M |
| Corwin & Schultz (2012) | Bid-ask spread estimated from daily highs and lows. | `costs.spread_model: corwin_schultz` | W (one catalogue lists pages 719-759 rather than 719-760) |
| Abdi & Ranaldo (2017) | Bid-ask spread from daily close, high and low; more robust in thinly traded stocks. | `costs.spread_model: abdi_ranaldo` | W |
| Amihud (2002) | A simple illiquidity measure (absolute return per unit of volume) and evidence of an illiquidity premium. | Liquidity screens | M |

## 5. Momentum crashes and risk management

| Paper | What it found | Bears on | Status |
|---|---|---|---|
| Daniel & Moskowitz (2016) | Momentum crashes come after market declines, when volatility is high, as the market rebounds; the damage is on the short (loser) side, which behaves like a written call. A long-only book avoids that leg but lags the rebound. | Stress design; regime filter | M |
| Barroso & Santa-Clara (2015) | Momentum's risk is forecastable from its own recent realised volatility; scaling exposure to a constant volatility nearly doubles the Sharpe ratio and removes most of the crash risk. | `risk.vol_target` | W |
| Grundy & Martin (2001) | Momentum's market beta swings with the past market return (winners are high-beta after rallies). Explains why beta should be monitored. | `risk.beta` | M |
| Cooper, Gutierrez & Hameed (2004) | Momentum pays after "up" markets and not after "down" markets. | `risk.regime_filter` | M |
| Moreira & Muir (2017) | Scaling factor exposure by inverse past variance raises alphas across many factors. | `risk.vol_target` | M |
| Cederburg, O'Doherty, Wang & Yan (2020) | Out of sample and in real time, volatility-managed portfolios do not reliably beat their unmanaged versions. A caution against expecting too much from volatility targeting. | `risk.vol_target` | M |
| Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018) | Volatility targeting cuts tail risk across asset classes and helps Sharpe ratios mainly for equities and credit. | `risk.vol_target` | M |
| Han, Zhou & Zhu (working paper, 2014-2016) | A simple stop-loss on momentum positions sharply reduces the worst monthly loss and raises the Sharpe ratio (results gross of costs). | `risk.stop_loss` | W (existence and authors confirmed; versions differ, so cite the version you read) |
| Kaminski & Lo (2014) | Stop-losses help when returns trend or switch regimes, and cost return when prices follow a random walk. | `risk.stop_loss` | M |
| Khandani & Lo (2007, 2011) | The August 2007 "quant meltdown": similar strategies unwinding together. The reference case for crowding risk. | Stress narrative | W |

## 6. Evidence from India and other emerging markets

| Paper | What it found | Status |
|---|---|---|
| Sehgal & Balakrishnan (2002) | An early Indian study of contrarian and momentum strategies. Read it for the result and the sample; the assistant has not seen the full text. | ! (page range cited as 13-19 and 13-20) |
| Ansari & Khan (2012) | Momentum profits in Indian stocks over 1995-2006 that CAPM and Fama-French do not explain. | W |
| Agarwalla, Jacob & Varma (2013, revised 2014) | Built survivorship-bias-adjusted Fama-French and momentum factors for India from CMIE Prowess; the factor series are published in the IIM Ahmedabad data library. This is the factor source for your exposure regressions. | W (check return figures against the PDF, not secondary summaries) |
| Singh, Walia, Panda & Gupta (2022) | In 450 BSE stocks, momentum profits are large but negatively skewed; a simple risk-managed version improves risk-adjusted performance. Indian evidence on crash risk. | W |
| Rouwenhorst (1999) | In 20 emerging markets, momentum, size and value effects resemble those in developed markets. | M |
| Cakici, Fabozzi & Tan (2013) | In 18 emerging markets (1990-2011), momentum everywhere except Eastern Europe; local factors explain returns better than global ones. | W |
| Hanauer & Lauterbach (2019) | In emerging markets, momentum is among the few anomalies that hold up in equal- and value-weighted sorts and in regressions. | W |

## 7. Back-test hygiene

| Paper | Point | Where the code uses it | Status |
|---|---|---|---|
| Bailey & Lopez de Prado (2014) | The best of many trials shows a good Sharpe ratio by luck; the deflated Sharpe ratio corrects for the number of trials and for non-normal returns. | `research.deflated_sharpe`, table `21_sharpe_significance` | W |
| Bailey, Borwein, Lopez de Prado & Zhu (2014) | "Pseudo-mathematics and financial charlatanism": how back-test overfitting arises and why the number of trials must be reported. | Trial registry | M |
| Harvey, Liu & Zhu (2016) | With hundreds of factors tested, a new one should clear a t-statistic of about 3, not 2. | Interpreting significance | M |
| Harvey & Liu (2015), "Backtesting" | Haircuts to reported Sharpe ratios for multiple testing. | Interpreting significance | M |
| McLean & Pontiff (2016) | Anomaly returns fall out of sample and fall further after publication. Expect your test period to be worse than your training period. | Train vs test | M |
| Lo (2002) | Sharpe ratios have sampling error, and annualising by the square root of time is wrong when returns are autocorrelated. | `sharpe_std_error` in the summary table | M |
| Arnott, Harvey & Markowitz (2019) | A protocol for honest back-testing (motivation first, count the trials, protect the hold-out). | The train / freeze / test workflow | M |
| Shumway (1997) | Ignoring what holders receive when a stock delists for poor performance biases returns upward; such delistings lose roughly 30% on average in US data. | `risk.delisting_return` | M |

## 8. Risk measurement and portfolio construction

| Paper | Point | Where used | Status |
|---|---|---|---|
| Artzner, Delbaen, Eber & Heath (1999) | Defines coherent risk measures; VaR is not one (it can penalise diversification). | VaR vs ES in the policy | M |
| Acerbi & Tasche (2002) | Expected Shortfall is coherent. | `risk.es` | M |
| Kupiec (1995) | Test of whether VaR breaches occur as often as the confidence level says. | `risk.kupiec_pof` | M |
| Christoffersen (1998) | Test of whether VaR breaches cluster. | `risk.christoffersen_independence` | M |
| Favre & Galeano (2002) | VaR adjusted for skewness and kurtosis (Cornish-Fisher). | `risk.var.method: cornish_fisher` | M |
| Sortino & Price (1994) | Downside deviation and the Sortino ratio. | `analytics.performance_summary` | M |
| Ledoit & Wolf (2004) | Shrinkage estimator of a large covariance matrix. | `portfolio.weighting: min_variance` | M |
| DeMiguel, Garlappi & Uppal (2009) | Equal weighting is hard to beat out of sample because optimised weights suffer from estimation error. | `portfolio.weighting` | M |
| Newey & West (1987) | Standard errors robust to autocorrelation and heteroskedasticity. | Factor regression t-statistics | M |

## 9. Cases and practitioner pieces

| Item | Why read it | Status |
|---|---|---|
| Bergstresser, Cohen, Cohen & Malloy, "AQR's Momentum Funds (A)", Harvard Business School Case 211-025 (September 2010, revised March 2012) | A firm deciding whether to offer long-only momentum to ordinary investors: turnover, capacity, costs and how to explain the strategy - the same questions your investment committee will ask. | W |
| NSE Indices white papers on the Nifty momentum indices (e.g. Nifty200 Momentum 30, September 2020) | How the exchange constructs and markets momentum indices in India. Written by the index provider, so read the performance claims critically. | W (documents located on niftyindices.com) |
| IIM Ahmedabad Indian Fama-French-momentum data library (web.iima.ac.in/~iffm) | Factor returns for India, with drawdown statistics for the momentum factor. | W (site located; check its current coverage dates) |

## 10. Indian market structure: primary sources to cite

Cite the regulator or exchange document itself, not a broker's summary.

| Fact used in this project | Source to obtain | Status |
|---|---|---|
| Large cap = 1st-100th company by full market capitalisation, mid cap = 101st-250th, small cap = 251st onwards | SEBI circular SEBI/HO/IMD/DF3/CIR/P/2017/114, 6 October 2017 (later consolidated in the Master Circular for Mutual Funds) | W |
| Nifty Microcap 250 = the 250 companies ranked just beyond the Nifty 500 by average full market capitalisation | NSE Indices methodology document / index white paper | W |
| Nifty Midcap 150 = ranks 101-250 and Nifty Smallcap 250 = ranks 251-500 within the Nifty 500 | NSE Indices methodology document | ! (the assistant is confident of this but the search did not return the text; confirm it) |
| Daily price bands of 2%, 5%, 10% or 20%; no band for stocks with derivatives | NSE "price bands" page | W |
| Surveillance frameworks (ASM, GSM): higher margins, 5% bands, trade-for-trade settlement at later stages | NSE FAQs and circulars on ASM and GSM | W (structure confirmed; get the introduction dates and stage rules from the circulars) |
| Statutory charges and their history | Listed line by line, with sources, in `config/statutory_charges.yaml` | see the `checked` flag on each line |

---

## Reference list

Journal abbreviations: JF = Journal of Finance; JFE = Journal of Financial Economics; RFS = Review of Financial Studies; JPM = Journal of Portfolio Management; FAJ = Financial Analysts Journal.

**Checked by web search (status W)**

- Abdi, F., & Ranaldo, A. (2017). A simple estimation of bid-ask spreads from daily close, high, and low prices. *RFS*, 30(12), 4437-4480.
- Agarwalla, S. K., Jacob, J., & Varma, J. R. (2013). Four factor model in Indian equities market. Working Paper No. 2013-09-05, Indian Institute of Management Ahmedabad (revised 2014).
- Ansari, V. A., & Khan, S. (2012). Momentum anomaly: evidence from India. *Managerial Finance*, 38(2), 206-223. doi:10.1108/03074351211193730
- Bailey, D. H., & Lopez de Prado, M. (2014). The deflated Sharpe ratio: correcting for selection bias, backtest overfitting, and non-normality. *JPM*, 40(5), 94-107. doi:10.3905/jpm.2014.40.5.094
- Barroso, P., & Santa-Clara, P. (2015). Momentum has its moments. *JFE*, 116(1), 111-120.
- Bergstresser, D. B., Cohen, L. H., Cohen, R. B., & Malloy, C. J. (2010). AQR's Momentum Funds (A). Harvard Business School Case 211-025 (revised March 2012).
- Cakici, N., Fabozzi, F. J., & Tan, S. (2013). Size, value, and momentum in emerging market stock returns. *Emerging Markets Review*, 16, 46-65. doi:10.1016/j.ememar.2013.03.001
- Corwin, S. A., & Schultz, P. (2012). A simple way to estimate bid-ask spreads from daily high and low prices. *JF*, 67(2), 719-760.
- Han, Y., Zhou, G., & Zhu, Y. Taming momentum crashes: a simple stop-loss strategy. Working paper (first draft 2014).
- Hanauer, M. X., & Lauterbach, J. G. (2019). The cross-section of emerging market stock returns. *Emerging Markets Review*, 38, 265-286. doi:10.1016/j.ememar.2018.11.009
- Israel, R., & Moskowitz, T. J. (2013). The role of shorting, firm size, and time on market anomalies. *JFE*, 108(2), 275-301.
- Khandani, A. E., & Lo, A. W. (2007). What happened to the quants in August 2007? *Journal of Investment Management*, 5(4), 29-78.
- Khandani, A. E., & Lo, A. W. (2011). What happened to the quants in August 2007? Evidence from factors and transactions data. *Journal of Financial Markets*, 14(1), 1-46.
- Novy-Marx, R., & Velikov, M. (2019). Comparing cost-mitigation techniques. *FAJ*, 75(1), 85-102.
- Securities and Exchange Board of India (2017). Categorization and rationalization of mutual fund schemes. Circular SEBI/HO/IMD/DF3/CIR/P/2017/114, 6 October 2017.
- Singh, S., Walia, N., Panda, P., & Gupta, S. (2022). Risk-managed momentum: an evidence from Indian stock market. *FIIB Business Review*, 11(3), 347-358. doi:10.1177/23197145211023001
- Toth, B., Lemperiere, Y., Deremble, C., de Lataillade, J., Kockelkoren, J., & Bouchaud, J.-P. (2011). Anomalous price impact and the critical nature of liquidity in financial markets. *Physical Review X*, 1, 021006.

**Discrepancy or gap found (status !) - resolve before citing**

- Almgren, R., Thum, C., Hauptmann, E., & Li, H. (2005). Direct estimation of equity market impact. *Risk*, 18(7). Pages given as 57-62 or 58-62 in different sources.
- Frazzini, A., Israel, R., & Moskowitz, T. J. (2018). Trading costs. Working paper. Not located by search.
- Sehgal, S., & Balakrishnan, I. (2002). Contrarian and momentum strategies in the Indian capital market. *Vikalpa*, 27(1). Pages given as 13-19 or 13-20.

**From memory (status M) - verify each against the journal before citing**

- Acerbi, C., & Tasche, D. (2002). On the coherence of expected shortfall. *Journal of Banking & Finance*, 26(7), 1487-1503.
- Amihud, Y. (2002). Illiquidity and stock returns: cross-section and time-series effects. *Journal of Financial Markets*, 5(1), 31-56.
- Arnott, R., Harvey, C. R., & Markowitz, H. (2019). A backtesting protocol in the era of machine learning. *Journal of Financial Data Science*, 1(1), 64-74.
- Artzner, P., Delbaen, F., Eber, J.-M., & Heath, D. (1999). Coherent measures of risk. *Mathematical Finance*, 9(3), 203-228.
- Asness, C. S., Frazzini, A., Israel, R., & Moskowitz, T. J. (2014). Fact, fiction, and momentum investing. *JPM*, 40(5), 75-92.
- Asness, C. S., Moskowitz, T. J., & Pedersen, L. H. (2013). Value and momentum everywhere. *JF*, 68(3), 929-985.
- Bailey, D. H., Borwein, J. M., Lopez de Prado, M., & Zhu, Q. J. (2014). Pseudo-mathematics and financial charlatanism: the effects of backtest overfitting on out-of-sample performance. *Notices of the American Mathematical Society*, 61(5), 458-471.
- Barberis, N., Shleifer, A., & Vishny, R. (1998). A model of investor sentiment. *JFE*, 49(3), 307-343.
- Blitz, D., Huij, J., & Martens, M. (2011). Residual momentum. *Journal of Empirical Finance*, 18(3), 506-521.
- Carhart, M. M. (1997). On persistence in mutual fund performance. *JF*, 52(1), 57-82.
- Cederburg, S., O'Doherty, M. S., Wang, F., & Yan, X. (2020). On the performance of volatility-managed portfolios. *JFE*, 138(1), 95-117.
- Christoffersen, P. F. (1998). Evaluating interval forecasts. *International Economic Review*, 39(4), 841-862.
- Chui, A. C. W., Titman, S., & Wei, K. C. J. (2010). Individualism and momentum around the world. *JF*, 65(1), 361-392.
- Conrad, J., & Kaul, G. (1998). An anatomy of trading strategies. *RFS*, 11(3), 489-519.
- Cooper, M. J., Gutierrez, R. C., & Hameed, A. (2004). Market states and momentum. *JF*, 59(3), 1345-1365.
- Da, Z., Gurun, U. G., & Warachka, M. (2014). Frog in the pan: continuous information and momentum. *RFS*, 27(7), 2171-2218.
- Daniel, K., Hirshleifer, D., & Subrahmanyam, A. (1998). Investor psychology and security market under- and overreactions. *JF*, 53(6), 1839-1885.
- Daniel, K., & Moskowitz, T. J. (2016). Momentum crashes. *JFE*, 122(2), 221-247.
- DeMiguel, V., Garlappi, L., & Uppal, R. (2009). Optimal versus naive diversification: how inefficient is the 1/N portfolio strategy? *RFS*, 22(5), 1915-1953.
- Fama, E. F., & French, K. R. (2012). Size, value, and momentum in international stock returns. *JFE*, 105(3), 457-472.
- Favre, L., & Galeano, J.-A. (2002). Mean-modified value-at-risk optimization with hedge funds. *Journal of Alternative Investments*, 5(2), 21-25.
- George, T. J., & Hwang, C.-Y. (2004). The 52-week high and momentum investing. *JF*, 59(5), 2145-2176.
- Grinblatt, M., & Han, B. (2005). Prospect theory, mental accounting, and momentum. *JFE*, 78(2), 311-339.
- Grinold, R. C., & Kahn, R. N. (2000). *Active Portfolio Management* (2nd ed.). McGraw-Hill.
- Grundy, B. D., & Martin, J. S. (2001). Understanding the nature of the risks and the source of the rewards to momentum investing. *RFS*, 14(1), 29-78.
- Harvey, C. R., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M., & Van Hemert, O. (2018). The impact of volatility targeting. *JPM*, 45(1), 14-33.
- Harvey, C. R., & Liu, Y. (2015). Backtesting. *JPM*, 42(1), 13-28.
- Harvey, C. R., Liu, Y., & Zhu, H. (2016). ... and the cross-section of expected returns. *RFS*, 29(1), 5-68.
- Hong, H., Lim, T., & Stein, J. C. (2000). Bad news travels slowly: size, analyst coverage, and the profitability of momentum strategies. *JF*, 55(1), 265-295.
- Hong, H., & Stein, J. C. (1999). A unified theory of underreaction, momentum trading, and overreaction in asset markets. *JF*, 54(6), 2143-2184.
- Jegadeesh, N. (1990). Evidence of predictable behavior of security returns. *JF*, 45(3), 881-898.
- Jegadeesh, N., & Titman, S. (1993). Returns to buying winners and selling losers: implications for stock market efficiency. *JF*, 48(1), 65-91.
- Jegadeesh, N., & Titman, S. (2001). Profitability of momentum strategies: an evaluation of alternative explanations. *JF*, 56(2), 699-720.
- Johnson, T. C. (2002). Rational momentum effects. *JF*, 57(2), 585-608.
- Kaminski, K. M., & Lo, A. W. (2014). When do stop-loss rules stop losses? *Journal of Financial Markets*, 18, 234-254.
- Korajczyk, R. A., & Sadka, R. (2004). Are momentum profits robust to trading costs? *JF*, 59(3), 1039-1082.
- Kupiec, P. H. (1995). Techniques for verifying the accuracy of risk measurement models. *Journal of Derivatives*, 3(2), 73-84.
- Ledoit, O., & Wolf, M. (2004). A well-conditioned estimator for large-dimensional covariance matrices. *Journal of Multivariate Analysis*, 88(2), 365-411.
- Lee, C. M. C., & Swaminathan, B. (2000). Price momentum and trading volume. *JF*, 55(5), 2017-2069.
- Lehmann, B. N. (1990). Fads, martingales, and market efficiency. *Quarterly Journal of Economics*, 105(1), 1-28.
- Lesmond, D. A., Schill, M. J., & Zhou, C. (2004). The illusory nature of momentum profits. *JFE*, 71(2), 349-380.
- Lo, A. W. (2002). The statistics of Sharpe ratios. *FAJ*, 58(4), 36-52.
- McLean, R. D., & Pontiff, J. (2016). Does academic research destroy stock return predictability? *JF*, 71(1), 5-32.
- Moreira, A., & Muir, T. (2017). Volatility-managed portfolios. *JF*, 72(4), 1611-1644.
- Moskowitz, T. J., & Grinblatt, M. (1999). Do industries explain momentum? *JF*, 54(4), 1249-1290.
- Moskowitz, T. J., Ooi, Y. H., & Pedersen, L. H. (2012). Time series momentum. *JFE*, 104(2), 228-250.
- Newey, W. K., & West, K. D. (1987). A simple, positive semi-definite, heteroskedasticity and autocorrelation consistent covariance matrix. *Econometrica*, 55(3), 703-708.
- Novy-Marx, R. (2012). Is momentum really momentum? *JFE*, 103(3), 429-453.
- Novy-Marx, R., & Velikov, M. (2016). A taxonomy of anomalies and their trading costs. *RFS*, 29(1), 104-147.
- Patton, A. J., & Weller, B. M. (2020). What you see is not what you get: the costs of trading market anomalies. *JFE*, 137(2), 515-549.
- Rouwenhorst, K. G. (1998). International momentum strategies. *JF*, 53(1), 267-284.
- Rouwenhorst, K. G. (1999). Local return factors and turnover in emerging stock markets. *JF*, 54(4), 1439-1464.
- Shumway, T. (1997). The delisting bias in CRSP data. *JF*, 52(1), 327-340.
- Sortino, F. A., & Price, L. N. (1994). Performance measurement in a downside risk framework. *Journal of Investing*, 3(3), 59-64.
- Torre, N. (1997). *BARRA Market Impact Model Handbook*. BARRA Inc.
- Zhang, X. F. (2006). Information uncertainty and stock returns. *JF*, 61(1), 105-137.
