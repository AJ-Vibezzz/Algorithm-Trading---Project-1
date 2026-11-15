# Verification checklist

The course policy makes you responsible for every number, citation and line of code. Work through this list and tick each item; what you did here is what goes in the fourth column of the AI-use declaration.

## A. Before any real run

- [ ] `pip install -r requirements.txt` succeeded.
- [ ] `python -m pytest` passes on your machine (it builds a synthetic fixture on first run; allow a few minutes).
- [ ] You have read `docs/04_methodology.md` and can describe each rule in your own words.
- [ ] You have read `docs/08_code_walkthrough.md` and both of you can answer its questions.

## B. Data  (after `python run.py download` and `python run.py build`)

- [ ] The download summary shows no `blocked` or `error` rows (re-run if it does).
- [ ] `data/interim/_parse_errors.csv` is empty, or you understand every line in it.
- [ ] `quality/by_year.csv`: trading days per year are about 245-250; the number of companies is plausible; `share_rows_with_shares_outstanding` is near 1 for the years you use.
- [ ] `quality/calendar_gaps.csv`: every weekday without data is a known market holiday.
- [ ] `quality/exits.csv`: companies that stopped trading are present. Pick three you know (a merger, an insolvency, a voluntary delisting) and confirm their last dates.
- [ ] `adjustment_log.csv`: at least twelve split / bonus / rights rows checked against an exchange notice or an independent adjusted-price series. Record which ones: __________
- [ ] `quality/unexplained_jumps.csv`: each row reviewed; real errors corrected through `data/external/return_overrides.csv`.
- [ ] `quality/unrecognised_purposes.csv`: nothing in it is a split, bonus, rights issue or dividend that was missed.
- [ ] One stock's total-return index compared with an independent source over several years.
- [ ] Month-end market caps of five stocks compared with an independent source.
- [ ] Benchmark index levels compared with the NSE Indices website for three dates.
- [ ] `data/external/risk_free.csv` matches the RBI DBIE series you downloaded (three dates checked).
- [ ] `python run.py check` shows the supplied files you expect.

## C. Statutory charges  (`config/statutory_charges.yaml`)

For each line, open the cited source - the primary circular where you can - and confirm the rate and the effective date. Then change `checked:` to a short note of what you verified.

- [ ] STT on delivery trades (two entries; one is `checked: no`)
- [ ] Stamp duty (two entries; the pre-July-2020 entry is an assumption about one State - confirm or replace it)
- [ ] NSE transaction charge (five entries; the earliest is `checked: no`)
- [ ] SEBI turnover fee (five entries)
- [ ] Investor-protection-fund levy (two entries)
- [ ] Service tax / GST (six entries; two are `checked: no`)
- [ ] Date from which GST applies to the SEBI fee (`checked: no`)
- [ ] `08_cost_per_side_schedule.csv` agrees with a hand calculation or a broker's contract note for one date.

## D. Configuration

- [ ] Every value in `config/strategy.yaml` was chosen by the group, and `docs/03_design_decisions.md` records why.
- [ ] `meta.is_example` is `false`.
- [ ] Your brokerage assumption has a source.
- [ ] Your stress-window dates come from `python run.py episodes` or another source you can cite.
- [ ] `python run.py check` reports no missing fields and no problems.

## E. Back-test

- [ ] One decision date reproduced by hand: a stock's signal value recomputed from the panel; the selected names are the top-ranked eligible ones; the weights respect your caps (notebook sections 4-6).
- [ ] One fill reproduced by hand from `results/train/logs/trades.csv`: the price lies within that day's low-high range in the bhavcopy; the value is within `participation_cap x ADV`; the cost matches the schedule plus spread and impact.
- [ ] No trade is dated on or before its decision date (`trades.csv`: `date` > `decision_date`).
- [ ] `logs/daily.csv`: cash never negative; exposure never above 1.
- [ ] The CAGR in `01_performance_summary.csv` recomputed from the first and last NAV in `logs/daily.csv`.
- [ ] Maximum drawdown and its dates confirmed from `logs/daily.csv`.
- [ ] `11_pnl_bridge.csv`: the components sum to the change in NAV.

## F. Out-of-sample discipline

- [ ] `results/research/trials.csv` contains every comparison you made; the count you report is its number of distinct fingerprints.
- [ ] `results/research/frozen.json` exists and its fingerprint matches the configuration used for the test run.
- [ ] `results/research/test_access_log.csv` shows how many times the test period was opened; the report states this number.
- [ ] Training and test results appear separately in the report.

## G. Charts and tables in the report

- [ ] No chart carries a "SYNTHETIC" or "EXAMPLE CONFIGURATION" stamp.
- [ ] Each chart's values spot-checked against the CSV with the same number.
- [ ] Each table in the report traced to a file in `results/` and to the `run_manifest.json` of that run.
- [ ] Gross and net results are both shown.

## H. Citations

- [ ] Every paper cited has been opened. Authors, year, journal, volume and pages confirmed.
- [ ] The finding you attribute to each paper is in the paper.
- [ ] The three references marked `!` in the literature digest are resolved or not cited.
- [ ] Datasets and code libraries are cited (list in `06_report_template.md`).

## I. AI-use declaration

- [ ] Every AI use is listed, including any use after this repository was built (for example language editing).
- [ ] The "How you checked the output" column describes checks you actually did.
- [ ] The statements under "What the AI was not used for" are true as submitted.
- [ ] The declaration is the last appendix of the report.
