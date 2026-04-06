"""
Watchlist groups: named lists of tickers stored in data/watchlists.json.
Enriches tickers with live prices + DB valuation on read.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from .portfolio import _fetch_prices, _get_valuations  # reuse helpers

router = APIRouter()

_WATCHLISTS_PATH: Path = settings.DATA_DIR / "watchlists.json"


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _load() -> dict[str, list[str]]:
    if _WATCHLISTS_PATH.exists():
        try:
            return json.loads(_WATCHLISTS_PATH.read_text())
        except Exception:
            pass
    return {}


def _save(data: dict[str, list[str]]) -> None:
    _WATCHLISTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WATCHLISTS_PATH.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GroupCreate(BaseModel):
    name: str

class TickerAdd(BaseModel):
    ticker: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def list_groups():
    """Return all group names with ticker counts."""
    data = _load()
    return [{"name": k, "count": len(v)} for k, v in data.items()]


@router.post("", status_code=201)
def create_group(body: GroupCreate):
    """Create a new empty watchlist group."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Group name cannot be empty")
    data = _load()
    if name in data:
        raise HTTPException(status_code=409, detail=f"Group '{name}' already exists")
    data[name] = []
    _save(data)
    return {"name": name, "count": 0}


@router.delete("/{group}", status_code=204)
def delete_group(group: str):
    """Delete a watchlist group and all its tickers."""
    data = _load()
    if group not in data:
        raise HTTPException(status_code=404, detail=f"Group '{group}' not found")
    del data[group]
    _save(data)


@router.get("/{group}")
def get_group(group: str, db: Session = Depends(get_db)):
    """Return enriched ticker list for a group (live prices + valuation)."""
    data = _load()
    if group not in data:
        raise HTTPException(status_code=404, detail=f"Group '{group}' not found")

    tickers = data[group]
    if not tickers:
        return {"name": group, "tickers": []}

    prices = _fetch_prices(tickers)
    vals   = _get_valuations(tickers, db)

    result = []
    for t in tickers:
        price = prices.get(t.upper())
        val   = vals.get(t.upper(), {})
        fair_pe = val.get("pe_median_price")
        to_fair = round((fair_pe - price) / price * 100, 1) if (fair_pe and price) else None
        result.append({
            "ticker":           t,
            "company_name":     val.get("company_name", t),
            "industry":         val.get("industry"),
            "current_price":    price,
            "pe_median_price":  fair_pe,
            "pct_to_fair_pe":   to_fair,
            "valuation_status": val.get("valuation_status"),
            "pe_median_5y":     val.get("pe_median_5y"),
            "estimated_pe":     val.get("estimated_pe"),
        })

    return {"name": group, "tickers": result}


@router.post("/{group}/tickers", status_code=201)
def add_ticker(group: str, body: TickerAdd):
    """Add a ticker to a watchlist group."""
    data = _load()
    if group not in data:
        raise HTTPException(status_code=404, detail=f"Group '{group}' not found")
    ticker = body.ticker.upper().strip()
    if ticker in [t.upper() for t in data[group]]:
        raise HTTPException(status_code=409, detail=f"{ticker} already in '{group}'")
    data[group].append(ticker)
    _save(data)
    return {"group": group, "ticker": ticker}


@router.delete("/{group}/tickers/{ticker}", status_code=204)
def remove_ticker(group: str, ticker: str):
    """Remove a ticker from a watchlist group."""
    data = _load()
    if group not in data:
        raise HTTPException(status_code=404, detail=f"Group '{group}' not found")
    ticker = ticker.upper().strip()
    tickers_upper = [t.upper() for t in data[group]]
    if ticker not in tickers_upper:
        raise HTTPException(status_code=404, detail=f"{ticker} not found in '{group}'")
    data[group] = [t for t in data[group] if t.upper() != ticker]
    _save(data)
