"""
Portfolio management: CRUD for portfolios + holdings.
Each GET /portfolio/{id} enriches holdings with live prices (yfinance)
and valuation data from the DB.

Also provides GET /portfolio/gsheet which reads holdings directly from
a public Google Sheet (CSV export) and enriches with DB valuation data.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import io
import re
import ssl
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
import pandas as pd
import yfinance as yf

from ..config import settings
from ..database import get_db
from ..models import Portfolio, Holding, Stock, Industry, Valuation

router = APIRouter()

# ── Google Sheet config (ID loaded from backend/.env) ────────────────────────
_SKIP = {"CASH", "eToro"}


def _gsheet_url(gsheet_id: str, sheet_name: str | None = None) -> str:
    if not gsheet_id:
        raise ValueError("Sheet ID is not set. Add GSHEET_ID / GSHEET_MOM_ID to backend/.env")
    if sheet_name:
        return f"https://docs.google.com/spreadsheets/d/{gsheet_id}/export?format=csv&sheet={urllib.parse.quote(sheet_name)}"
    return f"https://docs.google.com/spreadsheets/d/{gsheet_id}/export?format=csv&gid=0"


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class PortfolioCreate(BaseModel):
    name: str
    description: str = ""


class PortfolioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class HoldingCreate(BaseModel):
    ticker: str
    shares: float
    avg_cost: float
    notes: str = ""


class HoldingUpdate(BaseModel):
    shares: float | None = None
    avg_cost: float | None = None
    notes: str | None = None


# ── Live price helpers ────────────────────────────────────────────────────────

def _fetch_price(ticker: str) -> float | None:
    try:
        hist = yf.Ticker(ticker.upper()).history(period="5d", auto_adjust=True)
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return None


def _fetch_prices(tickers: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_price, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            p = fut.result()
            if p is not None:
                prices[t.upper()] = p
    return prices


# ── Valuation lookup ──────────────────────────────────────────────────────────

def _get_valuations(tickers: list[str], db: Session) -> dict[str, dict]:
    """Return the latest valuation row keyed by ticker (upper-cased)."""
    if not tickers:
        return {}
    upper = [t.upper() for t in tickers]
    subq = (
        db.query(Valuation.stock_id, func.max(Valuation.snapshot_date).label("max_date"))
        .group_by(Valuation.stock_id)
        .subquery()
    )
    rows = (
        db.query(Stock, Industry, Valuation)
        .join(Industry, Stock.industry_id == Industry.id)
        .join(subq, Stock.id == subq.c.stock_id)
        .join(Valuation, (Valuation.stock_id == Stock.id) & (Valuation.snapshot_date == subq.c.max_date))
        .filter(Stock.ticker.in_(upper))
        .all()
    )
    result = {}
    for stock, industry, val in rows:
        result[stock.ticker.upper()] = {
            "company_name":            stock.company_name,
            "industry":                industry.name,
            "pe_median_price":         val.pe_median_price,
            "current_year_fair_price": val.current_year_fair_price,
            "next_year_fair_price":    val.next_year_fair_price,
            "valuation_status":        val.valuation_status,
            "valuation_diff_pct":      val.valuation_diff_pct,
            "pe_median_5y":            val.pe_median_5y,
            "estimated_pe":            val.estimated_pe,
        }
    return result


# ── Enrich holdings ───────────────────────────────────────────────────────────

def _enrich(holdings: list[Holding], db: Session) -> list[dict]:
    tickers = list({h.ticker.upper() for h in holdings})
    prices  = _fetch_prices(tickers)
    vals    = _get_valuations(tickers, db)

    rows = []
    for h in holdings:
        t        = h.ticker.upper()
        price    = prices.get(t)
        val      = vals.get(t, {})
        cost_val = h.shares * h.avg_cost
        mkt_val  = h.shares * price if price else None
        pnl      = mkt_val - cost_val if mkt_val is not None else None
        pnl_pct  = (pnl / cost_val * 100) if (pnl is not None and cost_val) else None

        # distance to PE fair value
        fair_pe  = val.get("pe_median_price")
        to_fair  = ((fair_pe - price) / price * 100) if (fair_pe and price) else None

        rows.append({
            "id":          h.id,
            "ticker":      t,
            "shares":      h.shares,
            "avg_cost":    h.avg_cost,
            "notes":       h.notes,
            "added_date":  str(h.added_date),
            # live data
            "current_price": price,
            "market_value":  round(mkt_val, 2) if mkt_val else None,
            "cost_basis":    round(cost_val, 2),
            "unrealized_pnl": round(pnl, 2) if pnl is not None else None,
            "return_pct":    round(pnl_pct, 2) if pnl_pct is not None else None,
            # valuation
            "company_name":            val.get("company_name", t),
            "industry":                val.get("industry"),
            "pe_median_price":         fair_pe,
            "current_year_fair_price": val.get("current_year_fair_price"),
            "next_year_fair_price":    val.get("next_year_fair_price"),
            "valuation_status":        val.get("valuation_status"),
            "valuation_diff_pct":      val.get("valuation_diff_pct"),
            "pe_median_5y":            val.get("pe_median_5y"),
            "pct_to_fair_pe":          round(to_fair, 1) if to_fair is not None else None,
        })
    return rows


def _portfolio_summary(enriched: list[dict]) -> dict:
    total_value = sum(r["market_value"] or 0 for r in enriched)
    total_cost  = sum(r["cost_basis"] for r in enriched)
    total_pnl   = total_value - total_cost
    return_pct  = (total_pnl / total_cost * 100) if total_cost else 0

    # sector allocation
    sectors: dict[str, float] = {}
    for r in enriched:
        ind = r.get("industry") or "Unknown"
        sectors[ind] = sectors.get(ind, 0) + (r["market_value"] or r["cost_basis"])

    return {
        "total_value":  round(total_value, 2),
        "total_cost":   round(total_cost,  2),
        "total_pnl":    round(total_pnl,   2),
        "return_pct":   round(return_pct,  2),
        "sectors":      [{"name": k, "value": round(v, 2)} for k, v in sorted(sectors.items(), key=lambda x: -x[1])],
    }


# ── Google Sheet loader ──────────────────────────────────────────────────────

def _load_gsheet(gsheet_id: str, sheet_name: str | None = None) -> list[dict]:
    """
    Download the public Google Sheet as CSV and parse holdings.
    Actual sheet column layout (row 0 = header, skipped):
      0: account marker ("etoro" / "firstrade") — may appear on same row as first ticker
      1: ticker (matches ^[A-Z]{1,5}$)
      2: current price (股價)
      4: shares (股數)
      8: position value (價值)
      9: P&L % (損益%)
      10: portfolio weight % (佔比)
    """
    import math

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    response = urllib.request.urlopen(_gsheet_url(gsheet_id, sheet_name), context=ctx, timeout=15)
    df = pd.read_csv(io.BytesIO(response.read()), header=None)

    positions: list[dict] = []
    acct = ""
    total_val = 0.0
    overall_return: float | None = None

    def _float(val, strip=""):
        """Parse a cell to float; return None for missing/nan/inf values."""
        try:
            s = str(val).replace(",", "").replace(strip, "").strip()
            if s.lower() in ("nan", "inf", "-inf", ""):
                return None
            result = float(s)
            return None if (math.isnan(result) or math.isinf(result)) else result
        except (ValueError, TypeError):
            return None

    for i in range(1, len(df)):
        row = df.iloc[i]
        c0 = str(row[0] if pd.notna(row[0]) else "").strip()
        c1 = str(row[1] if pd.notna(row[1]) else "").strip()

        # Account marker — may share a row with the first ticker (e.g. "firstrade" + "VRT")
        if "etoro" in c0.lower():
            acct = "eToro"
            continue          # eToro marker row never has a ticker
        if "firstrade" in c0.lower():
            acct = "Firstrade"
            # fall through — this row also contains the first Firstrade ticker

        if "績效" in c0 or "報酬率" in c1:
            if "報酬率" in c1:
                v = _float(row[2], "%")
                if v is not None:
                    overall_return = v
            continue

        # eToro aggregate row (no ticker, has value + weight)
        if acct == "eToro" and not c1:
            v = _float(row[8])
            p = _float(row[10], "%")
            if v and v > 0 and p and 0 < p < 50:
                positions.insert(0, {
                    "account": "eToro", "ticker": "eToro",
                    "price": None, "shares": None,
                    "value": v, "pnl_pct": None, "weight": p,
                })
            continue

        # Individual stock row
        if re.match(r"^[A-Z]{1,5}$", c1):
            positions.append({
                "account": acct or "Unknown",
                "ticker":  c1,
                "price":   _float(row[2]),
                "shares":  _float(row[4]),
                "value":   _float(row[8]) or 0.0,
                "pnl_pct": _float(row[9], "%"),
                "weight":  _float(row[10], "%"),
            })
            continue

        # Firstrade portfolio total row
        if acct == "Firstrade" and not c1 and not c0:
            v = _float(row[8])
            if v and 30_000 < v < 500_000:
                total_val = v

    if not total_val:
        total_val = sum(p["value"] for p in positions)

    return positions, round(total_val, 2), overall_return


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/gsheet")
def get_gsheet_portfolio(
    portfolio: str | None = Query(None),
    gsheet_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Read holdings from Google Sheet and enrich with DB valuation data.
    Optional ?portfolio=mom to read Mom's portfolio sheet.
    Optional ?gsheet_id=<ID> to read an arbitrary public Google Sheet directly.
    """
    if not gsheet_id:
        gsheet_id = settings.GSHEET_ID
        if not gsheet_id:
            raise HTTPException(status_code=502, detail="GSHEET_ID is not set in backend/.env")
    try:
        positions, total_val, overall_return = _load_gsheet(gsheet_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to load Google Sheet: {exc}")

    # Collect real tickers (skip eToro aggregate / CASH)
    real_tickers = [p["ticker"] for p in positions if p["ticker"] not in _SKIP]
    vals = _get_valuations(real_tickers, db)

    # Sector breakdown from DB; fall back to yfinance name
    sector_map: dict[str, float] = {}
    enriched = []
    for p in positions:
        t   = p["ticker"]
        val = vals.get(t.upper(), {})
        ind = val.get("industry") or ("eToro (Mixed)" if t == "eToro" else None)
        sector_map[ind or "Unknown"] = sector_map.get(ind or "Unknown", 0) + p["value"]

        enriched.append({
            **p,
            "company_name":            val.get("company_name", t),
            "industry":                ind,
            "pe_median_price":         val.get("pe_median_price"),
            "current_year_fair_price": val.get("current_year_fair_price"),
            "next_year_fair_price":    val.get("next_year_fair_price"),
            "valuation_status":        val.get("valuation_status"),
            "valuation_diff_pct":      val.get("valuation_diff_pct"),
            "pe_median_5y":            val.get("pe_median_5y"),
        })

    sectors = sorted(
        [{"name": k, "value": round(v, 2)} for k, v in sector_map.items()],
        key=lambda x: -x["value"],
    )

    return {
        "total_value":     total_val,
        "overall_return":  overall_return,
        "sectors":         sectors,
        "holdings":        enriched,
    }


@router.get("")
def list_portfolios(db: Session = Depends(get_db)):
    portfolios = db.query(Portfolio).order_by(Portfolio.created_at.desc()).all()
    result = []
    for p in portfolios:
        tickers = list({h.ticker.upper() for h in p.holdings})
        result.append({
            "id":            p.id,
            "name":          p.name,
            "description":   p.description,
            "created_at":    str(p.created_at)[:10],
            "holding_count": len(p.holdings),
            "tickers":       sorted(tickers),
        })
    return result


@router.post("", status_code=201)
def create_portfolio(body: PortfolioCreate, db: Session = Depends(get_db)):
    p = Portfolio(name=body.name.strip(), description=body.description.strip())
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "name": p.name, "description": p.description}


@router.get("/{pid}")
def get_portfolio(pid: int, db: Session = Depends(get_db)):
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    enriched = _enrich(p.holdings, db)
    summary  = _portfolio_summary(enriched)
    return {
        "id":          p.id,
        "name":        p.name,
        "description": p.description,
        "created_at":  str(p.created_at)[:10],
        "summary":     summary,
        "holdings":    enriched,
    }


@router.put("/{pid}")
def update_portfolio(pid: int, body: PortfolioUpdate, db: Session = Depends(get_db)):
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if body.name is not None:
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description.strip()
    db.commit()
    return {"id": p.id, "name": p.name, "description": p.description}


@router.delete("/{pid}", status_code=204)
def delete_portfolio(pid: int, db: Session = Depends(get_db)):
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    db.delete(p)
    db.commit()


@router.post("/{pid}/holdings", status_code=201)
def add_holding(pid: int, body: HoldingCreate, db: Session = Depends(get_db)):
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    h = Holding(
        portfolio_id=pid,
        ticker=body.ticker.upper().strip(),
        shares=body.shares,
        avg_cost=body.avg_cost,
        notes=body.notes,
        added_date=date.today(),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return {"id": h.id, "ticker": h.ticker, "shares": h.shares, "avg_cost": h.avg_cost}


@router.put("/{pid}/holdings/{hid}")
def update_holding(pid: int, hid: int, body: HoldingUpdate, db: Session = Depends(get_db)):
    h = db.query(Holding).filter(Holding.id == hid, Holding.portfolio_id == pid).first()
    if not h:
        raise HTTPException(status_code=404, detail="Holding not found")
    if body.shares is not None:
        h.shares = body.shares
    if body.avg_cost is not None:
        h.avg_cost = body.avg_cost
    if body.notes is not None:
        h.notes = body.notes
    db.commit()
    return {"id": h.id, "ticker": h.ticker, "shares": h.shares, "avg_cost": h.avg_cost}


@router.delete("/{pid}/holdings/{hid}", status_code=204)
def delete_holding(pid: int, hid: int, db: Session = Depends(get_db)):
    h = db.query(Holding).filter(Holding.id == hid, Holding.portfolio_id == pid).first()
    if not h:
        raise HTTPException(status_code=404, detail="Holding not found")
    db.delete(h)
    db.commit()
