# Data: what you must supply, and how

The pipeline needs two kinds of input.

1. **Public exchange files** - downloaded by `python run.py download`. Too large to upload by hand.
2. **A few small files only you can obtain** - placed under `data/external/`. Layouts are given below.

`python run.py check` shows at any time which of the second kind are present.

---

## 1. Public exchange files (downloaded for you)

| Dataset key | File on the NSE archive host | Used for |
|---|---|---|
| `cm_bhav` | Cash-market bhavcopy (legacy layout before 8 July 2024, UDiFF layout from then) | prices, volume, traded value, previous close, ISIN |
| `pr` | Daily "PR" zip: corporate actions (`Bc*`), market cap (`MCAP*`), price-band hits (`Bh*`) | adjustments, point-in-time market cap, circuit locks |
| `ind_close` | All-index closing snapshot | benchmark index levels |
| static | Symbol-change list; current index member lists | company identity; sector labels |
| `fo_bhav` (optional) | Derivatives bhavcopy | which stocks had futures on a date |
| `sec_list` (optional) | Security-wise price bands | cross-check of bands |

### How to run the download

```bash
python run.py download --start 2012-01-01
```

(The date is an example. Set `download_start` in `config/data.yaml` once you have chosen your sample; it must be at least one full signal look-back before your training period starts.)

- **Where to run it.** The NSE archive refuses many cloud and foreign networks. Run it on your own laptop on an Indian home or campus connection. The command reports `blocked` if the network is refused.
- **How long.** Two to three files per trading day; allow an hour or more for ten years. It is resumable: stop and restart freely.
- **Size.** Roughly 0.1-0.2 GB per year of raw files, under `data/raw/` (git-ignored).
- **Then:** `python run.py build` parses everything, builds the panel and writes the data-quality report under `data/processed/quality/`. Read it.

### If you want the panel in the repository
`data/raw`, `data/interim` and `data/processed` are git-ignored on purpose (size, and so that no machine-made output is committed by accident). The instructor can reproduce the panel with the two commands above. If you prefer to share the built panel, zip `data/processed/panel` and store it outside git, or remove that line from `.gitignore` deliberately.

### What to check first on real data
The parsers were written from the documented layouts and tested on synthetic files in those layouts; they have **not yet been run on real NSE files**. After the first build:

1. `data/interim/_parse_errors.csv` should be empty or nearly so. If a whole dataset fails, the layout differs from what the parser expects - note the file name and the error.
2. `quality/by_year.csv`: are there about 245-250 trading days a year, and a plausible number of companies?
3. `quality/by_year.csv`, column `share_rows_with_shares_outstanding`: if it is near zero in early years, the market-cap file is missing there, and the universe will fall back to a traded-value size proxy for those years.
4. `adjustment_log.csv`: pick a dozen splits and bonuses you can verify elsewhere and confirm the adjusted return is sensible.
5. `quality/unexplained_jumps.csv` and `quality/unrecognised_purposes.csv`: review them.

---

## 2. Files you supply  (`data/external/`)

All are plain CSV with a header row. Column names are matched loosely (case and punctuation do not matter). Dates as `YYYY-MM-DD` or `DD-MM-YYYY`.

### 2.1 `risk_free.csv` - REQUIRED

The brief names RBI's Database on Indian Economy (DBIE) as the source. Use the 91-day Treasury bill yield.

| column | example | meaning |
|---|---|---|
| `date` | 2020-03-25 | auction or observation date |
| `yield_pct` | 4.36 | annualised yield, in percent (decimals are accepted too) |

Weekly data is fine: each day uses the latest yield published on or before it. Without this file the risk-free rate is taken as zero and the run prints a warning; Sharpe and Sortino ratios would then be overstated.

### 2.2 `factors/*.csv` - recommended

For the factor-exposure regression. The IIM Ahmedabad Indian Fama-French-momentum data library (Agarwalla, Jacob & Varma) publishes daily and monthly factors.

| column | meaning |
|---|---|
| `date` | day (daily file) or month-end (monthly file) |
| `mkt_rf` (also accepted: `mf`, `rm_rf`) | market minus risk-free |
| `smb`, `hml`, `wml` (also `mom`, `umd`) | size, value, momentum |
| `rf` | risk-free return |

Percent or decimal; frequency is detected from the date spacing. If absent, the pipeline builds market, size and momentum proxies from the panel and has no value factor unless the exchange's value index is in your data. Say in the report which source you used.

### 2.3 `benchmarks/*.csv` - recommended

Total-return index history, one file per index, as downloadable from the NSE Indices website.

| column | meaning |
|---|---|
| `date` | day |
| `total_returns_index` (also `tri`, `close`) | index level |
| `index_name` (optional) | otherwise the file name is used |

A series loaded here is available as a benchmark under its name with ` TRI` appended, e.g. `NIFTY SMALLCAP 250 TRI`.

### 2.4 `index_constituents/*.csv` - only if `universe.source: index_constituents`

Historical membership with dates. A list of *today's* members is not acceptable: that is the look-ahead the brief forbids, and the loader rejects a file without start dates.

| column | meaning |
|---|---|
| `index_name` | e.g. NIFTY SMALLCAP 250 |
| `symbol` and/or `isin` | the stock |
| `start` | first day as a member |
| `end` | last day as a member; blank if still a member |

Possible sources: NSE Indices (reconstitution press releases, or purchased history), CMIE Prowess, Bloomberg or ACE Equity through the library. If you cannot get it, use `source: mcap_rank`, which rebuilds the segments from the exchange's own market-cap files, and describe that in the report.

### 2.5 `sector_map.csv` - recommended

| column | meaning |
|---|---|
| `symbol` and/or `isin` | the stock |
| `sector` | your sector label |

Without it, the industry labels in NSE's current index member lists are used, and companies not in those lists (including most delisted ones) fall into `UNKNOWN`. CMIE Prowess covers delisted companies.

### 2.6 `surveillance.csv` - optional

ASM / GSM history.

| column | meaning |
|---|---|
| `symbol` | the stock |
| `list` | e.g. ASM-LT, ASM-ST, GSM |
| `start`, `end` | dates on the list; blank end = still listed |

Without it, exclusion of the trade-for-trade series (BE/BZ) stands in for surveillance status.

### 2.7 `delistings.csv` - optional

| column | meaning |
|---|---|
| `symbol` | last symbol of the company |
| `terminal_return` | what a holder received relative to the last traded price: 0 for a merger at fair value, -1 for a wipe-out |
| `reason` | free text |

Overrides the blanket `risk.delisting_return` for those stocks.

### 2.8 `return_overrides.csv` - optional

Hand corrections for data errors you have verified.

| column | meaning |
|---|---|
| `symbol`, `date` | the stock-day |
| `ret_total` | corrected total return for that day |
| `reason` | what you checked it against |

Rebuild the panel after editing.

---

## 3. Other things to add to the repository

| Item | Where | Why |
|---|---|---|
| Your strategy | `config/strategy.yaml` (copy of the template, filled in) | required to run anything |
| Your reasons | `docs/03_design_decisions.md` | the design rationale |
| Lecture slides or notes on momentum | `docs/course/` | so your terminology and the "classical strategy taught in class" match the course |
| Group names, submission and presentation dates, any cost or benchmark the instructor specified | report front matter | for the report |
