"""
DCF (Discounted Cash Flow) valuation calculator.

Reads scraped statistics and forecast JSON data, projects free cash flows
for 5 years, computes terminal value via Gordon Growth, and derives an
intrinsic value per share.

Produces three scenarios:
  - Optimistic:   high analyst growth, WACC -1%, terminal growth 3%, FCF margin +2pp
  - Moderate:     average analyst growth, base WACC, terminal growth 2.5%, base FCF margin
  - Pessimistic:  low analyst growth, WACC +1%, terminal growth 2%, FCF margin -2pp

Every stock that has both statistics and forecast data can be valued.
"""

import json
import glob
from datetime import date
from pathlib import Path

from src import DATA_DIR

# ── Constants ──────────────────────────────────────────────────────────────
RISK_FREE_RATE = 0.0425       # 4.25 %
EQUITY_RISK_PREMIUM = 0.055   # 5.50 %
TERMINAL_GROWTH = 0.025       # 2.50 %
PROJECTION_YEARS = 5
MIN_WACC = 0.04               # floor
MIN_WACC_GROWTH_SPREAD = 0.02 # WACC must exceed terminal growth by at least this

# Scenario offsets
SCENARIO_PARAMS = {
    "optimistic": {
        "wacc_offset": -0.01,
        "terminal_growth": 0.03,
        "fcf_margin_offset": 2.0,   # percentage points
    },
    "moderate": {
        "wacc_offset": 0.0,
        "terminal_growth": 0.025,
        "fcf_margin_offset": 0.0,
    },
    "pessimistic": {
        "wacc_offset": 0.01,
        "terminal_growth": 0.02,
        "fcf_margin_offset": -2.0,
    },
}


class DCFCalculator:
    """Compute a DCF valuation for any ticker with statistics + forecast data."""

    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR

    # ── Data loaders ───────────────────────────────────────────────────────

    def _load_statistics(self, ticker: str) -> dict | None:
        pattern = str(self.data_dir / "statistics" / f"{ticker.upper()}_statistics_*.json")
        files = sorted(glob.glob(pattern), reverse=True)
        if not files:
            return None
        with open(files[0], "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_forecast(self, ticker: str) -> dict | None:
        pattern = str(self.data_dir / "forecast" / "detail" / f"{ticker.upper()}_forecast_*.json")
        files = sorted(glob.glob(pattern), reverse=True)
        if not files:
            return None
        with open(files[0], "r", encoding="utf-8") as f:
            return json.load(f)

    # ── Extract helpers ────────────────────────────────────────────────────

    @staticmethod
    def _extract_stat_value(stats: dict, section_name: str, label: str):
        """Return the numeric *value* field from a statistics section/item."""
        for section in stats.get("sections", []):
            if section["name"] == section_name:
                for item in section.get("items", []):
                    if item["label"] == label:
                        return item.get("value")
        return None

    # ── Build DCF inputs ───────────────────────────────────────────────────

    def _build_inputs(self, ticker: str) -> dict | None:
        stats = self._load_statistics(ticker)
        if stats is None:
            return None

        forecast = self._load_forecast(ticker)

        ev = self._extract_stat_value
        inputs: dict = {"ticker": ticker.upper(), "data_date": stats.get("date")}
        warnings: list[str] = []

        # ── FCF & revenue from statistics ──────────────────────────────────
        inputs["fcf"] = ev(stats, "Cash Flow", "Free Cash Flow")
        inputs["revenue"] = ev(stats, "Income Statement", "Revenue")
        inputs["fcf_margin"] = ev(stats, "Margins", "FCF Margin")  # percentage
        inputs["shares_outstanding"] = ev(stats, "Share Statistics", "Shares Outstanding")
        inputs["market_cap"] = ev(stats, "Total Valuation", "Market Cap")

        # ── Net cash from balance sheet ────────────────────────────────────
        cash = ev(stats, "Balance Sheet", "Cash & Marketable Securities")
        debt = ev(stats, "Balance Sheet", "Total Debt")
        net_cash = ev(stats, "Balance Sheet", "Net Cash")
        inputs["cash"] = cash
        inputs["total_debt"] = debt
        inputs["net_cash"] = net_cash if net_cash is not None else (
            (cash or 0) - (debt or 0)
        )

        # ── WACC ───────────────────────────────────────────────────────────
        wacc_pct = ev(stats, "Financial Efficiency", "Weighted Average Cost of Capital (WACC)")
        beta = ev(stats, "Stock Price Statistics", "Beta (5Y)")
        if wacc_pct is not None:
            inputs["wacc"] = max(wacc_pct / 100.0, MIN_WACC)
            inputs["wacc_source"] = "statistics"
        elif beta is not None:
            capm = RISK_FREE_RATE + beta * EQUITY_RISK_PREMIUM
            inputs["wacc"] = max(capm, MIN_WACC)
            inputs["wacc_source"] = "CAPM"
            warnings.append(f"WACC unavailable; estimated via CAPM (Rf={RISK_FREE_RATE:.2%} + Beta={beta:.2f} x ERP={EQUITY_RISK_PREMIUM:.2%})")
        else:
            inputs["wacc"] = RISK_FREE_RATE + EQUITY_RISK_PREMIUM
            inputs["wacc_source"] = "default"
            warnings.append("WACC and Beta unavailable; using default discount rate")

        inputs["beta"] = beta
        inputs["tax_rate"] = ev(stats, "Taxes", "Effective Tax Rate")

        # ── Current price ──────────────────────────────────────────────────
        mc = inputs["market_cap"]
        so = inputs["shares_outstanding"]
        if mc and so and so > 0:
            inputs["current_price"] = mc / so
        else:
            inputs["current_price"] = None

        # ── Growth rates from forecast (avg / high / low) ─────────────────
        growth_avg_yr1 = None
        growth_avg_yr2 = None
        growth_high_yr1 = None
        growth_high_yr2 = None
        growth_low_yr1 = None
        growth_low_yr2 = None

        if forecast and forecast.get("annual"):
            annual = forecast["annual"]
            growth_avg_yr1 = annual.get("current_revenue_growth")
            growth_avg_yr2 = annual.get("next_year_revenue_growth")

        # Forecast ranges for high/low/avg fallback
        if forecast and forecast.get("forecast_ranges"):
            rg = forecast["forecast_ranges"].get("revenue_growth", {})
            avg_list = rg.get("avg", [])
            high_list = rg.get("high", [])
            low_list = rg.get("low", [])

            if growth_avg_yr1 is None and avg_list and avg_list[0] is not None:
                growth_avg_yr1 = avg_list[0]
            if growth_avg_yr2 is None and len(avg_list) > 1 and avg_list[1] is not None:
                growth_avg_yr2 = avg_list[1]

            if high_list and high_list[0] is not None:
                growth_high_yr1 = high_list[0]
            if len(high_list) > 1 and high_list[1] is not None:
                growth_high_yr2 = high_list[1]

            if low_list and low_list[0] is not None:
                growth_low_yr1 = low_list[0]
            if len(low_list) > 1 and low_list[1] is not None:
                growth_low_yr2 = low_list[1]

        # Fallback: 5Y revenue growth from statistics
        rev_5y = ev(stats, "Analyst Forecast", "Revenue Growth Forecast (5Y)")
        if growth_avg_yr1 is None and rev_5y is not None:
            growth_avg_yr1 = rev_5y
            warnings.append("Using 5Y revenue growth forecast as year-1 proxy")

        # Fill missing yr2 with yr1
        if growth_avg_yr2 is None:
            growth_avg_yr2 = growth_avg_yr1
        if growth_high_yr2 is None:
            growth_high_yr2 = growth_high_yr1
        if growth_low_yr2 is None:
            growth_low_yr2 = growth_low_yr1

        # Derive high/low from avg if ranges unavailable
        if growth_high_yr1 is None and growth_avg_yr1 is not None:
            growth_high_yr1 = growth_avg_yr1 * 1.5 if growth_avg_yr1 > 0 else growth_avg_yr1 * 0.5
            growth_high_yr2 = growth_avg_yr2 * 1.5 if growth_avg_yr2 and growth_avg_yr2 > 0 else (growth_avg_yr2 or 0) * 0.5
        if growth_low_yr1 is None and growth_avg_yr1 is not None:
            growth_low_yr1 = growth_avg_yr1 * 0.5 if growth_avg_yr1 > 0 else growth_avg_yr1 * 1.5
            growth_low_yr2 = growth_avg_yr2 * 0.5 if growth_avg_yr2 and growth_avg_yr2 > 0 else (growth_avg_yr2 or 0) * 1.5

        # Edge case: no growth at all
        if growth_avg_yr1 is None:
            growth_avg_yr1 = TERMINAL_GROWTH * 100
            growth_avg_yr2 = TERMINAL_GROWTH * 100
            growth_high_yr1 = TERMINAL_GROWTH * 100 * 1.5
            growth_high_yr2 = TERMINAL_GROWTH * 100 * 1.5
            growth_low_yr1 = TERMINAL_GROWTH * 100 * 0.5
            growth_low_yr2 = TERMINAL_GROWTH * 100 * 0.5
            warnings.append("No analyst revenue growth available; defaulting to terminal growth rate")

        inputs["growth_avg_yr1"] = growth_avg_yr1 / 100.0
        inputs["growth_avg_yr2"] = growth_avg_yr2 / 100.0
        inputs["growth_high_yr1"] = growth_high_yr1 / 100.0
        inputs["growth_high_yr2"] = growth_high_yr2 / 100.0
        inputs["growth_low_yr1"] = growth_low_yr1 / 100.0
        inputs["growth_low_yr2"] = growth_low_yr2 / 100.0
        inputs["rev_growth_5y"] = rev_5y / 100.0 if rev_5y is not None else None

        # ── Edge-case warnings ─────────────────────────────────────────────
        if inputs["fcf"] is not None and inputs["fcf"] < 0:
            warnings.append("DCF may not be meaningful for negative FCF companies")

        inputs["warnings"] = warnings
        return inputs

    # ── Project FCFs ───────────────────────────────────────────────────────

    @staticmethod
    def _project_fcfs(revenue: float, fcf_margin: float, g1: float, g2: float,
                      terminal_growth: float) -> list[dict] | None:
        if revenue is None or revenue == 0:
            return None

        projections = []
        prev_revenue = revenue

        for yr in range(1, PROJECTION_YEARS + 1):
            if yr == 1:
                g = g1
            elif yr == 2:
                g = g2
            else:
                fade_fraction = (yr - 2) / (PROJECTION_YEARS - 2)
                g = g2 + (terminal_growth - g2) * fade_fraction

            proj_revenue = prev_revenue * (1 + g)
            proj_fcf = proj_revenue * fcf_margin

            projections.append({
                "year": yr,
                "revenue": proj_revenue,
                "growth_rate": g,
                "fcf_margin": fcf_margin,
                "fcf": proj_fcf,
            })
            prev_revenue = proj_revenue

        return projections

    # ── Terminal value ─────────────────────────────────────────────────────

    @staticmethod
    def _compute_terminal_value(final_fcf: float, wacc: float, terminal_growth: float) -> float:
        effective_wacc = max(wacc, terminal_growth + MIN_WACC_GROWTH_SPREAD)
        return final_fcf * (1 + terminal_growth) / (effective_wacc - terminal_growth)

    # ── Sensitivity matrix ─────────────────────────────────────────────────

    @staticmethod
    def _compute_sensitivity(wacc: float, terminal_growth: float, shares: float,
                             net_cash: float, projections: list[dict]) -> dict:
        wacc_offsets = [-0.02, -0.01, 0, 0.01, 0.02]
        tg_values = [0.015, 0.02, 0.025, 0.03, 0.035]

        wacc_labels = [f"{(wacc + o) * 100:.1f}%" for o in wacc_offsets]
        tg_labels = [f"{tg * 100:.1f}%" for tg in tg_values]

        matrix = []
        for wacc_offset in wacc_offsets:
            row = []
            w = wacc + wacc_offset
            for tg in tg_values:
                effective_w = max(w, tg + MIN_WACC_GROWTH_SPREAD)
                pv_fcfs = sum(
                    p["fcf"] / (1 + effective_w) ** p["year"]
                    for p in projections
                )
                tv = projections[-1]["fcf"] * (1 + tg) / (effective_w - tg)
                pv_tv = tv / (1 + effective_w) ** PROJECTION_YEARS
                ev = pv_fcfs + pv_tv
                equity = ev + net_cash
                per_share = equity / shares if shares else 0
                row.append(round(per_share, 2))
            matrix.append(row)

        return {
            "wacc_labels": wacc_labels,
            "tg_labels": tg_labels,
            "matrix": matrix,
            "base_wacc_idx": wacc_offsets.index(0),
            "base_tg_idx": tg_values.index(terminal_growth) if terminal_growth in tg_values else 2,
        }

    # ── Single scenario computation ────────────────────────────────────────

    def _compute_scenario(self, inputs: dict, scenario: str) -> dict | None:
        params = SCENARIO_PARAMS[scenario]
        warnings = list(inputs.get("warnings", []))

        wacc = max(inputs["wacc"] + params["wacc_offset"], MIN_WACC)
        tg = params["terminal_growth"]

        # Pick growth rates per scenario
        if scenario == "optimistic":
            g1 = inputs["growth_high_yr1"]
            g2 = inputs["growth_high_yr2"]
        elif scenario == "pessimistic":
            g1 = inputs["growth_low_yr1"]
            g2 = inputs["growth_low_yr2"]
        else:
            g1 = inputs["growth_avg_yr1"]
            g2 = inputs["growth_avg_yr2"]

        # FCF margin with offset
        base_margin_pct = inputs.get("fcf_margin") or 0
        if base_margin_pct <= 0 and inputs.get("fcf") and inputs.get("revenue") and inputs["revenue"] != 0:
            base_margin_pct = inputs["fcf"] / inputs["revenue"] * 100.0
        adjusted_margin_pct = max(base_margin_pct + params["fcf_margin_offset"], 1.0)
        fcf_margin = adjusted_margin_pct / 100.0

        revenue = inputs.get("revenue")
        shares = inputs["shares_outstanding"]
        net_cash = inputs.get("net_cash", 0) or 0

        if not shares or shares <= 0:
            return None

        projections = self._project_fcfs(revenue, fcf_margin, g1, g2, tg)
        if projections is None:
            return None

        # Discount projected FCFs
        effective_wacc = max(wacc, tg + MIN_WACC_GROWTH_SPREAD)
        pv_fcfs = []
        for p in projections:
            df = 1 / (1 + effective_wacc) ** p["year"]
            pv = p["fcf"] * df
            pv_fcfs.append(pv)
            p["discount_factor"] = round(df, 4)
            p["present_value"] = pv

        total_pv_fcfs = sum(pv_fcfs)

        # Terminal value
        terminal_value = self._compute_terminal_value(projections[-1]["fcf"], wacc, tg)
        pv_terminal = terminal_value / (1 + effective_wacc) ** PROJECTION_YEARS

        # Enterprise & equity value
        enterprise_value = total_pv_fcfs + pv_terminal
        equity_value = enterprise_value + net_cash
        intrinsic_per_share = equity_value / shares

        # Terminal value as % of EV
        tv_pct = (pv_terminal / enterprise_value * 100) if enterprise_value != 0 else 0

        scenario_warnings = list(warnings)
        if tv_pct > 85:
            scenario_warnings.append(f"Terminal value represents {tv_pct:.0f}% of enterprise value — high reliance on terminal assumptions")
        if effective_wacc != wacc:
            scenario_warnings.append(f"WACC adjusted from {wacc:.2%} to {effective_wacc:.2%} to maintain minimum spread over terminal growth")

        # Upside / verdict
        current_price = inputs.get("current_price")
        upside = None
        margin_of_safety = None
        verdict = None
        if current_price and current_price > 0:
            upside = (intrinsic_per_share / current_price - 1) * 100
            margin_of_safety = upside
            verdict = "Undervalued" if upside > 0 else "Overvalued"

        # Sensitivity (only for moderate scenario to keep output manageable)
        sensitivity = None
        if scenario == "moderate":
            sensitivity = self._compute_sensitivity(wacc, tg, shares, net_cash, projections)

        # Round projections for output
        for p in projections:
            p["revenue"] = round(p["revenue"], 0)
            p["fcf"] = round(p["fcf"], 0)
            p["growth_rate"] = round(p["growth_rate"] * 100, 2)
            p["fcf_margin"] = round(p["fcf_margin"] * 100, 2)
            p["present_value"] = round(p["present_value"], 0)

        # Value bridge components
        value_bridge = [
            {"label": "PV of FCFs", "value": round(total_pv_fcfs, 0)},
            {"label": "PV of Terminal Value", "value": round(pv_terminal, 0)},
            {"label": "Net Cash", "value": round(net_cash, 0)},
            {"label": "Equity Value", "value": round(equity_value, 0)},
        ]

        return {
            "intrinsic_value": round(intrinsic_per_share, 2),
            "upside_pct": round(upside, 2) if upside is not None else None,
            "margin_of_safety": round(margin_of_safety, 2) if margin_of_safety is not None else None,
            "verdict": verdict,
            "assumptions": {
                "wacc": round(inputs["wacc"] * 100, 2),
                "effective_wacc": round(effective_wacc * 100, 2),
                "wacc_source": inputs.get("wacc_source"),
                "terminal_growth": round(tg * 100, 2),
                "risk_free_rate": round(RISK_FREE_RATE * 100, 2),
                "equity_risk_premium": round(EQUITY_RISK_PREMIUM * 100, 2),
                "beta": inputs.get("beta"),
                "tax_rate": inputs.get("tax_rate"),
                "fcf_margin": round(adjusted_margin_pct, 2),
                "growth_yr1": round(g1 * 100, 2),
                "growth_yr2": round(g2 * 100, 2),
                "projection_years": PROJECTION_YEARS,
            },
            "summary": {
                "base_fcf": inputs["fcf"],
                "base_revenue": inputs["revenue"],
                "shares_outstanding": shares,
                "enterprise_value": round(enterprise_value, 0),
                "equity_value": round(equity_value, 0),
                "terminal_value": round(terminal_value, 0),
                "pv_terminal": round(pv_terminal, 0),
                "total_pv_fcfs": round(total_pv_fcfs, 0),
                "terminal_value_pct": round(tv_pct, 1),
                "cash": inputs.get("cash"),
                "total_debt": inputs.get("total_debt"),
                "net_cash": round(net_cash, 0),
                "market_cap": inputs.get("market_cap"),
            },
            "projections": projections,
            "value_bridge": value_bridge,
            "sensitivity": sensitivity,
            "warnings": scenario_warnings,
        }

    # ── Main compute ───────────────────────────────────────────────────────

    def compute(self, ticker: str) -> dict | None:
        inputs = self._build_inputs(ticker)
        if inputs is None:
            return None

        scenarios = {}
        for name in ("optimistic", "moderate", "pessimistic"):
            result = self._compute_scenario(inputs, name)
            if result is None:
                return None
            scenarios[name] = result

        current_price = inputs.get("current_price")

        return {
            "ticker": inputs["ticker"],
            "data_date": inputs["data_date"],
            "computed_date": str(date.today()),
            "current_price": round(current_price, 2) if current_price else None,
            "scenarios": scenarios,
        }

    # ── Compute and persist ────────────────────────────────────────────────

    def compute_and_save(self, ticker: str, force: bool = False) -> dict | None:
        out_dir = self.data_dir / "dcf"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ticker.upper()}_dcf_{date.today()}.json"

        if out_path.exists() and not force:
            with open(out_path, "r", encoding="utf-8") as f:
                return json.load(f)

        result = self.compute(ticker)
        if result is None:
            return None

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        return result
