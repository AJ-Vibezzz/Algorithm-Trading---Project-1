# The walkthrough website (`site/`)

A 15-slide walkthrough of the strategy for readers with no finance background. It uses plain HTML, CSS and JavaScript, with no build step and no libraries. The brief it was built to is [`docs/site_brief.md`](site_brief.md).

| File | What it is |
|---|---|
| `site/index.html`, `styles.css`, `app.js` | page shell, styling (light and dark), slides and interactions |
| `site/data.js` | **every real number the site shows**; nothing else contains figures |
| `site/build_site_data.py` | fills `results` in `data.js` from the one-time test run |
| `.github/workflows/pages.yml` | publishes `site/` to GitHub Pages on every push to `main` that changes it |

Every illustration on slides 1–13 uses made-up prices and is captioned as such. Real figures come only from `data.js`.

## Preview

```bash
python -m http.server 8000 --directory site
```

Then open http://localhost:8000. Opening `site/index.html` directly also works.

## Where each value in `data.js` comes from

Checked on 20 Nov 2026 against the code, `config/strategy.yaml` and the built dataset.

| Key | Value | Source |
|---|---|---|
| `facts.companies`, `stoppedTrading` | 3,267; 625 | data quality report of the group's build |
| `facts.dataStart`, `dataEnd` | 4 Jan 2010 to 16 Nov 2026 | first and last trading day in the group's build |
| `facts.eligible` | 195 (2012), 168 (2013), 274–357 (each year from 2014) | typical eligible count at a monthly decision in each year, with the strategy's universe filters |
| `facts.rSquaredMedian` | 11–24% | median R² of the 24-month market regressions, by year |
| `facts.minCandidates` | 22 | fewest stocks passing both signal filters at a decision (30 Sep 2013) |
| `config.*` | | `config/strategy.yaml` (fund size, holdings, exit rank 1.5 × 30, weight cap, participation, execution days, liquidity floor, brokerage, impact k, vol target and floor, VaR/ES limits, drawdown tiers, cash buffer, sample dates) |
| `config.trials` | 12 until the test run | replaced by `build_site_data.py` with the count used for the deflated Sharpe ratio (`results/test/tables/21_sharpe_significance.csv`) |
| `episodes` | | `python run.py episodes --index "NIFTY SMALLCAP 100" --min-depth 0.25`; Covid from `--index "NIFTY 500"` |

Corrections made when the site was added:
- **Eligible counts.** "About 350 in later years" became "between 274 and 357 in each later year". 2014, 2019 and 2020 were below 300.
- **2024–25 fall.** The site had the Nifty Smallcap 250's start (Sep 2024) with the Nifty Smallcap 100's depth (−25%). It now uses the Smallcap 100 throughout: 11 Dec 2024 to 3 Mar 2025, −25.4%. For comparison, the Smallcap 250 fell 26.1% from 23 Sep 2024. The stress window in `config/strategy.yaml` starts at the Smallcap 250 peak and is labelled −25%. Which index to use for it is the group's call.
- **Deflated Sharpe ratio.** The code reports it as a probability (Bailey and López de Prado 2014): the chance that the true Sharpe ratio is above the best one luck alone would give after the trials tried. It is not an adjusted Sharpe ratio, so slides 13 and 14 now say so.
- **Universe filters.** The "traded on at least 9 days in 10" filter was added to the list on slide 4.

## Filling in the results (after the one-time test run only)

```bash
python run.py freeze
```
```bash
python run.py test --strategy config/frozen_strategy.yaml
```
```bash
python site/build_site_data.py
```

The script reads `config/frozen_strategy.yaml`, `results/research/` and `results/test/`. It writes `results`, `meta.generated` (the test run's date) and `config.trials` into `site/data.js`, and leaves every other line as it is. It copies figures without rounding them. The only values it calculates come from the same run's daily files:
- the month-end points of the growth-of-₹1 lines;
- the yearly return before and after 1 Jan 2020, which tests the retail-boom prediction.

It refuses, and writes nothing, if any of these is true:
- the test period has not been run with the frozen parameters;
- `config/frozen_strategy.yaml` was edited after freezing;
- `results/test` was made with other parameters, an example config or synthetic data;
- the test-access log shows the test period was also run with other parameters. To publish anyway, change the "single frozen test run" wording in `site/app.js`, disclose the other runs, and pass `--accept-other-test-runs`.

The test pack must come from the current code, which also saves `results/test/logs/benchmarks.csv`.

## Publishing

1. Merge to `main`.
2. In the repository's **Settings → Pages**, set the source to **GitHub Actions**. The workflow then publishes `site/` on each push that changes it, or when run by hand from the Actions tab.
3. Once the repository is public, set `meta.repoUrl` in `site/data.js`.

GitHub Pages from a private repository needs a paid plan, so the repository may have to be public. **Check with the course instructor before publishing the strategy or its results before grading.**
