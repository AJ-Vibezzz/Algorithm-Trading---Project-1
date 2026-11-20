# Group checklist - everything left for you to do, in order

The code, tests and documentation are built. Everything below is yours: the course's AI policy
requires the group to choose the strategy and parameters, produce the results and write the analysis.
Detailed checks for each step are in `docs/10_verification_checklist.md` (section letters in brackets).

## 0. Set up (both members)

- [ ] `pip install -r requirements.txt`, then `python -m pytest` passes on your machine.
- [ ] Re-read the AI-use rules in the brief.
- [ ] Both read `docs/04_methodology.md` and `docs/08_code_walkthrough.md` and can answer its self-test questions.
- [ ] Optional: put your lecture slides or notes on momentum in `docs/course/`.

## 1. Get the data (on your own laptop, Indian network)

- [ ] Set `download_start` in `config/data.yaml`. The public archive starts in 2010; index levels start in February 2012.
- [ ] `python run.py download` - about 1.5 hours and 2.5 GB for 2010-2026; resumable.
- [ ] `python run.py build` - needs about 8 GB of memory for the full history.
- [ ] Read the data-quality report in `data/processed/quality/`, especially `index_replication.csv`, `adjustments_to_check.csv` and `unexplained_jumps.csv` [B].
- [ ] **Required:** `data/external/risk_free.csv` - 91-day T-bill yield from RBI DBIE (layout in `docs/01_data_requirements.md`, section 2.1).
- [ ] Recommended: `sector_map.csv` (about 2,500 of 3,267 companies have no sector label otherwise), total-return benchmarks from NSE Indices, IIMA factor data.
- [ ] Optional: ASM/GSM history, delisting outcomes, AMFI half-yearly market-cap lists (only if you want `amfi_list` as a size measure).
- [ ] Correct any verified data errors in `data/external/return_overrides.csv` and rebuild.

## 2. Look at the facts before deciding

- [ ] `python run.py sizecheck` - which size measure exists in which years, and how far they agree.
- [ ] `python run.py liquidity` - how liquid each size segment is, year by year.
- [ ] `python run.py episodes --index "NIFTY SMALLCAP 250"` (or another index) - dates of the big falls, for stress windows.

## 3. Design the strategy (decisions D1-D14 in `docs/03_design_decisions.md`)

- [ ] Copy `config/strategy.template.yaml` to `config/strategy.yaml` and fill every `TODO`.
- [ ] Record the reason for each choice in `docs/03_design_decisions.md`.
- [ ] Decisions that need care on this data:
  - [ ] Sample period and train/test split (D1). Market cap exists only from April 2019; earlier years can only be ranked by traded value.
  - [ ] Size measure and its order of preference (`universe.size_metrics`, D4), and whether to fix the top 100 with `large_cap_indices`.
  - [ ] Fund size (D2): it drives liquidity caps, impact and capacity.
  - [ ] Brokerage assumption, with a source (D10).
  - [ ] Risk limits: VaR / ES, drawdown tiers, delisting return (D11-D12).
  - [ ] Stress windows from `episodes` or a source you can cite (D13).
  - [ ] The parameter grid to compare on the training data (D14).
- [ ] Verify the 6 unverified statutory-charge entries in `config/statutory_charges.yaml` [C].
- [ ] `python run.py check` reports nothing missing [D].

## 4. Run - training first, test once

- [ ] `python run.py train` - read the training pack in `results/train/`. Every run of your own config is
      logged in `results/research/trials.csv` (re-running unchanged settings is not counted twice).
- [ ] `python run.py search` - compare your alternatives on the training period only; every run is logged.
- [ ] Keep `docs/11_research_log.md`: the question and its basis before each run, the outcome after.
- [ ] Choose, then `python run.py freeze`.
- [ ] `python run.py test --strategy config/frozen_strategy.yaml` - **once**.
- [ ] `python run.py full --strategy config/frozen_strategy.yaml` - for the stress tests.
- [ ] Verify the results by hand [E], and check the out-of-sample record: trial count, freeze, test-access log [F].
- [ ] `python site/build_site_data.py` - copies the test run's figures into the website (`docs/12_site.md`), then check the Results slide against `results/test/tables`.

## 5. Write

- [ ] Risk-management policy from `docs/05_risk_policy_template.md`: limit values, actions on breach, who decides what.
- [ ] Report, at most 15 pages plus appendices, following `docs/06_report_template.md`:
  - [ ] gross and net results;
  - [ ] training and test shown separately;
  - [ ] number of parameter combinations tried;
  - [ ] how the historical universe was built, including the size measure in each period;
  - [ ] how many signals the liquidity limits blocked;
  - [ ] limitations;
  - [ ] recommendation.
- [ ] Every chart and table traced to `results/` and checked; none carries a SYNTHETIC or EXAMPLE stamp [G].
- [ ] Every citation opened and checked; resolve or drop the three references marked `!` in `docs/02_literature_review.md` [H].
- [ ] AI-use declaration (`docs/07_ai_use_declaration.md`):
  - [ ] fill in the model and version;
  - [ ] write the "How you checked the output" column from the checks you actually did;
  - [ ] make it the last appendix [I].

## 6. Present

- [ ] 12-minute deck plus 8 minutes of Q&A; both members present.
- [ ] Rehearse the questions in `docs/09_ic_qa_prep.md`.
- [ ] Website: ask the instructor before publishing it; then Settings -> Pages -> source "GitHub Actions", and set `meta.repoUrl` in `site/data.js` once the repository is public.
- [ ] Add the website's row to the AI-use declaration's tool column (who built it, with which tool).

## 7. Repository housekeeping

- [ ] Tell Claude whether to replace the branch history (removes about 170 MB of test files committed by mistake; needs a force-push) or squash-merge the branch instead.
- [ ] Add group names and submission and presentation dates to the report front matter.
- [ ] Decide whether to share the built panel; it is git-ignored and can be rebuilt with `download` + `build`.
