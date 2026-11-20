"""Writes notebooks/momentum_end_to_end.ipynb (without outputs).

The notebook is generated from this script so that its source stays easy to
review and diff. Re-run after editing:   python tools/build_notebook.py
"""

from pathlib import Path

import nbformat as nbf

REPO = Path(__file__).resolve().parents[1]
cells = []


def md(text: str) -> None:
    cells.append(nbf.v4.new_markdown_cell(text.strip("\n")))


def code(text: str) -> None:
    cells.append(nbf.v4.new_code_cell(text.strip("\n")))


md("""
# Momentum on Indian mid-, small- and micro-caps: end-to-end notebook

**MBA elective, Algorithmic Trading Strategies - Term Project 1**

This notebook runs the whole project from raw exchange files to the report exhibits:

| Step | What happens | Section |
|---|---|---|
| 1 | Download the public NSE files | 1 |
| 2 | Build the survivorship-free panel and read the data-quality report | 2 |
| 3 | Load **your** strategy configuration | 3 |
| 4 | Point-in-time universe, signal and portfolio on one date (so you can see every step) | 4-6 |
| 5 | Cost schedule | 7 |
| 6 | Back-test the **training** period: paper, gross, net | 8 |
| 7 | Parameter comparison on the training period (every run is logged) | 9 |
| 8 | Freeze the parameters, then run the untouched **test** period | 10-11 |
| 9 | Full-sample run for the stress tests | 12 |

**What this notebook does not do.** It computes tables and charts. It does not interpret them, choose
parameters or make a recommendation: under the course's AI policy those are your work. Cells marked
**YOUR NOTES** are where your reasoning goes; write it in your own words.

**AI-use disclosure.** The code in `src/momo` and this notebook were written with an AI assistant.
That use must be declared in the last appendix of your report - see `docs/07_ai_use_declaration.md`.
""")

md("## 0. Setup")
code("""
import os, sys
from pathlib import Path

# Run from the repository root whether the notebook is opened from the root or from notebooks/
REPO = Path.cwd() if (Path.cwd() / "src").exists() else Path.cwd().parent
os.chdir(REPO)
sys.path.insert(0, str(REPO / "src"))

import numpy as np
import pandas as pd
from IPython.display import Image, Markdown, display

pd.set_option("display.width", 200, "display.max_columns", 40, "display.float_format", "{:,.4f}".format)

# ---- files this run uses (environment variables let the automated test point them at the fixture) ----
DATA_CONFIG = os.environ.get("MOMO_DATA_CONFIG", "config/data.yaml")
STRATEGY    = os.environ.get("MOMO_STRATEGY", "config/strategy.yaml")
STATUTORY   = "config/statutory_charges.yaml"
OUT         = Path(os.environ.get("MOMO_OUT", "results"))

# ---- switches: set to True when you want that step to run --------------------------------------------
RUN_DOWNLOAD = os.environ.get("MOMO_RUN_DOWNLOAD", "0") == "1"   # needs a network that can reach NSE; takes a while
REBUILD_PANEL = os.environ.get("MOMO_REBUILD_PANEL", "0") == "1"  # re-parse raw files even if a panel exists
RUN_SEARCH   = os.environ.get("MOMO_RUN_SEARCH", "0") == "1"     # parameter comparison on the training period
DO_FREEZE    = os.environ.get("MOMO_DO_FREEZE", "0") == "1"      # lock the parameters (do this once, deliberately)
RUN_TEST     = os.environ.get("MOMO_RUN_TEST", "0") == "1"       # opens the test period - logged
RUN_FULL     = os.environ.get("MOMO_RUN_FULL", "0") == "1"       # training + test in one run - logged

def show(path, width=760):
    display(Image(filename=str(path), width=width))
""")

md("""
## 1. Data sources and download

Every input is a public end-of-day file. Nothing is typed in by hand and nothing is taken from a
"current constituents" list for past dates.

| Data | Source | Used for |
|---|---|---|
| Cash-market bhavcopy (daily) | NSE archives, `nsearchives.nseindia.com` | prices, volume, traded value, previous close, ISIN |
| "PR" archive (daily) | NSE archives | corporate actions with ex-dates, shares outstanding / market cap, price-band hits |
| Index snapshot (daily) | NSE archives | benchmark index levels |
| Symbol-change list, index member lists | NSE archives | company identity through renames; sector labels |
| 91-day T-bill yield | RBI DBIE (you supply `data/external/risk_free.csv`) | risk-free rate |
| Factor returns (optional) | IIM Ahmedabad Indian Fama-French-momentum library (you supply) | factor exposures |

The download is resumable: files already on disk are skipped, and dates that returned "not found"
(weekends, holidays) are remembered. Run it from a network that can reach NSE.
""")
code("""
from momo.config import DataConfig
from momo.data.download import Downloader, summarise_manifest

data_cfg = DataConfig.load(DATA_CONFIG)
if RUN_DOWNLOAD:
    manifest = Downloader(data_cfg).run()        # uses download_start / download_end from config/data.yaml
    display(summarise_manifest(manifest))
else:
    print("Download skipped (RUN_DOWNLOAD is False). Raw files are expected under", data_cfg.raw)
""")

md("""
## 2. Build the panel and read the data-quality report

Raw files -> one row per stock per day with **adjusted total returns**. The build

* keeps ordinary equity shares only, and keeps companies that later delisted;
* gives each company one permanent id across renames and ISIN changes;
* handles splits, bonuses, rights and dividends, writing every adjustment to an audit log;
* attaches shares outstanding / market cap **as published that day**.

How each step works is described in `docs/04_methodology.md`.
""")
code("""
from momo.data.ingest import ingest
from momo.data.panel import build_panel
from momo.data.quality import data_quality_report

panel_exists = (data_cfg.processed / "securities.parquet").exists()
if REBUILD_PANEL or not panel_exists:
    print(ingest(data_cfg, force=REBUILD_PANEL))
    build_panel(data_cfg)
quality = data_quality_report(data_cfg)
""")
code("""
display(Markdown("**Coverage by year**"));  display(quality["by_year"].T)
display(Markdown("**Companies that stopped trading (first rows)** - these stay in the sample"))
display(quality["exits"].head(10))
display(Markdown("**Large one-day moves with no corporate action on record** - check each against the exchange notice"))
display(quality["unexplained_jumps"].head(15))
display(Markdown("**Corporate-action text the parser did not understand**"));  display(quality["unrecognised_purposes"].head(15))
""")
md("""
> **YOUR NOTES - data checks.** Which rows of the audit log did you verify by hand, and against what?
> How many companies left the sample, and does that look plausible? Which gaps remain?
""")

md("""
## 3. Load the workspace and your strategy

`config/strategy.yaml` is yours: copy `config/strategy.template.yaml`, fill in every `null`, and record
your reasons in `docs/03_design_decisions.md`. The cell below stops with a list if anything is still blank.
""")
code("""
from momo.config import StrategyConfig
from momo.pipeline import Workspace, run_backtest, run_variants, benchmark_returns

ws  = Workspace.load(DATA_CONFIG, STATUTORY)
cfg = StrategyConfig.load(STRATEGY)
cfg.validate()                      # raises, listing the blank fields, if the design is incomplete
md_ = ws.md
print("Strategy:", cfg.meta.strategy_name, "| fingerprint", cfg.fingerprint())
print("Training:", cfg.sample.train_start, "to", cfg.sample.train_end, "| Test:", cfg.sample.test_start, "to", cfg.sample.test_end)
""")

md("""
## 4. Point-in-time universe

On each rebalance date the universe is rebuilt from what was known that day: market-cap rank from the
exchange's file for that date, then the tradability filters in a fixed order. The table counts, per date,
how many stocks each filter removed.
""")
code("""
from momo.strategy import decision_rows, MomentumStrategy

rows = decision_rows(md_, cfg, cfg.sample.train_start, cfg.sample.train_end)
universe = ws.universe(cfg, rows)
uni_table = universe.table()
display(uni_table.iloc[np.linspace(0, len(uni_table) - 1, min(8, len(uni_table))).astype(int)])
""")

md("""
## 5. Signal on one decision date

To make the mechanics visible, take one rebalance date in the middle of the training period and show the
best-ranked stocks with the raw value behind each score.
""")
code("""
from momo.signals import compute_signal

t = rows[len(rows) // 2]
snap = universe[t]
sig = compute_signal(md_, t, cfg.signal, reference=snap.eligible, score_for=snap.in_segment)
table = pd.DataFrame({"symbol": md_.symbols, "segment": snap.segment, "eligible": snap.eligible,
                      "removed_by": snap.reason, "score": sig.score, **sig.raw,
                      "ann_volatility": sig.volatility, "sector": md_.sector})
print("Decision date:", md_.dates[t].date(), "| eligible:", int(snap.eligible.sum()), "| scoreable:", int(np.isfinite(sig.score[snap.eligible]).sum()))
display(table[np.isfinite(table["score"])].sort_values("score", ascending=False).head(15))
""")

md("## 6. Portfolio construction on that date")
code("""
decision = MomentumStrategy(md_, cfg, universe).decide(t)
w = pd.Series(decision.weights, index=md_.symbols)
book = pd.DataFrame({"weight": w[w > 0], "sector": pd.Series(md_.sector, index=md_.symbols)[w > 0]}).sort_values("weight", ascending=False)
display(book.head(15))
display(book.groupby("sector")["weight"].sum().sort_values(ascending=False).to_frame("sector weight"))
print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in decision.info.items() if k != "date"})
""")

md("""
## 7. Cost schedule

Statutory charges plus your brokerage assumption, per side, in basis points - one row per regime.
Check a row by hand against a contract note. Spread and market impact come on top and depend on the
stock and the order size.
""")
code("""
from momo.costs import CostModel, cost_table

cm = CostModel(ws.schedule, cfg.costs, md_.dates)
display(cost_table(cm, md_.dates[rows[0]:]))
print("Statutory entries not yet verified against a source:", ws.schedule.unverified() or "none")
""")

md("""
## 8. Training-period back-test

Three runs of the same strategy separate the layers of realism:

* **paper** - no costs, no liquidity limits, no circuit blocks;
* **gross** - liquidity limits and circuit blocks, but no costs;
* **net** - everything. This is the one to report.

The report pack (tables as CSV, charts as PNG) is written to `results/train/`.
""")
code("""
from momo.analytics import capacity_sweep
from momo.report import build_report_pack
from momo.research import TrialRegistry

registry = TrialRegistry(OUT / "research")
rf = pd.Series(md_.rf_daily, index=md_.dates)

def run_period(start, end, label):
    variants = run_variants(ws, cfg, start, end)
    capacity = capacity_sweep(ws, cfg, start, end, cfg.research.capacity_aum_inr, rf) if cfg.research.capacity_aum_inr else None
    return variants, build_report_pack(ws, cfg, variants, OUT / label, label, capacity, registry)

train_variants, train_pack = run_period(cfg.sample.train_start, cfg.sample.train_end, "train")
""")
code("""
display(train_pack["01_performance_summary"])
display(Markdown("**Paper -> gross -> net**"));  display(train_pack["06_paper_gross_net"])
show(OUT / "train" / "figures" / "01_equity_and_drawdown.png")
show(OUT / "train" / "figures" / "06_paper_gross_net.png")
""")
code("""
display(Markdown("**Signals blocked before portfolio construction**"));  display(train_pack["10_blocked_screening"])
display(Markdown("**Orders: how they ended**"));  display(train_pack["10_blocked_orders"])
display(Markdown("**Order-days that fell short, by cause**"));  display(train_pack["10_blocked_execution"])
display(Markdown("**Costs by component**"));  display(train_pack["08_costs_by_component"])
show(OUT / "train" / "figures" / "09_blocked_signals.png")
""")
md("""
> **YOUR NOTES - training period.** What do the paper / gross / net gaps tell you about this strategy?
> Which limits bind most often, and is that acceptable? (Write this yourself; do not paste AI text.)
""")

md("""
## 9. Parameter comparison - training period only

`research.grid` in your config lists the values you want to compare. Every run is appended to
`results/research/trials.csv`, so "how many combinations did you try" is read from a log.
The table ranks; it does not choose. Prefer a region where neighbouring settings perform alike.
""")
code("""
from momo.research import grid_search, sensitivity_table
from momo import plots

if RUN_SEARCH and cfg.research.grid:
    trials = grid_search(ws, cfg, registry)
    display(trials.drop(columns=["fingerprint"]))
    params = list(cfg.research.grid)
    metric = cfg.research.selection_metric or "sharpe"
    if len(params) >= 2:
        pivot = sensitivity_table(trials, params[0], params[1], metric)
        plots.save(plots.sensitivity_heatmap(pivot, metric, params[0], params[1]),
                   registry.folder / "sensitivity.png", md_.data_kind, cfg.meta.is_example)
        show(registry.folder / "sensitivity.png", width=520)
print("Parameter sets tried on the training period so far:", registry.n_parameter_sets())
""")
md("""
> **YOUR DECISION - parameters.** Which setting do you choose, and why that one rather than the
> top-ranked one? Update `config/strategy.yaml`, re-run section 8, then freeze.
""")

md("""
## 10. Freeze the parameters

Freezing writes `config/frozen_strategy.yaml` and records how many parameter sets were tried before it.
Do it once, deliberately, when the design is final.
""")
code("""
from momo.research import freeze

if DO_FREEZE:
    record = freeze(cfg, registry, OUT / "research" / "frozen_strategy.yaml")
    display(pd.Series(record))
else:
    print("Not frozen yet (DO_FREEZE is False). Frozen record on file:", registry.frozen())
""")

md("""
## 11. Test period - the untouched sample

Running this cell is logged in `results/research/test_access_log.csv`, with a note of whether the
configuration matched the frozen one. Report the test period separately from the training period.
""")
code("""
from momo.research import evaluate_test

if RUN_TEST:
    evaluate_test(ws, cfg, registry)       # logs the access and warns if the parameters were not frozen
    test_variants, test_pack = run_period(cfg.sample.test_start, cfg.sample.test_end, "test")
    display(test_pack["01_performance_summary"])
    display(Markdown("**Is the Sharpe ratio distinguishable from luck, given the number of trials?**"))
    display(test_pack["21_sharpe_significance"])
    show(OUT / "test" / "figures" / "01_equity_and_drawdown.png")
    display(registry.test_accesses())
else:
    print("Test period not opened (RUN_TEST is False).")
""")
md("""
> **YOUR NOTES - out of sample.** How does the test period compare with the training period, and what
> do you conclude about the strategy? What would make you change your mind?
""")

md("""
## 12. Full-sample run: stress tests, risk limits, attribution

One continuous run over training + test, so that every stress window you named is covered.
Because it includes the test period it is logged like section 11.
""")
code("""
if RUN_FULL:
    evaluate_test(ws, cfg, registry)
    full_variants, full_pack = run_period(cfg.sample.train_start, cfg.sample.test_end, "full")
    for name in ["18_stress_windows", "18_momentum_factor_worst_periods", "19_replay_latest_book",
                 "19_market_shocks", "19_liquidity_stress", "13_var_backtest_summary", "14_limit_breach_summary",
                 "05_regimes", "03_calendar_years", "11_attribution_by_sector", "12_factor_regression_monthly"]:
        if name in full_pack:
            display(Markdown(f"**{name}**"));  display(full_pack[name])
    for fig in ["16_stress_windows", "13_var_backtest", "05_regimes", "10_attribution_by_sector",
                "11_factor_exposures", "14_sector_weights", "15_liquidity_profile", "17_capacity"]:
        path = OUT / "full" / "figures" / f"{fig}.png"
        if path.exists():
            show(path)
else:
    print("Full-sample run skipped (RUN_FULL is False).")
""")
md("""
> **YOUR NOTES - stress tests and risk.** Which scenario hurts most and why? Were any limits breached,
> and what does your risk policy say should happen then? What is the capacity of the strategy?

## 13. Where everything is

* `results/<period>/tables/*.csv` - every exhibit as a table (the numbers behind each chart)
* `results/<period>/figures/*.png` - the charts
* `results/<period>/run_manifest.json` - exactly what was run (config, data, versions)
* `results/research/` - the trial log, the frozen-config record and the test-access log
* `data/processed/quality/` and `data/processed/adjustment_log.csv` - the data evidence pack

Next: `docs/06_report_template.md` shows where each exhibit belongs in the report, and
`docs/05_risk_policy_template.md` is the skeleton of the written risk policy.
""")

nb = nbf.v4.new_notebook(cells=cells, metadata={
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
})
out = REPO / "notebooks" / "momentum_end_to_end.ipynb"
out.parent.mkdir(exist_ok=True)
nbf.write(nb, out)
print("wrote", out, "with", len(cells), "cells")
