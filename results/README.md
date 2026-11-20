# Results of the group's frozen strategy

These folders hold the back-test output for **Slow Diffusion Residual Momentum**, frozen as 30 holdings with quarterly review (fingerprint `9f5311bc21b1`, `config/frozen_strategy.yaml`). They contain computed tables, charts and logs only. What they show, and what to recommend, is for the group to write.

| Folder | Period | What it is |
|---|---|---|
| `research/` | training | `trials.csv`: every setting tried on the training period. The first 9 rows are the group's earlier design, run on the group's machine (8 distinct settings). The last 4 rows are the final 2 × 2 search. `grid_20261120_201443.csv` and `sensitivity_sharpe.png` are that search. `frozen.json` records the freeze. `test_access_log.csv` records every run that touched the test period |
| `train/` | 30 Mar 2012 – 30 Dec 2016 | report pack of the frozen settings on the training period |
| `test/` | 31 Mar 2017 – 30 Oct 2026 | **the one-time test run** of the frozen settings |
| `full/` | 30 Mar 2012 – 30 Oct 2026 | training and test in one run, for the stress windows of the training years |

Each pack has the following, as described in the main README, section 2.5:
- `tables/`: one CSV per exhibit;
- `figures/`: the charts;
- `logs/`: daily NAV, benchmark returns, orders, trades, positions;
- `run_manifest.json`: the config, data and versions used.

## How these runs were made

All runs were made on 20 Nov 2026 by Claude Code at the group's request, with the code in this repository at that date. The data was the NSE archive (4 Jan 2010 – 13 Nov 2026), rebuilt with that code. It has the same company count (3,267) and index-replication check (Nifty 50 correlation 0.9997) as the group's own build. The order was:
1. the training search (4 settings);
2. the group chose 30 holdings, quarterly review;
3. freeze (20:17:50);
4. test (20:17:54);
5. full sample (20:19:02);
6. the training pack.

Points to know when reading them:
- **The fund starts in cash.** With quarterly review, the first review in the test is 31 Mar 2017, so the test record starts there (39 reviews). Training has 19 reviews, 30 Mar 2012 – 30 Sep 2016.
- **Use the test pack's trial count.** `train/` was built in a separate folder so that re-running an already-logged setting would not be counted as a new trial. Its parameters are those of search trial `5f1bbf22d2b0`, and its net Sharpe ratio (0.2086) is identical to that trial's. Its `21_sharpe_significance.csv` therefore shows 1 trial. Use the one in `test/`, which counts all 12.
- **The 2013 and 2015–16 stress windows** fall outside the test period. Their rows in `test/tables/18_stress_windows.csv` are blank, and `full/` covers them.
- **The test period has been run twice, both with the frozen fingerprint.** `test_access_log.csv` shows the test run and then the full-sample run, which includes the test years.
