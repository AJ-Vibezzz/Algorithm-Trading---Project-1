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
| `pr` | Daily "PR" zip: corporate actions (`Bc*`), price-band hits (`Bh*`), and from January 2024 market cap (`MCAP*`) | adjustments, circuit locks, market cap |
| `ind_close` | All-index closing snapshot (from February 2012) | benchmark index levels |
| `mcwb` | Monthly Nifty 50 / Nifty Next 50 members and weights (from 2008) | dated large-cap list; index-replication check |
| `mcap_lists` | The exchange's "market capitalisation - all companies" lists (31 Mar 2019 to 28 Mar 2024) | market cap before the daily file |
| static | Symbol-change list; listed securities (`EQUITY_L`); current index member lists | company identity; face values; sector labels |
| `fo_bhav` (optional) | Derivatives bhavcopy | which stocks had futures on a date |
| `sec_list` (optional) | Security-wise price bands | cross-check of bands |

### How to run the download

```bash
python run.py download --start 2012-01-01
```

(The date is an example. Set `download_start` in `config/data.yaml` once you have chosen your sample; it must be at least one full signal look-back before your training period starts.)

- **Where to run it.** The NSE archive refuses some networks; an Indian home or campus connection works. The command reports `blocked` if the network is refused.
- **How long.** About 1.5 hours for 2010 to 2026 (three files per trading day). It is resumable: stop and restart freely.
- **Size.** About 2.5 GB of raw files for 2010-2026, under `data/raw/` (git-ignored). Building the panel needs about 8 GB of memory for the full history.
- **Spreadsheets.** The market-cap lists are `.xlsx` files; reading them needs `openpyxl` (in `requirements.txt`).
- **Then:** `python run.py build` parses everything, builds the panel and writes the data-quality report under `data/processed/quality/`. Read it.

### If you want the panel in the repository
`data/raw`, `data/interim` and `data/processed` are git-ignored on purpose (size, and so that no machine-made output is committed by accident). The instructor can reproduce the panel with the two commands above. If you prefer to share the built panel, zip `data/processed/panel` and store it outside git, or remove that line from `.gitignore` deliberately.

### What the real archive looks like (checked on the files for 2010-2026)
The pipeline has been run on the full public archive from January 2010 to November 2026 (4,185 trading days, 3,267 companies, 6.9 million stock-days). What that showed, and what it means for you:

- **Size information is patchy before 2024.** The daily market-cap file starts in January 2024. The exchange's market-cap lists cover March 2019 (ranks only) to March 2024. Before April 2019 there is no exchange measure of market cap. Choose `universe.size_metrics` with that in mind; `python run.py sizecheck` shows the coverage and how far the measures agree.
- **The previous-close field is not adjusted on ex-dates**, so returns depend on the corporate-action texts. The quality report checks them by rebuilding the Nifty 50 and Nifty Next 50 from their members: on this data the rebuilt series track the published ones with correlation above 0.999.
- **Index levels start in February 2012**, and the index names changed in 2013 and 2015 (`S&P CNX 500` -> `CNX 500` -> `NIFTY 500`); the series are joined automatically, listed in `data/interim/index_renames.csv`.
- **ISINs appear in the price file from 22 June 2011**; earlier rows are linked to companies by symbol.

### What to check after your own build
1. `data/interim/_parse_errors.csv` - one known entry: an empty corporate-action file the exchange published on 10 January 2022.
2. `quality/summary.json` and `quality/by_year.csv` - trading days, companies, and where the market cap came from in each year.
3. `quality/index_replication.csv` - correlation near 0.999 and a handful of days above 40 bp. Much worse means something in the adjustments went wrong.
4. `quality/adjustments_to_check.csv` - the adjustments that rest on a judgement (inferred ratios, very large dividends). Check them against the company's announcements.
5. `quality/unexplained_jumps.csv` - large moves with nothing to explain them. Most are real (Yes Bank, Jet Airways, DHFL); a few may be missed corporate actions. Correct those in `data/external/return_overrides.csv`.

---

## 2. Files you supply  (`data/external/`)

All are plain CSV with a header row. Column names are matched loosely (case and punctuation do not matter). Dates as `YYYY-MM-DD` or `DD-MM-YYYY`.

### 2.1 `risk_free.csv` - REQUIRED

The brief names RBI's Database on Indian Economy (DBIE) as the source. Use the 91-day Treasury bill yield.

DBIE is at https://data.rbi.org.in/#/dbie/home. If it does not load, the same auction yields are in RBI's *Handbook of Statistics on Indian Economy* (table on auctions of 91-day Treasury Bills, Excel download). In Excel keep the date and yield columns, rename them `date` and `yield_pct`, delete title and note rows, and save as CSV.

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

Possible sources: NSE Indices (reconstitution press releases, or purchased history), CMIE Prowess, Bloomberg or ACE Equity through the library. If you cannot get it, use `source: mcap_rank`, which rebuilds the segments from the size measures you choose, and describe that in the report.

### 2.4a `amfi/*.xlsx` - optional

AMFI's half-yearly lists of average market capitalisation (from the half-year to December 2017), downloadable from amfiindia.com. Put the files as published in `data/external/amfi/`; the half-year end is read from the file name or the title line (rename to `amfi_2023-12-31.xlsx` if neither has it). They are used only if you list `amfi_list` in `universe.size_metrics`. Companies are matched by ISIN.

### 2.5 `sector_map.csv` - recommended

| column | meaning |
|---|---|
| `symbol` and/or `isin` | the stock |
| `sector` | your sector label |

Without it, the industry labels in NSE's current index member lists are used; on the full history about 2,500 of 3,267 companies (including most delisted ones) have no label and are not held to the sector limit. CMIE Prowess covers delisted companies.

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
