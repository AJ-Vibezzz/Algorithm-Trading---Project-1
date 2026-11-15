# AI use declaration - draft for the last appendix of the report

The course policy: every use of AI must be disclosed; undisclosed use is treated as academic misconduct; you are responsible for every number, citation and line of code; AI-suggested papers must be checked against the original; and either member may be asked to explain any part of the work.

This file records, accurately, what the AI assistant did in building this repository. **The first three columns are filled in. The fourth - "How you checked the output" - is blank because only you can fill it in, truthfully, after you have actually done the checking.** Suggested checks follow the table. Add a row for any further AI use (for example, language editing of the report), and delete nothing that is true.

## Declaration table (course template)

| AI tool | What it was used for | Where in the work (section / code file) | How you checked the output |
|---|---|---|---|
| Claude Code (Anthropic's AI coding assistant). Model and version: ____________ | Writing the back-testing code: data download and parsing, corporate-action adjustment, security identity, universe, signals, portfolio construction, cost model, back-test engine, risk, stress tests, analytics, factor regressions, research workflow | `src/momo/` (all modules), `run.py` | |
| same | Writing the notebook and the script that generates it | `notebooks/momentum_end_to_end.ipynb`, `tools/build_notebook.py` | |
| same | Writing automated tests and a synthetic data generator used only to test the code | `tests/`, `src/momo/data/synthetic.py` | |
| same | Designing the charts | `src/momo/plots.py`; every figure in the report pack | |
| same | Finding papers and summarising them | `docs/02_literature_review.md`; references in `docs/03_design_decisions.md` | |
| same | Looking up Indian statutory charges and their history | `config/statutory_charges.yaml` | |
| same | Drafting the structure of the design sheet, risk policy, report and Q&A list (headings and questions; no conclusions) | `docs/03`, `05`, `06`, `09` | |
| same | Writing documentation of the code and data | `README.md`, `docs/01`, `04`, `08`, `10`, `config/strategy.template.yaml`, `config/data.yaml` | |
| same | An example configuration with placeholder values, to check that the code runs | `config/examples/smoke_test.yaml` | |
| | | | |
| | | | |

## What the AI was *not* used for

Keep each line only if it is true when you submit; otherwise move the item into the table above.

- Choosing the strategy, its parameters, the risk limits or the stress scenarios (`config/strategy.yaml`, `docs/03_design_decisions.md` and `docs/05_risk_policy_template.md` were left blank for the group).
- Producing the back-test results in the report (the group ran the pipeline on real data with its own configuration; the AI ran the code only on synthetic test data, whose outputs are stamped and not used).
- Writing the analysis, the limitations assessment or the recommendation in the report.
- Fabricating any data or result.

## Suggested checks (so the fourth column can be filled in honestly)

**Code**
1. Run the test suite (`python -m pytest`) and read what each test asserts. The names say what is being proved: for example the accounting identity, the liquidity cap, no fills at a circuit lock, and that deleting future data does not change past results.
2. Follow one rebalance by hand: in the notebook (sections 4-6), take one decision date, recompute one stock's 12-1 return from the panel in a spreadsheet, and confirm the weights respect your caps.
3. Follow one trade by hand: pick a row of `results/train/logs/trades.csv`, look up that stock's bhavcopy for the day, and confirm the price lies within the day's range, the value is within your participation cap and the cost matches `08_cost_per_side_schedule` plus spread and impact.
4. Read `docs/08_code_walkthrough.md` and make sure each of you can answer its questions without it.

**Data**
5. Spot-check a dozen entries of `data/processed/adjustment_log.csv` (splits, bonuses) against exchange notices or an adjusted-price source such as Yahoo Finance (the brief suggests it for cross-checks).
6. Compare a few month-end market caps and a benchmark index series with an independent source.
7. Review `quality/unexplained_jumps.csv` and `quality/exits.csv`.

**Statutory charges**
8. Open the source cited on each line of `config/statutory_charges.yaml`, prefer the primary circular, and change `checked:` to a note of what you verified. Six lines are marked `checked: no`.

**Papers**
9. For every paper you cite: open it, confirm authors, year, journal, volume and pages, and confirm the finding in the paper itself. The digest marks which details were web-checked (W), which are from the assistant's memory (M) and which showed a discrepancy (!). Three have known discrepancies and one recalled paper could not be found at all and was dropped.

**Charts**
10. For each chart you use, open the CSV of the same number in `tables/` and confirm a few plotted values.

## Record of AI sessions

| Date | What was done | Evidence |
|---|---|---|
| 14-15 November 2026 | Repository built by the AI assistant from the project brief: code, tests, notebook, documentation, literature digest. The assistant could not reach NSE or other data hosts from its environment, so no real market data was used. | Git history of this repository (commits carry a co-author line) |
| | | |
