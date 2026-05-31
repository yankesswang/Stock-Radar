"""
Industry comparison endpoint.

GET /api/industry-compare          → list of all groups and their tickers
GET /api/industry-compare?group=X  → per-group metrics for all tickers in that group

Valuation ratios come from the scraped statistics JSON.
Analyst upside % is recalculated using the latest current_price stored in the
Valuation table, so it stays accurate as prices move between scrape runs.
"""
import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Industry, Stock, Valuation

# Ensure project root is on sys.path so src.config can be imported
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import STOCK_LIST  # noqa: E402

router = APIRouter()

STATS_DIR: Path    = settings.DATA_DIR / "statistics"
FORECAST_DIR: Path = settings.DATA_DIR / "forecast" / "detail"


def _latest(directory: Path, ticker: str, suffix: str) -> Optional[Path]:
    for t in (ticker.upper(), ticker.lower()):
        files = sorted(directory.glob(f"{t}_{suffix}_*.json"), reverse=True)
        if files:
            return files[0]
    return None


def _flatten_stats(sections: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sec in sections:
        for item in sec.get("items", []):
            label = item.get("label")
            if label:
                out[label] = {"value": item.get("value"), "display": item.get("display")}
    return out


def _num(flat: dict, label: str) -> Optional[float]:
    item = flat.get(label)
    if item is None:
        return None
    return item.get("value")


def _str_val(flat: dict, label: str) -> Optional[str]:
    item = flat.get(label)
    if item is None:
        return None
    return item.get("display") or (str(item["value"]) if item.get("value") is not None else None)


def _ticker_metrics(ticker: str, db: Session) -> dict[str, Any]:
    t = ticker.upper()

    # ── Statistics JSON ───────────────────────────────────────────────────────
    stats_flat: dict[str, Any] = {}
    stats_path = _latest(STATS_DIR, t, "statistics")
    if stats_path:
        with open(stats_path, encoding="utf-8") as f:
            stats_flat = _flatten_stats(json.load(f).get("sections", []))

    # ── Forecast JSON ─────────────────────────────────────────────────────────
    annual: dict = {}
    price_target: dict = {}
    forecast_path = _latest(FORECAST_DIR, t, "forecast")
    if forecast_path:
        with open(forecast_path, encoding="utf-8") as f:
            fdata = json.load(f)
            annual       = fdata.get("annual", {})
            price_target = fdata.get("price_target", {})

    # ── Latest current_price from Valuation table ─────────────────────────────
    stock = db.query(Stock).filter(func.upper(Stock.ticker) == t).first()
    company = stock.company_name if stock else t

    current_price: Optional[float] = None
    if stock:
        val = (
            db.query(Valuation)
            .filter_by(stock_id=stock.id)
            .order_by(Valuation.snapshot_date.desc())
            .first()
        )
        if val and val.current_price is not None:
            current_price = val.current_price  # type: ignore[assignment]

    # Recalculate upside % with the latest stored price
    avg_target = price_target.get("average")
    if current_price and avg_target and current_price > 0:
        upside: Optional[float] = (avg_target - current_price) / current_price * 100
    else:
        upside = price_target.get("average_change_pct")

    def v(label: str) -> Optional[float]:
        return _num(stats_flat, label)

    return {
        "ticker": t,
        "company": company,
        "valuation": {
            "fwd_pe":    v("Forward PE"),
            "pe":        v("PE Ratio"),
            "ev_ebitda": v("EV / EBITDA"),
            "peg":       v("PEG Ratio"),
            "pb":        v("PB Ratio"),
            "ps":        v("P/S Ratio"),
            "pfcf":      v("P/FCF Ratio"),
        },
        "growth": {
            "eps_fy":   annual.get("current_growth"),
            "eps_nfy":  annual.get("next_year_growth"),
            "rev_fy":   annual.get("current_revenue_growth"),
            "rev_nfy":  annual.get("next_year_revenue_growth"),
            "eps_5y":   v("EPS Growth Forecast (5Y)"),
            "rev_5y":   v("Revenue Growth Forecast (5Y)"),
        },
        "profitability": {
            "gross_margin": v("Gross Margin"),
            "op_margin":    v("Operating Margin"),
            "net_margin":   v("Profit Margin"),
            "fcf_margin":   v("FCF Margin"),
            "roe":          v("Return on Equity (ROE)"),
            "roic":         v("Return on Invested Capital (ROIC)"),
            "roa":          v("Return on Assets (ROA)"),
        },
        "analyst": {
            "target":      avg_target,
            "upside":      upside,
            "target_high": price_target.get("high"),
            "target_low":  price_target.get("low"),
            "consensus":   _str_val(stats_flat, "Analyst Consensus"),
            "beta":        v("Beta (5Y)"),
            "return_52w":  v("52-Week Price Change"),
        },
    }


@router.get("")
def industry_compare(
    group: Optional[str] = Query(None),
    db:    Session        = Depends(get_db),
):
    def db_tickers_for_group(group_name: str) -> list[str]:
        rows = (
            db.query(Stock.ticker)
            .join(Industry, Stock.industry_id == Industry.id)
            .filter(Industry.name == group_name)
            .order_by(Stock.ticker)
            .all()
        )
        return [ticker for (ticker,) in rows]

    if group is None:
        group_map: dict[str, set[str]] = {
            name: {t.upper() for t in tickers}
            for name, tickers in STOCK_LIST.items()
        }
        rows = (
            db.query(Industry.name, Stock.ticker)
            .join(Stock, Stock.industry_id == Industry.id)
            .order_by(Industry.name, Stock.ticker)
            .all()
        )
        for name, ticker in rows:
            group_map.setdefault(name, set()).add(ticker.upper())

        groups = [
            {"name": name, "tickers": sorted(tickers)}
            for name, tickers in sorted(group_map.items(), key=lambda item: item[0])
        ]
        return {"groups": groups}

    configured_tickers = {t.upper() for t in STOCK_LIST.get(group, set())}
    db_tickers = set(db_tickers_for_group(group))
    group_tickers = sorted(configured_tickers | db_tickers)

    if not group_tickers:
        raise HTTPException(status_code=404, detail=f"Group '{group}' not found")

    result = [_ticker_metrics(ticker, db) for ticker in group_tickers]
    return {"group": group, "tickers": result}
