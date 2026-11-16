# Momentum on Indian mid-, small- and micro-cap stocks

Term Project 1, MBA elective *Algorithmic Trading Strategies*. The brief is in [`Term_Project_Algorithmic_Trading_Strategies.pdf`](Term_Project_Algorithmic_Trading_Strategies.pdf).

This repository contains a point-in-time back-testing framework for momentum strategies on NSE stocks, built to the brief's realism rules, and the working documents for the report. It runs from raw exchange files to a pack of tables and charts.

**It does not contain a strategy, results or conclusions.** Those are the group's to produce: see [Who does what](#who-does-what).

## Who does what

The course's AI policy allows AI to write and debug code, find and summarise papers and design charts, with disclosure. It does not allow AI-generated analysis, results or recommendations to be submitted as the group's own, or AI to fabricate data or results. It also requires each group to design its own strategy and parameters.

| Built with an AI assistant (declared in `docs/07_ai_use_declaration.md`) | Left for the group |
|---|---|
| All code in `src/momo`, tests, notebook | The strategy and every parameter: `config/strategy.yaml` |
| Dated statutory-charge schedule, with sources | The reasons for each choice: `docs/03_design_decisions.md` |
| Literature digest with verification status | Checking every citation against the original |
| Charts and the report pack | Running the pipeline on real data: the results |
| Skeletons of the risk policy and the report | The risk policy, the analysis, the limitations assessment, the recommendation |
| Synthetic data for testing the code | Verification (`docs/10_verification_checklist.md`) |

Two safeguards are built in: the pipeline refuses to run while a design field is blank, and anything produced from synthetic test data or from the example configuration is stamped so it cannot be mistaken for a result.

## Quick start

```bash
pip install -r requirements.txt
```

```bash
python -m pytest
```

Try the whole pipeline on invented data first (outputs are stamped "synthetic"):

```bash
python run.py fixture
```

```bash
python run.py train --data-config data/_synthetic_fixture/data.yaml --strategy tests/fixtures/fixture_strategy.yaml --out results/_fixture
```

Then with real data:

| Step | Command | What you do |
|---|---|---|
| 1 | - | Read `docs/01_data_requirements.md`; set `download_start` in `config/data.yaml` |
| 2 | `python run.py download` | Run on an Indian home or campus network |
| 3 | `python run.py build` | Read the data-quality report in `data/processed/quality/` |
| 4 | - | Put `risk_free.csv` (and any other files you have) in `data/external/` |
| 5 | `python run.py sizecheck`, `python run.py liquidity`, `python run.py episodes` | Look at the facts that inform your choices |
| 6 | - | Copy `config/strategy.template.yaml` to `config/strategy.yaml`; fill it in; record why in `docs/03_design_decisions.md` |
| 7 | `python run.py check` | Lists anything still undecided |
| 8 | `python run.py train` | Training-period pack in `results/train/` |
| 9 | `python run.py search` | Compare the alternatives in your grid; every run is logged |
| 10 | `python run.py freeze` | Lock the parameters |
| 11 | `python run.py test --strategy config/frozen_strategy.yaml` | The untouched test period, once |
| 12 | `python run.py full --strategy config/frozen_strategy.yaml` | Full-sample run for the stress tests |
| 13 | - | Write the risk policy and the report from your own results |

The notebook `notebooks/momentum_end_to_end.ipynb` runs the same steps with every intermediate object visible. `config/examples/smoke_test.yaml` is a placeholder configuration for checking that the code runs on your data; its outputs are stamped as an example.

## What the framework covers

| Requirement in the brief | Where |
|---|---|
| Point-in-time information only | Universe, signal and limits use data up to the decision date; a test deletes future data and requires identical history (`tests/test_backtest.py`) |
| No survivorship bias | Panel built from every daily bhavcopy; delisted names kept; write-off rule for holdings that stop trading (`data/panel.py`, `backtest.py`) |
| Transaction costs, gross and net | Dated STT, stamp duty, exchange, SEBI and GST schedule; brokerage; estimated spread; square-root impact (`costs.py`) |
| Liquidity limits and blocked signals | Liquidity filter, participation cap, multi-day fills, blocked-signal tables (`universe.py`, `backtest.py`, `analytics.py`) |
| Indian market frictions | Circuit locks block fills; trade-for-trade and surveillance exclusions; long-only cash equities (`frictions.py`, `universe.py`) |
| Out-of-sample discipline | Training-only search with a trial log, freeze, logged test-period access, deflated Sharpe ratio (`research.py`) |
| Trading signals | Return, risk-adjusted, residual and 52-week-high momentum; skip month; screens (`signals.py`) |
| Portfolio construction | Exit buffer, four weighting schemes, stock and sector caps, overlapping tranches (`portfolio.py`) |
| Risk management | VaR and ES limits, volatility target, drawdown tiers, stop-loss, regime switch, beta cap, limit monitor (`risk.py`, `stress.py`) |
| Stress tests | Named windows, worst periods, replay of the current book, market shock, liquidity stress (`stress.py`) |
| Post-trade analysis | Performance, hit rate, turnover, costs, capacity, factor exposures, by year and regime, attribution (`analytics.py`, `factors.py`) |

How each is implemented: `docs/04_methodology.md`.

## Repository map

```
config/
  data.yaml                   where files live, what to download
  statutory_charges.yaml      dated statutory charges, each with a source
  strategy.template.yaml      the group's design - every choice blank
  examples/smoke_test.yaml    placeholder config (outputs are stamped)
data/
  raw/ interim/ processed/    exchange files and the built panel (git-ignored)
  external/                   small files you supply (risk-free rate, ...)
docs/
  01_data_requirements.md     what to supply and in what layout
  02_literature_review.md     reading guide with verification status
  03_design_decisions.md      the choices, the evidence, and blanks for your reasons
  04_methodology.md           how each rule is implemented; known limitations
  05_risk_policy_template.md  skeleton of the written risk policy
  06_report_template.md       skeleton of the report, exhibit by exhibit
  07_ai_use_declaration.md    draft of the required declaration
  08_code_walkthrough.md      what each file does; self-test questions
  09_ic_qa_prep.md            questions to prepare for the committee
  10_verification_checklist.md
notebooks/momentum_end_to_end.ipynb
src/momo/                     the package (see docs/08_code_walkthrough.md)
tests/                        automated tests, on synthetic data
tools/build_notebook.py       regenerates the notebook
run.py                        command-line entry point
```

## Data sources

| Data | Source |
|---|---|
| Daily prices, volumes, ISINs | NSE cash-market bhavcopy, `nsearchives.nseindia.com` |
| Corporate actions, price-band hits, market capitalisation (from 2024) | NSE daily "PR" archive |
| Market capitalisation, 2019-2024 | NSE "market capitalisation - all companies" lists |
| Nifty 50 / Next 50 members and weights (from 2008) | NSE monthly index files |
| Index levels (from 2012) | NSE all-index closing snapshot |
| Symbol changes, listed securities, current index members (sector labels) | NSE archive static files |
| Risk-free rate | RBI Database on Indian Economy (supplied by the group) |
| Factor returns (optional) | IIM Ahmedabad Indian Fama-French-momentum data library (supplied by the group) |
| Total-return benchmarks (optional) | NSE Indices (supplied by the group) |

URL patterns were cross-checked against the open-source `nse` and `jugaad-data` Python packages.

## Status

- The automated tests pass on synthetic data written in the real NSE file layouts (including every naming and layout variant met in the real archive).
- **The data layer has been run on the full real archive, January 2010 to November 2026**: 3,267 companies, 6.9 million stock-days, one unparseable (empty) file. Rebuilding the Nifty 50 and Nifty Next 50 from their members with the adjusted returns tracks the published indices with correlation above 0.999. What the real data showed - notably that the exchange publishes market caps daily only from 2024 - is in `docs/01_data_requirements.md`.
- No strategy has been run on real data: that needs your `config/strategy.yaml`.

## Licence and disclaimer

MIT licence (see `LICENSE`). Course work for teaching purposes; nothing here is investment advice. Back-tested performance does not guarantee future results.
