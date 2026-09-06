# DCF (Discounted Cash Flow) Valuation

This document explains how the DCF valuation model works in this project, including the methodology, assumptions, and interpretation of results.

## Overview

Discounted Cash Flow (DCF) is an intrinsic valuation method that estimates the value of a company based on its expected future free cash flows, discounted back to present value. This approach answers the question: **"What is this company worth based on the cash it will generate?"**

## Methodology

### Step 1: Data Collection

`src/analysis/dcf_inputs.py` loads every fundamental source StockRadar scrapes,
harmonises units (the statements are in millions, statistics in raw dollars) and
tolerates the scraper's label drift (`FCF Margin` vs `Free Cash Flow Margin`,
`Cash & Cash Equivalents` vs `Cash & Marketable Securities`, `Revenue Growth
Forecast (5Y)` vs `(3Y)` — each of which used to silently return `None` and knock
out a whole branch of the model).

| Data Source | File | Key Metrics |
|-------------|------|-------------|
| **Statistics** | `data/statistics/{T}_statistics_*.json` | Revenue, FCF, FCF margin, shares, market cap, EV, WACC, beta, cash, debt, Altman Z / Piotroski F |
| **Forecast** | `data/forecast/detail/{T}_forecast_*.json` | 8y revenue history, **absolute** analyst revenue levels FY+1..FY+3, high/low ranges, Nasdaq estimate counts and revisions, price targets |
| **Income statement** | `data/financials/{T}_income_*.csv` | 5FY + TTM revenue, interest expense, effective tax rate, diluted shares, share change (YoY) |
| **Cash flow** | `data/financials/{T}_cashflow_*.csv` | 5FY + TTM FCF margin history, SBC, capex, D&A |
| **Ratios** | `data/financials/{T}_ratios_*.csv` | Last close price, market cap, historical EV/EBITDA and EV/FCF |

**Price** comes from the ratios CSV's `Last Close Price` (usually a day or two
old), not from market cap ÷ shares out of the monthly statistics snapshot. Where
a fresh market cap is available too, the implied share count is used — which
self-corrects ADRs like TSM, whose market cap and share count are quoted on
different bases and used to produce a $77 "price" against a $417 quote.

### Step 2: Calculate WACC (Discount Rate)

WACC is **built up from components** and then reconciled with the scraped figure,
rather than trusting either one outright. The scraped WACC is a raw CAPM on a raw
5-year beta; for a high-momentum name that lands near 17 % and quietly buries the
valuation.

```
beta_adj      = clamp(0.67 × beta + 0.33, 0.6, 1.8)        # Blume shrinkage toward 1
size_premium  = 0 % (>$10B) | 1 % ($2–10B) | 2 % (<$2B)
lev_premium   = clamp(2 % × net_debt / market_cap, 0, 4 %)  # financial risk
cost_of_equity = Rf + beta_adj × ERP + size_premium + lev_premium

cost_of_debt  = interest_expense / total_debt              # or a coverage-implied spread
after_tax_cod = cost_of_debt × (1 − effective_tax_rate)

wacc_build_up = w_E × cost_of_equity + w_D × after_tax_cod  # weights from market cap and debt
WACC          = 0.5 × wacc_build_up + 0.5 × wacc_scraped    # build-up alone if none scraped
```

**Constraints:**
- WACC band: 6 % – 16 % (below 6 % the terminal value explodes; above 16 % a scraped beta is noise, not risk)
- WACC must exceed terminal growth by at least 2 %

`Rf`, `ERP` and terminal growth move every valuation in the universe together, so
they are env-overridable: `DCF_RISK_FREE_RATE`, `DCF_EQUITY_RISK_PREMIUM`,
`DCF_TERMINAL_GROWTH`.

The full component breakdown ships in the output under
`assumptions.wacc_components`.

### Step 3: Project Free Cash Flows (5 Years)

**Revenue is anchored to analyst *levels*, not to growth applied to a TTM base.**
Analyst growth rates are fiscal-year over fiscal-year; compounding them onto TTM
revenue double-counts whatever growth the trailing period already contains — for
a company mid-ramp the error is easily 15 %. Where the scraper captured absolute
forecast revenue we use it directly, anchored to the last *actual* fiscal year so
both sit on one fiscal calendar.

| Year | Revenue Logic |
|------|---------------|
| Years 1-3 | Analyst revenue **levels** (`forecast_ranges.revenue`), per path (avg / high / low) |
| Missing level | Analyst growth for that year → consensus growth → the 3Y CAGR read as a *level target* → last growth decayed 60 % toward terminal |
| Years 4-5 | Fade the year-3 growth rate toward terminal growth |
| No levels at all | Fall back to growth-on-TTM (flagged in `data_sources.revenue_anchor`) |

A reporting-currency check compares the forecast base against the statistics
revenue; a ratio outside 0.4–2.5× is treated as a different currency and the
level path is rebased onto the USD figures (TSM forecasts in TWD against a USD
market cap — a ~30× error straight into value per share).

**FCF margin fades**, rather than holding one spot value flat forever:

```
margin(year) = spot + (terminal − spot) × (year − 1) / 4 + scenario_offset × (year − 1) / 4
terminal     = 0.15 × spot + 0.85 × mean(recency_weighted, median(last 3), median(all))
```

A terminal margin is a claim about year 6 to infinity, so no single year may
dominate it. An earlier formulation — `0.35 × spot + 0.65 × recency_weighted` —
gave the trailing twelve months an **effective weight of 58 %** (the last two
periods, 74 %) because spot was counted again inside the recency-weighted mean.
Any company in a cyclical trough was then valued as though the trough were
permanent: Oracle's TTM margin of −29 % (an AI-capex year) pinned its terminal
margin to the 1 % floor against a six-year median of 20 %, and the DCF returned
**−$50 a share**.

Leaning on the through-cycle median alone breaks the opposite case. Reddit went
−28 % → +37 % in a straight line; that is a business reaching scale, not a
cycle, and its six-year median of −12.8 % describes a company that no longer
exists.

Five or six annual points cannot reliably separate a trough from an inflection,
so the model does not pretend to. It computes three readings of the same history
and weights them equally:

| Reading | Question it answers |
|---------|--------------------|
| Recency-weighted mean | Where is the margin heading? (trend-sensitive) |
| Median of the last 3 years | Where has it settled? (level-sensitive) |
| Median of all years | What is the through-cycle norm? (cycle-sensitive) |

All three ship in `margin_readings`, along with their spread. A wide spread is
precisely the case where the terminal margin is a judgement call, so it is shown
on the page and it lowers the confidence score. The terminal margin is also
clamped to the historical range — the model never extrapolates a margin the
company has never reached.

The recency-weighted mean is computed on a **winsorized** series (Tukey fence).
A weighted mean has no defence against one catastrophic year: Carnival's 2020
FCF margin was −392 % and Royal Caribbean's −256 %, and either one dragged the
mean of an otherwise 17–22 % business below zero. The median already ignores
such a point; winsorizing lets the mean ignore it too, without discarding the
observation entirely.

The **scenario offset also ramps in** over the projection rather than applying
in full from year 1. Today's margin is measured, not assumed — charging the full
historical sigma against year 1 as well tripled the scenario band on the
strength of uncertainty the model does not actually have about the current year.

Margins are put on an **unlevered** basis by adding back after-tax interest
expense. Reported FCF is already net of interest, and the value bridge then
subtracts net debt — feeding levered FCF into an enterprise-value bridge charges
the same leverage twice. Harmless for a debt-free company, decisive for a levered
one.

```
Projected FCF = Projected Revenue × margin(year)
```

### Step 4: Calculate Terminal Value

Terminal value represents the company's value beyond the projection period, using the **Gordon Growth Model**:

```
Terminal Value = Final Year FCF × (1 + Terminal Growth)
                 ─────────────────────────────────────────
                        WACC - Terminal Growth
```

### Step 5: Discount to Present Value

All future cash flows are discounted to today's value:

```
PV of FCF = FCF ÷ (1 + WACC)^year
PV of Terminal Value = Terminal Value ÷ (1 + WACC)^5
```

### Step 6: Calculate Intrinsic Value

```
Enterprise Value = Sum of PV(FCFs) + PV(Terminal Value)
Equity Value = Enterprise Value + Net Cash (Cash - Debt)
Shares(year 5) = Shares Outstanding × (1 + share_drift)^5
Intrinsic Value per Share = Equity Value ÷ Shares(year 5)
```

`share_drift` is the recency-weighted 5-year change in diluted shares, clamped to
−4 %..+6 % a year. Value accrues over the projection, so it is shared with the
count that will exist then: buybacks add per-share value and SBC dilution
subtracts it.

---

## Three Scenarios

The model produces three valuation scenarios to capture uncertainty:

| Scenario | WACC Adjustment | Terminal Growth | FCF Margin Adjustment | Revenue Path |
|----------|-----------------|-----------------|----------------------|--------------|
| **Optimistic** | -1% | 3.0% | +1σ | Analyst High |
| **Moderate** | Base | 2.5% | Base | Analyst Average |
| **Pessimistic** | +1% | 2.0% | -1σ | Analyst Low |

**The band is measured, not assumed.** σ is 0.75 × the historical standard
deviation of the FCF margin, clamped to 1.5–8 pp: a steady compounder gets a
narrow band, a cyclical a wide one. When analysts publish no high/low revenue
range, the paths are widened from historical growth volatility instead (and the
model says so in `warnings`).

The three scenarios are also collapsed into a single **probability-weighted fair
value**. Weights shift toward the base case as confidence falls, so a
thinly-supported model does not present a sharp point estimate.

### Example Output

For a stock with current price $150:

| Scenario | Intrinsic Value | Upside | Verdict |
|----------|-----------------|--------|---------|
| Optimistic | $210.50 | +40.3% | Undervalued |
| Moderate | $165.20 | +10.1% | Undervalued |
| Pessimistic | $125.80 | -16.1% | Overvalued |

---

## Key Assumptions

### Default Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Risk-Free Rate | 4.25% | Based on 10-year Treasury yield |
| Equity Risk Premium | 5.50% | Historical market risk premium |
| Terminal Growth | 2.5% | Long-term GDP growth proxy |
| Projection Years | 5 | Standard DCF horizon |
| WACC band | 6% – 16% | Floor stops the terminal value exploding; cap treats an extreme beta as noise |
| Min WACC-Growth Spread | 2% | Ensures mathematical validity |
| Beta shrinkage | 0.67 (Blume) | Toward 1.0, clamped to 0.6–1.8 |
| Share drift band | −4% .. +6% /yr | Caps extrapolated buybacks and dilution |
| Exit multiple band | 10x – 30x EV/FCF | For the terminal cross-check |

`DCF_RISK_FREE_RATE`, `DCF_EQUITY_RISK_PREMIUM` and `DCF_TERMINAL_GROWTH`
override the three macro inputs without touching the model.

### FCF Margin

- **Spot** from the statistics `FCF Margin` (or `FCF ÷ Revenue`)
- **Normalised** = recency-weighted mean of 5FY + TTM cash-flow history
- Put on an **unlevered** basis by adding back after-tax interest expense
- The projection fades spot → terminal over five years; floor 1%, cap 60%
- With no cash-flow statement scraped, it degrades to the spot value and says so
  in `data_sources.margin_basis` and in the confidence score

---

## Sensitivity Analysis

The model includes a sensitivity matrix showing how intrinsic value changes with different WACC and terminal growth assumptions:

```
                    Terminal Growth
           ┌─────┬─────┬─────┬─────┬─────┐
           │1.5% │2.0% │2.5% │3.0% │3.5% │
     ┌─────┼─────┼─────┼─────┼─────┼─────┤
W    │7.0% │ 185 │ 195 │ 210 │ 230 │ 255 │
A    │8.0% │ 160 │ 168 │ 178 │ 190 │ 205 │
C    │9.0% │ 142 │ 148 │ 155 │ 163 │ 173 │ ← Base
C    │10.0%│ 128 │ 132 │ 138 │ 144 │ 151 │
     │11.0%│ 116 │ 120 │ 124 │ 129 │ 135 │
     └─────┴─────┴─────┴─────┴─────┴─────┘
                              ↑
                            Base
```

A second matrix crosses **year-1 FCF margin against revenue growth** — the two
inputs an analyst actually argues about. Unlike the previous version, the
WACC × growth matrix rebuilds the FCF path for every terminal-growth column,
because years 4-5 fade toward that rate; holding one path fixed made the matrix
inconsistent with the headline it was supposed to explain.

---

## Normalised Capex

A free-cash-flow DCF treats every dollar of capex as gone forever. For a company
mid-buildout that is the wrong question. Amazon runs **22.3 % of revenue through
capex against 9.7 % of depreciation** — the 12.6-point gap is capacity being
built — so the model prices a $1.3T-revenue business at a 2.6 % cash margin and
lands 95 % below the market, while the same filing shows $135B of net income and
a 20.6x P/E.

When capex exceeds D&A by more than 3 points of revenue, the model reports what
the company is worth with capex normalised to maintenance level (proxied by
depreciation, i.e. owner earnings = operating cash flow − D&A). **66 of 271
tickers trigger it** — essentially the entire AI-capex cohort:

| | As modelled | Normalised capex | Price |
|---|---|---|---|
| AMZN | $9 | $117 | $259 |
| ORCL | −$58 | $282 | $154 |
| META | $511 | $986 | $611 |
| MSFT | $279 | $474 | $510 |
| TSM | $266 | $443 | $417 |

Depreciation is a crude proxy for maintenance capex and understates it for a
business whose asset base is still compounding, so the normalised figure is an
upper bound, not a target price. The real question — when the investment cycle
ends and what return the new capacity earns — is not one a discounting model
answers.

---

## Backtest — does any of this predict anything?

`scripts/backtest_dcf.py` rolls the model back to a past date, feeds it only
files dated on or before that day (`FundamentalBundle(as_of=…)`), and scores its
output against what the share price actually did.

```bash
uv run python scripts/backtest_dcf.py                      # every variant, every formation date
uv run python scripts/backtest_dcf.py --variant owner
```

Formation dates are the days with statistics + forecast + ratios all present:
2026-02-02, 2026-06-01, 2026-07-02. Prices come from the weekly ratios files
(`Last Close Price`), which run 2026-02-01 to 2026-09-04.

### What it found

| Signal | 2026-02-02 (7 mo) | 2026-06-01 (3 mo) | 2026-07-02 (2 mo) |
|---|---|---|---|
| DCF weighted upside | −0.01 | +0.36 | +0.38 |
| **FCF yield (one line of data)** | −0.01 | **+0.45** | **+0.47** |
| DCF after removing FCF yield | −0.08 | +0.07 | +0.08 |
| FCF yield after removing DCF | +0.04 | **+0.30** | **+0.33** |

Spearman rank IC against forward return. The DCF's upside correlates **+0.80**
with FCF yield: it is, for ranking purposes, an expensive way to recompute a
ratio. Orthogonalise the two and almost nothing survives on the DCF side, while
the yield keeps most of its power.

**The four terminal-margin variants are indistinguishable** — `legacy` +0.258,
`recency` +0.254, `ensemble` +0.243, `owner` +0.244 mean IC, against a standard
error of ±0.07. None of the reworking documented above improved stock selection.
They were kept because they remove specific, demonstrable defects (a 58 % weight
on one year, an arbitrary 1 % floor driving three of five projection years,
leverage charged twice, one catastrophic year poisoning a mean) — not because
the backtest endorsed them. It does not.

### What *did* validate

| | 2026-02-02 | 2026-06-01 | 2026-07-02 |
|---|---|---|---|
| Median forward return, `meaningful` | +8.3 % | 0.0 % | 0.0 % |
| Median forward return, **not meaningful** | +0.7 % | **−17.4 %** | **−9.7 %** |

The applicability gate separates outcomes in all three windows, including the
one where the upside signal had no power at all. Restricting to confidence ≥ 60
also lifts the IC (+0.37 → +0.48, +0.39 → +0.47) in the two windows where there
was a signal to lift.

So the model earns its keep by saying **which companies it cannot value** and
**how much of each answer is assumption** — not by ranking on intrinsic value.
The page shows FCF yield next to the headline for that reason.

### Limits of this test, stated plainly

* The price series is seven months long. A DCF is a multi-year thesis; a
  two-to-seven-month window mostly measures momentum and style rotation.
* The three formation dates overlap heavily in time and holdings — roughly 1.5
  independent observations, so the significance stars overstate confidence.
* Both windows with a strong IC had a *negative* median market return; the one
  with a positive market return had zero IC. That is one regime, one direction.

Which is why nothing here was tuned to make the backtest look better. Re-run it
after any change to the model, and treat an IC improvement of less than ~0.15 as
noise.

---

## Reverse DCF

The single most useful output for a stock picker: **what does today's price
already assume?**

The model bisects on a multiplier applied to the consensus growth path until
intrinsic value equals the market price, holding margin, WACC and terminal growth
at their moderate-case values. It reports the implied 5-year revenue CAGR next to
what analysts actually forecast, plus the terminal FCF margin the price implies at
consensus growth.

```json
"reverse_dcf": {
  "implied_revenue_cagr_5y": 57.38,
  "consensus_revenue_cagr_5y": 31.60,
  "gap_pp": -25.78,
  "implied_terminal_fcf_margin": 60.0,
  "implied_terminal_fcf_margin_capped": true,
  "interpretation": "Market already prices in more growth than analysts forecast"
}
```

---

## Terminal Value Cross-Check

Gordon growth is checked against the company's **own median historical EV/FCF
multiple** (from the ratios CSV, clamped to 10–30x). Where the two disagree by
more than 40 %, the terminal assumption is the swing factor and the model says so
— far more actionable than the old bare "terminal value is 78 % of EV" warning,
which is now also reported as `summary.implied_terminal_fcf_multiple`.

---

## Confidence Score

Every valuation is graded 0–100 (A/B/C/D) on how much of the answer rests on
measured data versus assumption:

| Factor | What it measures |
|--------|------------------|
| Data freshness | Age of the oldest source, and which are missing entirely |
| Analyst coverage | Nasdaq estimate count; penalised when outer years are thinly covered |
| Revenue anchor | Absolute analyst levels (90) vs growth-on-TTM fallback (45) |
| Margin stability | 5-year standard deviation of the FCF margin |
| Terminal reliance | Terminal value as a share of enterprise value |
| Scenario tightness | Optimistic-to-pessimistic spread relative to the base case |

The grade also sets the probability weights on the fair value: a D-grade model
concentrates on its base case rather than pretending to a precise range.

---

## Applicability Gate

A negative equity value is arithmetically valid and completely meaningless.
Before quoting `intrinsic_value`, read `applicability`:

```json
"applicability": {
  "meaningful": false,
  "reasons": [
    "trailing free cash flow is negative",
    "modelled equity value is negative — debt exceeds the discounted cash flows"
  ]
}
```

Roughly a quarter of the universe trips this gate — leveraged restaurant and
airline names, and companies in a heavy investment phase. The batch script lists
them separately instead of printing a number that looks like an opinion.

---

## Value Bridge

The model shows how intrinsic value is constructed:

```
┌────────────────────────────────────┐
│ PV of FCFs (Years 1-5)    $50.2B   │
├────────────────────────────────────┤
│ PV of Terminal Value     $120.5B   │
├────────────────────────────────────┤
│ = Enterprise Value       $170.7B   │
├────────────────────────────────────┤
│ + Net Cash                $15.3B   │
├────────────────────────────────────┤
│ = Equity Value           $186.0B   │
├────────────────────────────────────┤
│ ÷ Shares Outstanding      1.2B     │
├────────────────────────────────────┤
│ = Intrinsic Value/Share   $155.00  │
└────────────────────────────────────┘
```

---

## Interpretation Guide

### Upside/Downside Percentage

```
Upside % = (Intrinsic Value - Current Price) / Current Price × 100
```

| Upside Range | Interpretation |
|--------------|----------------|
| > +30% | Significantly Undervalued |
| +10% to +30% | Moderately Undervalued |
| -10% to +10% | Fairly Valued |
| -30% to -10% | Moderately Overvalued |
| < -30% | Significantly Overvalued |

### Valuation Verdict

- **Undervalued**: Intrinsic value > Current price (positive upside)
- **Overvalued**: Intrinsic value < Current price (negative upside)

### Terminal Value % Warning

If terminal value represents >85% of enterprise value, the model displays a warning. This indicates high sensitivity to terminal assumptions and lower confidence in the valuation.

---

## Limitations & Warnings

### When DCF May Be Unreliable

| Condition | Warning |
|-----------|---------|
| Negative FCF from growth capex | `applicability` explains it is capacity being built, not an unprofitable business, and points at the normalised-capex cross-check |
| Negative FCF otherwise | Flagged in `applicability`; the terminal margin leans on history |
| Terminal-margin readings disagree > 8 pp | "this company is either in a cyclical trough or at a structural inflection, and the data cannot say which" |
| Outlier years winsorized | Says how many, and prints the raw series |
| No analyst revenue levels | Falls back to growth-on-TTM, and the confidence score drops |
| No cash-flow statement | Margin normalisation degrades to spot; confidence drops |
| Stale source | "statistics data is 217 days old (>120d) — rerun the scraper" |
| High terminal value % | ">85 % of enterprise value — the answer is mostly an assumption about year 6 onward" |
| SBC > 8 % of revenue | "reported FCF overstates owner earnings" |
| Volatile margin | "FCF margin has swung ±N pp over five years — the band is wide for a reason" |
| Currency mismatch | "Forecast revenue is N× the statistics revenue — rebasing onto the USD figures" |
| Restated share count | ADR or stale snapshot reconciled against the traded price |
| WACC clamped | Hit the 6–16 % band |

### Not Suitable For

- **Early-stage growth companies** with no positive FCF
- **Cyclical businesses** with volatile cash flows
- **Financial institutions** (banks, insurance) - use different models
- **Distressed companies** with uncertain survival

---

## API Endpoint

```
GET /api/stocks/{ticker}/dcf
```

### Response Structure

Every field the previous version returned is still returned, in the same shape.
The new blocks are additive:

```json
{
  "ticker": "AAPL",
  "current_price": 185.50,
  "data_date": "2025-02-01",
  "computed_date": "2025-02-03",
  "applicability": { "meaningful": true, "reasons": [], "note": "..." },
  "fair_value":    { "weighted": 172.6, "upside_pct": -47.4, "weights": {...} },
  "confidence":    { "score": 62, "grade": "C", "factors": [...], "summary": "..." },
  "reverse_dcf":   { "implied_revenue_cagr_5y": 4.2, "consensus_revenue_cagr_5y": 6.1, ... },
  "cross_checks":  { "checks": [analyst target, Lynch, Graham, model EV vs market EV] },
  "data_sources":  { "used": [...], "age_days": {...}, "revenue_anchor": "...",
                     "margin_basis": "...", "fcf_basis": "...", "wacc_basis": "...",
                     "price_source": "..." },
  "warnings": [...],
  "scenarios": {
    "optimistic": {
      "intrinsic_value": 245.80,
      "upside_pct": 32.5,
      "verdict": "Undervalued",
      "assumptions": {...},           // + wacc_components, fcf_margin_path,
                                      //   growth_yr3, revenue_source, share_drift_pct
      "projections": [...],
      "value_bridge": [...],
      "terminal_cross_check": {...},  // moderate scenario only
      "warnings": []
    },
    "moderate": {
      "intrinsic_value": 198.20,
      "upside_pct": 6.8,
      "verdict": "Undervalued",
      "sensitivity": {...},
      ...
    },
    "pessimistic": {
      "intrinsic_value": 162.50,
      "upside_pct": -12.4,
      "verdict": "Overvalued",
      ...
    }
  }
}
```

---

## Files & Structure

```
src/analysis/dcf_inputs.py        # Loads and harmonises every scraped source
src/analysis/dcf_calculator.py    # Core DCF calculation engine
backend/app/routers/dcf.py        # API endpoint
backend/app/routers/scheduler.py  # Daily statements top-up (04:00) + DCF recompute
data/dcf/                         # Cached DCF results
  └── {TICKER}_dcf_{date}.json
scripts/compute_dcf.py            # Batch computation script
frontend/src/pages/StockDCF.jsx   # DCF page
```

## Keeping the inputs fresh

The income and cash-flow statements feed margin normalisation, the interest
add-back, share drift and the exit multiple. They sat at 217 days old and covered
only 181 of 262 tickers, which is why the model used to fall back to spot values
everywhere. They are now on the same rolling top-up as statistics:

| Pipeline | Hour | Batch | Notes |
|----------|------|-------|-------|
| statistics | 03:00 | 45 | ~6 days for a full cycle |
| statements (income + cash flow) | 04:00 | 15 | two pages per ticker; recomputes the DCF for the batch it just refreshed |

```bash
curl -X POST 'localhost:6000/api/scheduler/statements/run?limit=40'   # manual top-up
curl -s localhost:6000/api/scheduler/status | jq .statements           # freshness
uv run python scripts/compute_dcf.py --all --force                     # rebuild every DCF
```

---

## Formula Summary

| Metric | Formula |
|--------|---------|
| Cost of equity | `Rf + clamp(0.67β+0.33) × ERP + size_premium + leverage_premium` |
| WACC | `0.5 × (w_E·CoE + w_D·CoD·(1−t)) + 0.5 × WACC_scraped` |
| Projected Revenue | Analyst level for years 1-3; year-3 growth faded to terminal for 4-5 |
| Unlevered FCF margin | `FCF margin + interest_expense × (1−t) / revenue` |
| Projected FCF | `Projected Revenue × margin(year)` |
| Terminal Value | `Final FCF × (1 + g) / (WACC - g)` |
| Present Value | `Future Value / (1 + WACC)^n` |
| Enterprise Value | `Σ PV(FCFs) + PV(Terminal Value)` |
| Equity Value | `Enterprise Value + Net Cash` |
| Intrinsic Value | `Equity Value / (Shares × (1 + drift)^5)` |
| Upside % | `(Intrinsic - Current) / Current × 100` |

---

---

---

## Real Example: Apple Inc. (AAPL)

A full walk-through on live data (computed 2026-09-06, statistics as of 2026-07-02).

### Input data

| Metric | Value | Source |
|--------|-------|--------|
| Current price | $328.21 | ratios last close (2d old) |
| Base revenue (FY 2025) | $416.2B | analyst revenue levels (fiscal-year aligned) |
| Spot FCF margin | 29.21% | statistics |
| Normalised FCF margin (5y weighted) | 27.28% | cash-flow statement |
| FCF margin σ | 1.69 pp | history [28.91, 24.33, 28.42, 26.58, 28.86, 26.01] |
| Unlevered uplift | +0.6 pp | after-tax interest add-back |
| Shares outstanding | 14.59B | statistics / implied |
| Share drift | -2.71% /yr | diluted shares, 5y weighted |
| Net cash | $61.9B | balance sheet |
| Beta (5Y) → adjusted | 1.086 → 1.058 | Blume shrinkage |
| WACC | 9.98% | blended (build-up + scraped) |

### WACC build-up

```
cost of equity = 4.25% + 1.058 × 5.5% + 0.0% (size) + 0.0% (leverage) = 10.07%
after-tax cost of debt = 3.12% × (1 − tax) = 2.58%  [interest expense / total debt]
build-up  = 98.3% × 10.07% + 1.7% × 2.58% = 9.94%
scraped   = 10.025%
WACC      = 0.5 × 9.94% + 0.5 × 10.025% = 9.98%
```

### Step 1 — project revenue and FCF

Revenue for years 1-3 is taken from analyst *levels*; the margin fades from spot to the normalised level.

| Year | Growth | Revenue | FCF margin | FCF | Discount factor | PV |
|------|--------|---------|-----------|-----|-----------------|-----|
| 1 | 14.72% | $477.4B | 29.21% | $139.4B | 0.9092 | $126.8B |
| 2 | 9.74% | $523.9B | 28.89% | $151.4B | 0.8267 | $125.1B |
| 3 | 6.98% | $560.4B | 28.58% | $160.2B | 0.7517 | $120.4B |
| 4 | 5.48% | $591.2B | 28.27% | $167.1B | 0.6835 | $114.2B |
| 5 | 3.99% | $614.8B | 27.95% | $171.8B | 0.6215 | $106.8B |
| **Total PV of FCFs** | | | | | | **$593.3B** |

### Step 2 — terminal value

```
Terminal Value = $171.8B × (1 + 2.5%) / (9.98% − 2.5%)
               = $2,354.3B
PV of TV       = $2,354.3B × 0.6215 = $1,463.1B
               = 71.1% of enterprise value
               → implies a 13.7x exit multiple on year-5 FCF
```

Cross-check: Apple's own 5-year median EV/FCF is 28.8x (applied 28.8x), which would give $293.52 per share — +76.3% versus Gordon growth. Gordon growth and the historical exit multiple disagree by more than 40% — treat the terminal assumption as the swing factor.

### Step 3 — bridge to value per share

```
Enterprise Value = $593.3B + $1,463.1B = $2,056.4B
Equity Value     = $2,056.4B + $61.9B (net cash) = $2,118.3B
Shares (year 5)  = 14.59B × (1 + -2.71%)^5 = 12.72B
Intrinsic Value  = $2,118.3B / 12.72B = $166.53
```

### Result

| | Value | vs price |
|---|-------|----------|
| Pessimistic | $134.67 | -59.0% |
| Moderate | $166.53 | -49.3% |
| Optimistic | $214.27 | -34.7% |
| **Probability-weighted** | **$169.72** | **-48.3%** |

Confidence **62 (C)** — Indicative only — several inputs are assumed or stale.

- Data freshness: 28 — Oldest source 216d old
- Analyst coverage: 36 — 6 estimates
- Revenue anchor: 90 — analyst revenue levels (fiscal-year aligned)
- Margin stability: 88 — FCF margin σ = 1.7 pp over 5y
- Terminal reliance: 60 — Terminal value is 71% of EV
- Scenario tightness: 72 — Optimistic-to-pessimistic spread is 48% of the base case

Reverse DCF: at 9.98% WACC the price implies a 25.62% 5-year revenue CAGR, against a consensus of 8.12% — a gap of -17.5 pp. Market already prices in more growth than analysts forecast.
