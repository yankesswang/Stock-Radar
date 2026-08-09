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
import yfinance as yf

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


def _year_label(years: list, idx: int, base: str, fallback: str) -> str:
    """'EPS 2027' when the fiscal year is known, else the generic label."""
    if idx < len(years) and years[idx]:
        return f"{base} {years[idx]}"
    return fallback


def _fill_sources(fdata: dict) -> dict:
    """
    Per-(field, year) provenance for cells StockAnalysis left Pro-locked.

    Keyed 'eps:2027' -> note text, so a valuation isn't built on a number
    whose origin — or thin analyst coverage — is invisible in the UI.
    """
    out: dict = {}
    for fill in (fdata.get("fallback") or {}).get("filled", []):
        source = fill.get("source_fiscal_end", "")
        origin = "Yahoo" if "yahoo" in str(source).lower() else "Nasdaq"
        if fill.get("method") == "raw":
            note = (f"⚠ {origin} 原始值，未校準口徑 — 無重疊年度可對齊，"
                    f"與 StockAnalysis 的 EPS 定義可能不同，勿直接乘上 PE median")
        else:
            note = f"{origin} 補值（StockAnalysis Pro 鎖住），已按成長率校準到 StockAnalysis 基準"
        if fill.get("thin_coverage"):
            note += f"　⚠ 僅 {fill.get('num_estimates')} 位分析師"
        out[f"{fill.get('field')}:{fill.get('year')}"] = note
    return out


def _with_source(metric: dict, fill_sources: dict, field: str, years: list, idx: int) -> dict:
    """Append the fallback provenance to a metric's hint when it was filled."""
    if idx < len(years):
        note = fill_sources.get(f"{field}:{years[idx]}")
        if note:
            metric = {**metric, "hint": f"{metric.get('hint', '')}　·　{note}".strip("　·　")}
    return metric


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
    eps_years: list = []
    rev_years: list = []
    fill_sources: dict = {}
    forecast_path = _latest(FORECAST_DIR, t, "forecast")
    if forecast_path:
        with open(forecast_path, encoding="utf-8") as f:
            fdata = json.load(f)
            annual = fdata.get("annual", {})
            price_target = fdata.get("price_target", {})
            ranges = fdata.get("forecast_ranges") or {}
            # "Next year" means FY2027 for a December filer but FY2028 for
            # NVDA, so label the columns with the actual fiscal years. Index 0
            # is the current year and 1 the next — the same positions
            # _populate_legacy_annual reads when it fills the annual dict.
            eps_years = (ranges.get("eps") or {}).get("years") or []
            rev_years = (ranges.get("revenue") or {}).get("years") or []
            fill_sources = _fill_sources(fdata)

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

    # ── Live PE from yfinance (always current, overrides stale stats file) ────
    yf_ticker = yf.Ticker(t)
    yf_info: dict = {}
    fwd_pe_live: float | None = None
    trail_pe_live: float | None = None
    try:
        yf_info = yf_ticker.info or {}
        price = yf_info.get("currentPrice") or yf_info.get("regularMarketPrice")
        # Forward PE = current price / current fiscal year EPS estimate ("0y")
        # yfinance's forwardPE uses next-FY EPS (+1y) which overstates future growth
        est = yf_ticker.earnings_estimate
        if est is not None and not est.empty and price:
            row = est[est.index == "0y"]
            if not row.empty:
                eps_0y = row["avg"].iloc[0]
                if eps_0y and eps_0y > 0:
                    fwd_pe_live = round(price / eps_0y, 2)
        # Fallback to yfinance forwardPE if estimate unavailable
        if fwd_pe_live is None:
            v = yf_info.get("forwardPE")
            if v:
                fwd_pe_live = round(v, 2)
        # Trailing PE always from info
        v = yf_info.get("trailingPE")
        if v:
            trail_pe_live = round(v, 2)
    except Exception:
        pass

    def _live_pe(live_val: float | None, label: str, fallback_label: str, hint: str) -> dict:
        if live_val is not None:
            return {"label": label, "value": live_val, "display": f"{live_val:.2f}", "hint": hint}
        return {**_metric(stats_flat, fallback_label), "label": label, "hint": hint}

    def s(label: str) -> dict:
        return _metric(stats_flat, label)

    # ── Assemble metric groups ────────────────────────────────────────────────
    groups = [
        {
            "name": "Valuation",
            "color": "cyan",
            "metrics": [
                _live_pe(fwd_pe_live,   "Forward PE", "Forward PE", "Forward price-to-earnings — price ÷ current-FY EPS estimate"),
                _live_pe(trail_pe_live, "PE (TTM)",  "PE Ratio",   "Trailing-twelve-months P/E"),
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
                _with_source({
                    "label": _year_label(eps_years, 0, "EPS Growth", "EPS Growth (FY est)"),
                    "value": annual.get("current_growth"),
                    "display": _pct(annual.get("current_growth")),
                    "hint": "Current fiscal year EPS growth estimate",
                }, fill_sources, "eps", eps_years, 0),
                _with_source({
                    "label": _year_label(eps_years, 1, "EPS", "EPS Next Year"),
                    "value": annual.get("next_year_eps"),
                    "display": _dollar(annual.get("next_year_eps")),
                    "sub": _pct(annual.get("next_year_growth")) if annual.get("next_year_growth") is not None else None,
                    "hint": "Next fiscal year EPS estimate · sub-line shows YoY growth %",
                }, fill_sources, "eps", eps_years, 1),
                _with_source({
                    "label": _year_label(rev_years, 0, "Rev Growth", "Rev Growth (FY est)"),
                    "value": annual.get("current_revenue_growth"),
                    "display": _pct(annual.get("current_revenue_growth")),
                    "hint": "Current fiscal year revenue growth estimate",
                }, fill_sources, "revenue", rev_years, 0),
                _with_source({
                    "label": _year_label(rev_years, 1, "Rev Growth", "Rev Growth (Next FY)"),
                    "value": annual.get("next_year_revenue_growth"),
                    "display": _pct(annual.get("next_year_revenue_growth")),
                    "hint": "Next fiscal year revenue growth estimate",
                }, fill_sources, "revenue", rev_years, 1),
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
