"""
Aggregated key-metrics summary for a single ticker.

Pulls from:
  • statistics JSON  →  valuation ratios, margins, health, efficiency
  • forecast JSON    →  EPS/revenue growth estimates, analyst price target
  • PE JSON          →  historical PE median summary
  • valuation DB     →  pe_median_5y, eps_growth_5y, revenue_growth_5y
"""
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Stock, Valuation

router = APIRouter()

STATS_DIR: Path = settings.DATA_DIR / "statistics"
FORECAST_DIR: Path = settings.DATA_DIR / "forecast" / "detail"
PE_DIR: Path = settings.DATA_DIR / "pe" / "fullratio"


def _latest(directory: Path, ticker: str, suffix: str) -> Path | None:
    for t in (ticker.upper(), ticker.lower()):
        files = sorted(directory.glob(f"{t}_{suffix}_*.json"), reverse=True)
        if files:
            return files[0]
    return None


def _flatten_stats(sections: list[dict]) -> dict[str, dict]:
    """Return {label: {value, display}} for every item across all sections."""
    out: dict[str, dict] = {}
    for sec in sections:
        for item in sec.get("items", []):
            label = item.get("label")
            if label:
                out[label] = {"value": item.get("value"), "display": item.get("display")}
    return out


def _metric(flat: dict, label: str) -> dict:
    item = flat.get(label)
    if item is None:
        return {"label": label, "value": None, "display": "—"}
    return {"label": label, "value": item["value"], "display": item.get("display") or "—"}


def _pct(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:+.2f}%"


def _dollar(val: float | None) -> str:
    if val is None:
        return "—"
    return f"${val:,.2f}"


@router.get("/{ticker}/summary")
def get_summary(ticker: str, db: Session = Depends(get_db)):
    t = ticker.upper()

    # ── Statistics JSON ──────────────────────────────────────────────────────
    stats_flat: dict[str, Any] = {}
    stats_path = _latest(STATS_DIR, t, "statistics")
    if stats_path:
        with open(stats_path, encoding="utf-8") as f:
            stats_flat = _flatten_stats(json.load(f).get("sections", []))

    # ── Forecast JSON ────────────────────────────────────────────────────────
    annual: dict = {}
    price_target: dict = {}
    forecast_path = _latest(FORECAST_DIR, t, "forecast")
    if forecast_path:
        with open(forecast_path, encoding="utf-8") as f:
            fdata = json.load(f)
            annual = fdata.get("annual", {})
            price_target = fdata.get("price_target", {})

    # ── PE JSON ──────────────────────────────────────────────────────────────
    pe_summary: dict = {}
    pe_median_hist: float | None = None
    pe_path = _latest(PE_DIR, t, "fullratio_pe")
    if pe_path:
        with open(pe_path, encoding="utf-8") as f:
            pe_data = json.load(f)
            pe_summary = pe_data.get("summary", {})
            pe_median_hist = pe_data.get("pe_median")

    # ── Valuation DB (latest snapshot) ───────────────────────────────────────
    stock = db.query(Stock).filter(func.upper(Stock.ticker) == t).first()
    val = None
    if stock:
        val = (
            db.query(Valuation)
            .filter_by(stock_id=stock.id)
            .order_by(Valuation.snapshot_date.desc())
            .first()
        )

    def s(label: str) -> dict:
        return _metric(stats_flat, label)

    # ── Assemble metric groups ────────────────────────────────────────────────
    groups = [
        {
            "name": "Valuation",
            "color": "cyan",
            "metrics": [
                {**s("Forward PE"), "hint": "Forward price-to-earnings (analyst estimate)"},
                {**s("PE Ratio"), "label": "PE (TTM)", "hint": "Trailing-twelve-months P/E"},
                {
                    "label": "PE Median (5Y)",
                    "value": val.pe_median_5y if val else None,
                    "display": str(val.pe_median_5y) if val and val.pe_median_5y else "—",
                    "hint": "Historical 5-year median PE used for fair-value calc",
                },
                {
                    "label": "PE Median (Hist)",
                    "value": pe_median_hist,
                    "display": str(pe_median_hist) if pe_median_hist else "—",
                    "hint": "Long-run historical median PE from fullratio.com",
                },
                {**s("PEG Ratio"), "hint": "PE ÷ growth — <1 may be undervalued"},
                {**s("P/FCF Ratio"), "label": "P/FCF", "hint": "Price to free cash flow"},
                {**s("EV / EBITDA"), "label": "EV/EBITDA", "hint": "Enterprise value multiple"},
                {**s("PB Ratio"), "label": "P/B", "hint": "Price to book value"},
            ],
        },
        {
            "name": "Growth",
            "color": "purple",
            "metrics": [
                {
                    "label": "EPS Growth (FY est)",
                    "value": annual.get("current_growth"),
                    "display": _pct(annual.get("current_growth")),
                    "hint": "Current fiscal year EPS growth estimate",
                },
                {
                    "label": "EPS Growth (Next FY)",
                    "value": annual.get("next_year_growth"),
                    "display": _pct(annual.get("next_year_growth")),
                    "hint": "Next fiscal year EPS growth estimate",
                },
                {
                    "label": "Rev Growth (FY est)",
                    "value": annual.get("current_revenue_growth"),
                    "display": _pct(annual.get("current_revenue_growth")),
                    "hint": "Current fiscal year revenue growth estimate",
                },
                {
                    "label": "Rev Growth (Next FY)",
                    "value": annual.get("next_year_revenue_growth"),
                    "display": _pct(annual.get("next_year_revenue_growth")),
                    "hint": "Next fiscal year revenue growth estimate",
                },
                {
                    **s("EPS Growth Forecast (5Y)"),
                    "label": "EPS Growth Fcst (5Y)",
                    "hint": "Analyst consensus 5-year EPS CAGR forecast",
                },
                {
                    **s("Revenue Growth Forecast (5Y)"),
                    "label": "Rev Growth Fcst (5Y)",
                    "hint": "Analyst consensus 5-year revenue CAGR forecast",
                },
                {
                    "label": "EPS Growth (5Y hist)",
                    "value": val.eps_growth_5y if val else None,
                    "display": f"{val.eps_growth_5y}%" if val and val.eps_growth_5y else "—",
                    "hint": "Historical 5-year EPS CAGR",
                },
                {
                    "label": "Rev Growth (5Y hist)",
                    "value": val.revenue_growth_5y if val else None,
                    "display": f"{val.revenue_growth_5y}%" if val and val.revenue_growth_5y else "—",
                    "hint": "Historical 5-year revenue CAGR",
                },
            ],
        },
        {
            "name": "Profitability",
            "color": "success",
            "metrics": [
                {**s("Gross Margin"), "hint": "Revenue minus cost of goods sold"},
                {**s("Operating Margin"), "hint": "Operating income / revenue"},
                {**s("Profit Margin"), "hint": "Net income / revenue"},
                {**s("FCF Margin"), "hint": "Free cash flow / revenue"},
                {**s("Return on Equity (ROE)"), "label": "ROE", "hint": "Net income / shareholders' equity"},
                {**s("Return on Invested Capital (ROIC)"), "label": "ROIC", "hint": "Return on invested capital"},
                {**s("Return on Assets (ROA)"), "label": "ROA", "hint": "Net income / total assets"},
                {**s("EBITDA Margin"), "hint": "EBITDA / revenue"},
            ],
        },
        {
            "name": "Financial Health",
            "color": "amber",
            "metrics": [
                {**s("Current Ratio"), "hint": ">1 means current assets exceed liabilities"},
                {**s("Quick Ratio"), "hint": "Liquid assets / current liabilities"},
                {**s("Debt / Equity"), "label": "D/E Ratio", "hint": "Financial leverage — lower is safer"},
                {**s("Debt / EBITDA"), "label": "D/EBITDA", "hint": "Years of EBITDA needed to repay debt"},
                {**s("Net Cash Per Share"), "label": "Net Cash/Share", "hint": "Cash minus total debt, per share"},
                {**s("Interest Coverage"), "hint": "EBIT / interest expense — higher is safer"},
                {**s("Altman Z-Score"), "hint": ">3 safe, 1.8–3 grey zone, <1.8 distress"},
                {**s("Piotroski F-Score"), "hint": "0–9 quality score, ≥7 is strong"},
            ],
        },
        {
            "name": "Analyst",
            "color": "danger",
            "metrics": [
                {
                    **s("Analyst Consensus"),
                    "label": "Consensus",
                    "hint": "Analyst rating consensus",
                },
                {
                    "label": "Price Target (avg)",
                    "value": price_target.get("average"),
                    "display": _dollar(price_target.get("average")),
                    "hint": "Average analyst price target",
                },
                {
                    "label": "Target Upside",
                    "value": price_target.get("average_change_pct"),
                    "display": _pct(price_target.get("average_change_pct")),
                    "hint": "% upside to the average price target",
                },
                {
                    "label": "Target (High)",
                    "value": price_target.get("high"),
                    "display": _dollar(price_target.get("high")),
                    "hint": "Highest analyst price target",
                },
                {
                    "label": "Target (Low)",
                    "value": price_target.get("low"),
                    "display": _dollar(price_target.get("low")),
                    "hint": "Lowest analyst price target",
                },
                {**s("Beta (5Y)"), "hint": "Volatility vs market — 1 = market-like"},
                {**s("52-Week Price Change"), "label": "52W Return", "hint": "Price change over past year"},
            ],
        },
    ]

    return {
        "ticker": t,
        "groups": groups,
        "pe_history_summary": pe_summary,
        "pe_median_hist": pe_median_hist,
    }
