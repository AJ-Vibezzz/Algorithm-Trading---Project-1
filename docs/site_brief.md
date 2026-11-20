# Brief for Claude Code: the walkthrough website

Hand this file to Claude Code together with the `site/` folder and `.github/workflows/pages.yml`. It describes what the site is, what is finished, and what Claude Code should do.

## What the site is

A minimal, single-page walkthrough of our momentum strategy for an audience with no finance background. It has 15 slides in a carousel, grouped into five chapters:

| # | Slide | Chapter | Interaction |
|---|---|---|---|
| 1 | Cover: "Some news takes months to show up in a share price" | — | Animated slow-drift chart (the one page-load animation) |
| 2 | What momentum is | Basics | Reveal what 12 made-up shares did next |
| 3 | Why it happens (slow diffusion) | Basics | Slider: how long investors take to notice news |
| 4 | Where we look (ranks 101–500) | The signal | Hover the size ladder; list of eligibility filters |
| 5 | Judging against the market (residual momentum) | The signal | Slider: the market's move, which changes the raw ranking but not the residual one |
| 6 | How far back we look (12-2 and 7-2 windows) | The signal | Hover each month |
| 7 | Two quality checks (rising, steady) | The signal | Pick an example path; pass/fail table |
| 8 | Picking 30, selling below 45 | Building the fund | Slider for rank, toggle for "owned" |
| 9 | Inverse-volatility weights, 6% cap | Building the fund | Slider for one share's volatility |
| 10 | Buying slowly (participation, 15 days, impact) | Building the fund | Sliders for daily traded value and volatility |
| 11 | Four safety limits (smallest wins) | Staying safe | Sliders for turbulence and drawdown |
| 12 | Train/test split and crash timeline | Honest testing | Hover each crash |
| 13 | Why few versions (luck simulator) | Honest testing | Slider for versions tried |
| 14 | Results | Honest testing | Shows "pending" until real results exist |
| 15 | What this can't tell you, plus credits | Honest testing | — |

Every slide has a plain-language headline, two short paragraphs, a "Why we do it" line, and an expandable "Under the hood" section with the technical detail and references.

**Design:**
- Cool graph-paper white, navy-ink text, teal for the strategy, marigold for the drift.
- Headings in Bricolage Grotesque, body text in Source Serif 4.
- The graph-paper grid appears only behind the figures.
- Light and dark mode both work.

**Tech:**
- Plain HTML, CSS and JavaScript. No build step and no libraries.
- Charts are hand-drawn SVG.
- Navigation: arrow keys, swipe, a dot per slide, and a URL hash per slide (`#/5`).

## Files

```
site/
  index.html   page shell
  styles.css   all styling, including dark mode and mobile
  app.js       slides, interactions, navigation
  data.js      every number the site shows (the only file with figures)
.github/workflows/pages.yml   publishes site/ to GitHub Pages on push to main
```

## What Claude Code should do

### 1. Add the files to the repository
Copy `site/` to the repository root. Copy `pages.yml` to `.github/workflows/`. Preview locally:

```bash
python -m http.server 8000 --directory site
```

Then open http://localhost:8000. Opening `index.html` directly also works.

### 2. Check `data.js` against the repository
`facts`, `config` and `episodes` were written by hand from our dataset reports and `config/strategy.yaml`. Claude Code should verify every value against the code and data, and correct any that differ:
- the company counts
- the eligible counts by year
- R²
- the configuration values
- the crash dates and depths (from `python run.py episodes`)
- the trial count at freeze

Set `meta.repoUrl` once the repository is public.

### 3. Write `site/build_site_data.py` (results)
`results` must stay `null` until the one-time `python run.py test` has run. After that, a script, not a person, fills it from the test output files.

Requirements:
- Read the frozen config (`config/frozen_strategy.yaml`) and the test-period result files under `results/test/`. Find the actual file names for the equity curve, the summary metrics, stress-window returns and the deflated Sharpe ratio.
- Write `site/data.js`, keeping every existing key, and fill `results` with exactly the shape documented in `data.js`:
  - `period` and `benchmarkName`
  - `series.dates`, `series.strategy` and `series.benchmark`, as growth of ₹1, monthly points
  - `metrics.strategy`: `cagr`, `vol`, `sharpe`, `maxDrawdown` (negative), `turnover`, `costDrag`
  - `metrics.benchmark`: `cagr`, `vol`, `sharpe`, `maxDrawdown`
  - `stress`: a list of `{ name, strategy, benchmark }`, returns as fractions
  - `deflatedSharpe`
- Set `meta.generated` to the run date, and refuse to run if the test has not been run.
- Never round to flatter, re-run or adjust: the page states that the figures come from the single frozen run.

Optionally, add a "Before 2020 / after 2020" row to the results table. That tests our prediction that momentum weakens after the retail boom.

### 4. Publish
1. Push to `main`.
2. In the repository settings, go to **Pages** and set the source to **GitHub Actions**.
3. The workflow then publishes `site/`.

Publishing Pages from a private repository needs a paid GitHub plan, so the repository may have to be public. **Check with the course instructor before publishing strategy details or results publicly before grading.**

### 5. Polish (optional, keep it minimal)
- On phones, the chart labels are small because each SVG scales down from a 640-unit width. If needed, give the denser charts (slides 4, 6, 12) a taller mobile layout. Don't add libraries.
- Keep the copy plain and short. Every figure on the site must come from `data.js`, and every made-up example must keep its "illustration" caption.

## Rules the site follows (keep them)
- **Toy examples:** the illustrations on slides 1–13 use made-up prices, are labelled as such, and are never presented as results.
- **Real numbers:** they come only from `data.js`.
- **Results:** they appear only after the one-time test run, and come from the script.
- **AI use:** the footer note on the last slide says the site was built with AI assistance. Keep it, and list the site in the AI-use appendix.
- **Disclaimer:** not investment advice.
