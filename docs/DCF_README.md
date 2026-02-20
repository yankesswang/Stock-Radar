# DCF (Discounted Cash Flow) Valuation

This document explains how the DCF valuation model works in this project, including the methodology, assumptions, and interpretation of results.

## Overview

Discounted Cash Flow (DCF) is an intrinsic valuation method that estimates the value of a company based on its expected future free cash flows, discounted back to present value. This approach answers the question: **"What is this company worth based on the cash it will generate?"**

## Methodology

### Step 1: Data Collection

The DCF calculator uses two primary data sources:

| Data Source | Key Metrics |
|-------------|-------------|
| **Statistics** | Revenue, FCF, FCF Margin, Shares Outstanding, Market Cap, WACC, Beta, Cash, Debt |
| **Forecast** | Revenue Growth (Year 1 & 2), Analyst High/Low/Avg estimates |

### Step 2: Calculate WACC (Discount Rate)

The Weighted Average Cost of Capital (WACC) represents the required return for investors. It's determined in priority order:

1. **Direct WACC** - Use reported WACC from financial data if available
2. **CAPM Calculation** - If WACC unavailable, calculate using:
   ```
   WACC = Risk-Free Rate + (Beta × Equity Risk Premium)
   WACC = 4.25% + (Beta × 5.50%)
   ```
3. **Default Rate** - If Beta unavailable: `4.25% + 5.50% = 9.75%`

**Constraints:**
- Minimum WACC: 4%
- WACC must exceed terminal growth by at least 2%

### Step 3: Project Free Cash Flows (5 Years)

Revenue and FCF are projected for 5 years using a **fade-to-terminal** approach:

| Year | Growth Rate Logic |
|------|-------------------|
| Year 1 | Analyst forecast (Year 1 estimate) |
| Year 2 | Analyst forecast (Year 2 estimate) |
| Years 3-5 | Linear fade from Year 2 growth → Terminal growth |

**FCF Calculation:**
```
Projected FCF = Projected Revenue × FCF Margin
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
Intrinsic Value per Share = Equity Value ÷ Shares Outstanding
```

---

## Three Scenarios

The model produces three valuation scenarios to capture uncertainty:

| Scenario | WACC Adjustment | Terminal Growth | FCF Margin Adjustment | Growth Rates |
|----------|-----------------|-----------------|----------------------|--------------|
| **Optimistic** | -1% | 3.0% | +2 percentage points | Analyst High |
| **Moderate** | Base | 2.5% | Base | Analyst Average |
| **Pessimistic** | +1% | 2.0% | -2 percentage points | Analyst Low |

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
| Min WACC | 4% | Floor to prevent unrealistic valuations |
| Min WACC-Growth Spread | 2% | Ensures mathematical validity |

### FCF Margin

- Uses reported FCF Margin from financial statements
- If unavailable, calculates: `FCF ÷ Revenue`
- Minimum margin floor: 1%

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
| Negative FCF | "DCF may not be meaningful for negative FCF companies" |
| No analyst growth data | Uses terminal growth rate as fallback |
| High terminal value % | "High reliance on terminal assumptions" |
| WACC adjusted | "WACC adjusted to maintain minimum spread" |

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

```json
{
  "ticker": "AAPL",
  "current_price": 185.50,
  "data_date": "2025-02-01",
  "computed_date": "2025-02-03",
  "scenarios": {
    "optimistic": {
      "intrinsic_value": 245.80,
      "upside_pct": 32.5,
      "verdict": "Undervalued",
      "assumptions": {...},
      "projections": [...],
      "value_bridge": [...],
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
src/analysis/dcf_calculator.py    # Core DCF calculation engine
backend/app/routers/dcf.py        # API endpoint
data/dcf/                         # Cached DCF results
  └── {TICKER}_dcf_{date}.json
scripts/compute_dcf.py            # Batch computation script
```

---

## Formula Summary

| Metric | Formula |
|--------|---------|
| WACC (CAPM) | `Rf + β × ERP` |
| Projected Revenue | `Previous Revenue × (1 + Growth Rate)` |
| Projected FCF | `Projected Revenue × FCF Margin` |
| Terminal Value | `Final FCF × (1 + g) / (WACC - g)` |
| Present Value | `Future Value / (1 + WACC)^n` |
| Enterprise Value | `Σ PV(FCFs) + PV(Terminal Value)` |
| Equity Value | `Enterprise Value + Net Cash` |
| Intrinsic Value | `Equity Value / Shares Outstanding` |
| Upside % | `(Intrinsic - Current) / Current × 100` |

---

---

## Real Example: Apple Inc. (AAPL)

Let's walk through the complete DCF calculation for Apple using real data.

### Input Data (as of Feb 2026)

| Metric | Value | Source |
|--------|-------|--------|
| Current Stock Price | $259.48 | Market |
| Revenue (TTM) | $435.6B | Income Statement |
| Free Cash Flow (TTM) | $123.3B | Cash Flow Statement |
| FCF Margin | 28.31% | FCF ÷ Revenue |
| Shares Outstanding | 14.68B | Share Statistics |
| Cash & Securities | $144.8B | Balance Sheet |
| Total Debt | $90.5B | Balance Sheet |
| Net Cash | $54.3B | Cash - Debt |
| Beta (5Y) | 1.093 | Stock Statistics |
| WACC | 10.02% | Financial Data |

### Growth Rate Assumptions (Moderate Scenario)

| Period | Revenue Growth | Source |
|--------|---------------|--------|
| Year 1 | 10.9% | Analyst Average Forecast |
| Year 2 | 6.9% | Analyst Average Forecast |
| Years 3-5 | Fade to 2.5% | Linear interpolation to terminal |
| Terminal | 2.5% | Long-term GDP growth |

---

### Step-by-Step Calculation (Moderate Scenario)

#### Step 1: Project Revenue & FCF for 5 Years

Starting with base revenue of **$435.6B** and FCF margin of **28.31%**:

| Year | Growth Rate | Projected Revenue | FCF (Revenue × 28.31%) |
|------|-------------|-------------------|------------------------|
| 1 | 10.90% | $483.1B | $136.8B |
| 2 | 6.90% | $516.4B | $146.2B |
| 3 | 5.43% | $544.5B | $154.1B |
| 4 | 3.97% | $566.1B | $160.3B |
| 5 | 2.50% | $580.2B | $164.3B |

**Growth Rate Fade Calculation (Years 3-5):**
```
Year 3 growth = 6.9% + (2.5% - 6.9%) × (1/3) = 5.43%
Year 4 growth = 6.9% + (2.5% - 6.9%) × (2/3) = 3.97%
Year 5 growth = 2.5% (terminal)
```

#### Step 2: Discount FCFs to Present Value

Using WACC = **10.02%**:

| Year | FCF | Discount Factor | Present Value |
|------|-----|-----------------|---------------|
| 1 | $136.8B | 1/(1.1002)¹ = 0.9089 | $124.3B |
| 2 | $146.2B | 1/(1.1002)² = 0.8261 | $120.8B |
| 3 | $154.1B | 1/(1.1002)³ = 0.7508 | $115.7B |
| 4 | $160.3B | 1/(1.1002)⁴ = 0.6824 | $109.4B |
| 5 | $164.3B | 1/(1.1002)⁵ = 0.6202 | $101.9B |
| **Total PV of FCFs** | | | **$572.1B** |

**Discount Factor Formula:**
```
Discount Factor = 1 / (1 + WACC)^year
Example Year 3: 1 / (1.1002)³ = 0.7508
```

#### Step 3: Calculate Terminal Value

Using Gordon Growth Model with final year FCF = **$164.3B**:

```
Terminal Value = FCF₅ × (1 + Terminal Growth) / (WACC - Terminal Growth)
               = $164.3B × (1 + 2.5%) / (10.02% - 2.5%)
               = $164.3B × 1.025 / 0.0752
               = $2,237.8B
```

**Discount Terminal Value to Present:**
```
PV of Terminal Value = $2,237.8B / (1.1002)⁵
                     = $2,237.8B × 0.6202
                     = $1,388.0B
```

#### Step 4: Calculate Enterprise Value

```
Enterprise Value = PV of FCFs + PV of Terminal Value
                 = $572.1B + $1,388.0B
                 = $1,960.1B
```

#### Step 5: Calculate Equity Value

```
Equity Value = Enterprise Value + Net Cash
             = $1,960.1B + $54.3B
             = $2,014.3B
```

#### Step 6: Calculate Intrinsic Value per Share

```
Intrinsic Value = Equity Value / Shares Outstanding
                = $2,014.3B / 14.68B shares
                = $137.21 per share
```

#### Step 7: Compare to Current Price

```
Upside % = (Intrinsic Value - Current Price) / Current Price × 100
         = ($137.21 - $259.48) / $259.48 × 100
         = -47.12%

Verdict: OVERVALUED by 47%
```

---

### Value Bridge Summary (AAPL Moderate)

```
┌─────────────────────────────────────────────────────┐
│                 AAPL DCF Value Bridge               │
├─────────────────────────────────────────────────────┤
│                                                     │
│  PV of FCFs (Years 1-5)          $572.1B   (29.2%)  │
│  ──────────────────────────────────────────────     │
│  PV of Terminal Value          $1,388.0B   (70.8%)  │
│  ═══════════════════════════════════════════════    │
│  Enterprise Value              $1,960.1B            │
│  ──────────────────────────────────────────────     │
│  + Net Cash                       $54.3B            │
│  ═══════════════════════════════════════════════    │
│  Equity Value                  $2,014.3B            │
│  ÷ Shares Outstanding            14.68B             │
│  ═══════════════════════════════════════════════    │
│  Intrinsic Value per Share       $137.21            │
│                                                     │
│  Current Market Price            $259.48            │
│  Upside/Downside                 -47.12%            │
│  Verdict                       OVERVALUED           │
└─────────────────────────────────────────────────────┘
```

---

### Three Scenarios Comparison (AAPL)

| Scenario | Growth Y1 | Growth Y2 | WACC | Terminal Growth | FCF Margin | Intrinsic Value | Upside |
|----------|-----------|-----------|------|-----------------|------------|-----------------|--------|
| **Optimistic** | 18.4% | 19.7% | 9.02% | 3.0% | 30.31% | $237.73 | -8.4% |
| **Moderate** | 10.9% | 6.9% | 10.02% | 2.5% | 28.31% | $137.21 | -47.1% |
| **Pessimistic** | 3.9% | -2.7% | 11.02% | 2.0% | 26.31% | $86.56 | -66.6% |

**Interpretation:**
- Even in the **optimistic** scenario, AAPL appears overvalued by 8%
- In the **moderate** scenario, the stock is 47% above intrinsic value
- This suggests the current market price ($259.48) has very high growth expectations baked in

---

### Sensitivity Matrix (AAPL)

How intrinsic value changes with different WACC and terminal growth assumptions:

```
                         Terminal Growth Rate
                ┌─────────┬─────────┬─────────┬─────────┬─────────┐
                │  1.5%   │  2.0%   │  2.5%   │  3.0%   │  3.5%   │
        ┌───────┼─────────┼─────────┼─────────┼─────────┼─────────┤
        │  8.0% │ $163.15 │ $173.60 │ $185.95 │ $200.75 │ $218.83 │
        ├───────┼─────────┼─────────┼─────────┼─────────┼─────────┤
   W    │  9.0% │ $141.71 │ $149.20 │ $157.84 │ $167.92 │ $179.82 │
   A    ├───────┼─────────┼─────────┼─────────┼─────────┼─────────┤
   C    │ 10.0% │ $125.30 │ $130.88 │ $137.21 │ $144.43 │ $152.76 │ ← Base
   C    ├───────┼─────────┼─────────┼─────────┼─────────┼─────────┤
        │ 11.0% │ $112.35 │ $116.63 │ $121.42 │ $126.80 │ $132.90 │
        ├───────┼─────────┼─────────┼─────────┼─────────┼─────────┤
        │ 12.0% │ $101.86 │ $105.23 │ $108.95 │ $113.08 │ $117.70 │
        └───────┴─────────┴─────────┴─────────┴─────────┴─────────┘
                                       ↑
                                     Base

Current Price: $259.48
Even at the most aggressive assumptions (8% WACC, 3.5% terminal growth),
intrinsic value is only $218.83 — still 16% below market price.
```

---

### Key Takeaways from AAPL Example

1. **Terminal Value Dominance**: 70.8% of AAPL's enterprise value comes from terminal value, meaning the valuation is highly sensitive to long-term assumptions

2. **Growth Fade**: Revenue growth fades from 10.9% (Year 1) → 2.5% (Year 5), reflecting the assumption that growth normalizes over time

3. **Strong FCF Margin**: Apple's 28.31% FCF margin is exceptionally high, reflecting its premium pricing power and efficient operations

4. **Market Premium**: At $259.48, the market is pricing in growth expectations that exceed even the optimistic analyst forecasts

5. **Sensitivity Analysis**: Even with the most favorable assumptions (WACC 8%, terminal growth 3.5%), the intrinsic value only reaches $218.83 — suggesting the stock may be pricing in expectations beyond what DCF can justify

---

## References

- **Aswath Damodaran** - NYU Stern School of Business DCF frameworks
- **McKinsey Valuation** - Corporate valuation standards
- **CFA Institute** - Equity valuation methodologies
