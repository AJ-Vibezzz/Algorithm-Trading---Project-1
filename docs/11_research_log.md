# Research log - TEMPLATE  (yours to fill in, one row per training-period run)

The brief asks how many parameter combinations you tried and how you avoided
fitting the test period. This log is the evidence. Write the **question and
the basis before the run**, the outcome after. `results/research/trials.csv`
is the machine record of the same runs (the fingerprint links the two).

## 0. Plan, written before the first comparison
- Questions we will test on the training period, and why each matters:
  1. ______________________
  2. ______________________
- Maximum number of rounds / parameter sets we will try: ______
- How we will choose (metric, and what counts as a real difference): ______
- Robustness checks we will report but not choose on: ______

## 1. Runs

| # | Date | Fingerprint | Question this run answers | Basis (paper, data fact, or mandate) | What changed | Expected effect | Tables looked at | Outcome | Decision |
|---|------|-------------|---------------------------|--------------------------------------|--------------|-----------------|------------------|---------|----------|
| 1 | | | | | | | | | |
| 2 | | | | | | | | | |

A valid basis is a published finding, a fact about the data (from `sizecheck`,
`liquidity`, `episodes`, the quality report) or a constraint of the mandate
(fund size, participation cap, costs). "The Sharpe ratio was higher" is an
outcome, not a basis.

## 2. Freeze
- Date frozen: 20 Nov 2026  Fingerprint: 9f5311bc21b1  Parameter sets tried: 12 (8 earlier design on the group's machine, 4 final grid run by Claude Code at the group's request)
- Why this configuration (in our own words): ______

## 3. Test period
- Date run: 20 Nov 2026 (by Claude Code, at the group's request)  Times the test period was opened (`test_access_log.csv`): 2, both with the frozen fingerprint: the test run, then the full-sample run

Note: runs are counted in `<out>/research/trials.csv`, where `<out>` is the
`--out` folder (default `results`). Keep every training-period run on the
same `--out`, or add the counts from each folder.
