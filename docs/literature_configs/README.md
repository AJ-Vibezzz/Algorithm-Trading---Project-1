# Momentum strategies from the literature — mapped to the strategy template

32 published momentum strategies, each written out in the layout of your
`strategy_template.yaml`. Each file records **what the source did**. None of
them is your group's design.

## How to read each file

- **`source:` block** (added on top of the template): citation, DOI, market,
  sample period, headline finding, and a `pipeline_fit` of `full`, `partial` or
  `none` with notes on what the template can and cannot reproduce. If
  `python run.py check` rejects unknown keys, delete this block before running.
- **Template sections** are filled only where the source specifies a value.
  `null` means the source is silent, or its choice does not translate to NSE
  (US price floors, value-weighting, which the template does not offer).
- **`# [verify]`** marks every detail not confirmed against the original paper
  in this pass. The brief penalises invented references, so check each one in
  the PDF before it goes in your report.
- **Window convention (checked against the pipeline, `src/momo/signals.py`):**
  `lookback_months` is how many months before the decision date the window
  **starts**, and `skip_months` how many months before it **ends**. "12-1"
  momentum (returns of months t-12 to t-2, decided at the end of t-1) is
  therefore `lookback 12, skip 1`, and Novy-Marx's "12-7" is `lookback 12,
  skip 6`. The Nifty momentum indices decide at the end of month M-1 on
  P(M-1)/P(M-7) and P(M-1)/P(M-13), i.e. `6/0` and `12/0`.
- **Long-short vs long-only:** most academic results are winners-minus-losers.
  The template is long-only, so these configs reproduce the **long leg** only.
  Expect weaker and more market-correlated results than the papers report.

## Index

| # | File | Source | Family | Signal in template | Fit | India? |
|---|------|--------|--------|--------------------|-----|--------|
| 01 | `01_jegadeesh_titman_1993` | Jegadeesh & Titman (1993) JF | Classic J/K | return 6/0, K=6 | full | |
| 02 | `02_carhart_1997_pr1yr` | Carhart (1997) JF | Factor | return 12/1 | full | |
| 03 | `03_fama_french_umd` | French Data Library; FF (2012) JFE | Factor | return 12/1 | partial | |
| 04 | `04_asness_moskowitz_pedersen_2013` | AMP (2013) JF | Global | return 12/1 | partial | |
| 05 | `05_novy_marx_2012_intermediate` | Novy-Marx (2012) JFE | Window variant | return 12/6 (12-7) | full | |
| 06 | `06_george_hwang_2004_52w_high` | George & Hwang (2004) JF | Anchoring | high_52w | full | |
| 07 | `07_rachev_et_al_2007_reward_risk` | Rachev et al. (2007) JBF | Risk-adjusted | risk_adjusted_return | partial | |
| 08 | `08_blitz_huij_martens_2011_residual` | BHM (2011) JEF | Residual | residual 12/1, 36m | partial | |
| 09 | `09_grundy_martin_2001_factor_adjusted` | Grundy & Martin (2001) RFS | Residual / beta | residual | partial | |
| 10 | `10_da_gurun_warachka_2014_frog_in_pan` | DGW (2014) RFS | Path quality | return + ID filter | full | |
| 11 | `11_grinblatt_moskowitz_2004_consistency` | Grinblatt & Moskowitz (2004) JFE | Path quality | — | none | |
| 12 | `12_moskowitz_grinblatt_1999_industry` | Moskowitz & Grinblatt (1999) JF | Industry | — | none | |
| 13 | `13_lee_swaminathan_2000_volume` | Lee & Swaminathan (2000) JF | Volume | — | none | |
| 14 | `14_hong_lim_stein_2000_bad_news` | Hong, Lim & Stein (2000) JF | Info diffusion | segment choice | partial | |
| 15 | `15_cooper_gutierrez_hameed_2004_market_states` | CGH (2004) JF | Market state | regime_filter (approx.) | partial | |
| 16 | `16_barroso_santa_clara_2015_vol_scaled` | Barroso & Santa-Clara (2015) JFE | Risk-managed | vol_target 12%, 126d | full | |
| 17 | `17_daniel_moskowitz_2016_crashes` | Daniel & Moskowitz (2016) JFE | Crash risk | vol_target + regime (approx.) | partial | |
| 18 | `18_moskowitz_ooi_pedersen_2012_tsmom` | MOP (2012) JFE | Time-series | absolute screen | partial | |
| 19 | `19_antonacci_dual_momentum` | Antonacci (2012 WP / 2014 book) | Dual | return + absolute screen | partial | |
| 20 | `20_faber_2007_ma_timing` | Faber (2007) JWM | Trend overlay | regime_filter 10m SMA | partial | |
| 21 | `21_novy_marx_velikov_2016_trading_costs` | Novy-Marx & Velikov (2016) RFS | Cost mitigation | exit_rank_multiple | full | |
| 22 | `22_nifty200_momentum30` | NSE Indices methodology | Index | 2 × risk_adjusted_return (6/0, 12/0) | partial | ✓ |
| 23 | `23_nifty_midcap150_momentum50` | NSE Indices methodology | Index | same, mid caps | partial | ✓ |
| 24 | `24_nifty_smallcap250_momentum_quality100` | NSE Indices methodology | Index (mom + quality) | momentum half only | partial | ✓ |
| 25 | `25_nifty500_momentum50` | NSE Indices whitepaper | Index | same, all-cap | partial | ✓ |
| 26 | `26_sehgal_balakrishnan_2002_india` | Sehgal & Balakrishnan (2002) Vikalpa | Indian evidence | return | full | ✓ |
| 27 | `27_ansari_khan_2012_india` | Ansari & Khan (2012) Managerial Finance | Indian evidence | return 3/0, K=3 | full | ✓ |
| 28 | `28_sehgal_jain_2011_india_sectoral` | Sehgal & Jain (2011) JAMR | Indian, sectoral | return 6/0, K=6 | partial | ✓ |
| 29 | `29_agarwalla_jacob_varma_iima_wml` | Agarwalla, Jacob & Varma (2013) IIMA WP | Indian factor | WML (use as data) | partial | ✓ |
| 30 | `30_sehgal_pandey_sen_2024_india_dual` | Sehgal, Pandey & Sen (2024) IJOEM | Indian dual | return + absolute screen | full | ✓ |
| 31 | `31_maheshwari_dhankar_2017_india_volume` | Maheshwari & Dhankar (2017) GBR | Indian volume | — | none | ✓ |
| 32 | `32_gray_vogel_2016_quantitative_momentum` | Gray & Vogel (2016) book | Composite | return + ID filter, quarterly | full | |

## Strategies found but not mapped (no template field for them)

These are real momentum families. They need data or mechanics the template
lacks, so they have no config file. All details here are [verify].

- **Earnings momentum / post-earnings drift:** Chan, Jegadeesh & Lakonishok
  (1996), "Momentum Strategies", JF 51(5), 1681-1713. Novy-Marx (2015 working
  paper), "Fundamentally, Momentum is Fundamental Momentum". Both need
  earnings-surprise data.
- **Seasonal (same-calendar-month) momentum:** Heston & Sadka (2008), JFE.
- **Factor momentum:** Ehsani & Linnainmaa (2022), JF. Ranks factors, not stocks.
- **Crowding ("comomentum"):** Lou & Polk, RFS. A risk indicator, useful for
  your stress-test narrative.
- **Trend following over a century:** Hurst, Ooi & Pedersen (2017), JPM.
  Blends 1-, 3- and 12-month time-series signals.

## Checks made when these files were added to the repository

- Every file loads with the project's config loader once the `source:` block is
  removed (`StrategyConfig.from_dict`), and its values pass the range checks.
- Signal windows were converted to the pipeline's convention (see above). The
  original files used "window length"; under the pipeline's reading they would
  have measured shorter windows, and file 05 was rejected outright
  (`skip_months must be below lookback_months`).
- File 09 leaves `residual_estimation_months` empty; `python run.py check` now
  lists it as a field to fill in.
- Citations found as given (bibliographic record only, not the full text):
  Rachev et al. (2007) JBF 31(8) 2325-2346; Sehgal & Jain (2011) JAMR 8(1)
  99-122; Maheshwari & Dhankar (2017) GBR 18(4) 974-992; Sehgal, Pandey & Sen
  (2024) IJOEM, doi 10.1108/IJOEM-09-2023-1518. Everything marked `[verify]`
  still needs checking against the paper.

## Before you use any of this

1. **Read the original for every paper you cite.** Fill in or correct each
   `[verify]` and record what you checked. The brief's AI-use appendix asks how
   you checked the output.
2. **These are evidence, not choices.** The brief requires your group to design
   its own strategy and parameters. Use these files to argue why a choice is
   defensible. Don't copy one in as your config.
3. **Count the combinations.** If you test several of these signals on the
   training period, each counts toward "how many parameter combinations you
   tried".
