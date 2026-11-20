/*
  SITE DATA: the only file with numbers in it.
  - `facts`, `config` and `episodes` come from the dataset reports and config/strategy.yaml
    (checked against them on 20 Nov 2026; see docs/12_site.md for the source of each value).
  - `results` stays null until the ONE-TIME test run. It is written by
    site/build_site_data.py from the pipeline's output files, never typed by hand.
    The same script sets `meta.generated` and `config.trials`.
*/
window.SITE_DATA = {
  meta: {
    title: "Slow Diffusion Momentum",
    authors: ["Arsh Jad", "Rohit Ranjan"],
    project: "Term project, Algorithmic Trading Strategies",
    repoUrl: null,            // e.g. "https://github.com/<user>/<repo>"; set once the repository is public
    aiNote: "Built with AI assistance (Claude). See the AI-use appendix in the repository.",
    generated: null           // set by build_site_data.py
  },

  facts: {
    companies: 3267,
    stoppedTrading: 625,
    dataStart: "4 Jan 2010",
    dataEnd: "16 Nov 2026",
    // typical number of eligible companies at a monthly decision in each year
    eligible: { y2012: 195, y2013: 168, laterMin: 274, laterMax: 357 },
    rSquaredMedian: "11–24%",
    minCandidates: 22
  },

  config: {
    fundCrore: 50,
    holdings: 30,
    sellBelowRank: 45,
    maxWeight: 0.06,
    participation: 0.10,
    maxExecutionDays: 15,
    liquidityFloorCrore: 1.1,
    brokerageBps: 10,
    impactK: 1.0,
    volTarget: 0.1678,
    volFloor: 0.50,
    varLimit: 0.045,
    esLimit: 0.06,
    softDD: 0.15, softExposure: 0.50,
    hardDD: 0.25, hardExposure: 0.25,
    cashBuffer: 0.01,
    trainStart: "Feb 2012", trainEnd: "Dec 2016",
    testStart: "Jan 2017", testEnd: "Oct 2026",
    trials: 12                 // parameter sets tried on the training period; build_site_data.py writes the logged count
  },

  // Falls of 25% or more in the Nifty Smallcap 100 (python run.py episodes), plus Covid in the Nifty 500.
  episodes: [
    { name: "Small-cap fall", start: "2013-01", end: "2013-08", fall: -0.35 },
    { name: "Small-cap fall", start: "2015-04", end: "2016-02", fall: -0.28 },
    { name: "Small-cap collapse", start: "2018-04", end: "2020-03", fall: -0.60 },
    { name: "Covid crash (Nifty 500)", start: "2020-01", end: "2020-03", fall: -0.38 },
    { name: "Small-cap fall", start: "2022-01", end: "2022-06", fall: -0.33 },
    { name: "Small-cap fall", start: "2024-12", end: "2025-03", fall: -0.25 }
  ],

  /*
    results: null until the test run. Shape when filled by build_site_data.py:
    {
      period: "Jan 2017 – Oct 2026",
      benchmarkName: "Nifty Midsmallcap 400 TRI",
      series: { dates: ["2017-01-31", ...], strategy: [1.0, ...], benchmark: [1.0, ...] }, // growth of ₹1, month-ends
      metrics: {
        strategy:  { cagr, vol, sharpe, maxDrawdown, turnover, costDrag },
        benchmark: { cagr, vol, sharpe, maxDrawdown }
      },
      stress: [ { name, strategy, benchmark }, ... ],     // returns as fractions
      deflatedSharpe: 0.0,   // a PROBABILITY (0 to 1): chance the true Sharpe ratio beats the best
                             // that luck alone would give after config.trials tries (Bailey & López de Prado 2014)
      split2020: { before: { label, strategy, benchmark }, after: { label, strategy, benchmark } }  // yearly return
    }
  */
  // ---- results: written by site/build_site_data.py; do not edit by hand ----
  results: null
  // ---- end of results ----
};
