"""
Price alert system: target price + moving average alerts per ticker.
Configs stored in data/alerts.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yfinance as yf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings

router = APIRouter()

_ALERTS_PATH: Path = settings.DATA_DIR / "alerts.json"

MA_PERIODS = [5, 10, 20, 50, 200]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AlertConfig(BaseModel):
    target_price: Optional[float] = None
    mas: list[int] = []  # e.g. [5, 20, 50]


class AlertStatus(BaseModel):
    ticker: str
    price: Optional[float]
    target_price: Optional[float]
    target_hit: bool
    ma_status: dict  # { "20": {"value": 123.4, "near": True, "crossed": False} }


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    if _ALERTS_PATH.exists():
        return json.loads(_ALERTS_PATH.read_text())
    return {}


def _save(data: dict) -> None:
    _ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ALERTS_PATH.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# MA computation
# ---------------------------------------------------------------------------

def _compute_status(ticker: str, config: dict) -> dict:
    """Fetch price history and compute MA status for one ticker."""
    t = yf.Ticker(ticker)
    hist = t.history(period="1y")

    price: Optional[float] = None
    if not hist.empty:
        price = float(hist["Close"].iloc[-1])

    ma_status: dict = {}
    requested_mas: list[int] = config.get("mas", [])
    for period in requested_mas:
        if len(hist) < period:
            continue
        ma_val = float(hist["Close"].rolling(period).mean().iloc[-1])
        near = False
        crossed = False
        if price is not None:
            near = abs(price - ma_val) / ma_val <= 0.01
            if len(hist) >= period + 1:
                prev_close = float(hist["Close"].iloc[-2])
                prev_ma = float(hist["Close"].rolling(period).mean().iloc[-2])
                # crossed = price moved from one side of MA to the other
                crossed = (prev_close < prev_ma and price >= ma_val) or (
                    prev_close > prev_ma and price <= ma_val
                )
        ma_status[str(period)] = {
            "value": round(ma_val, 4),
            "near": near,
            "crossed": crossed,
        }

    target_price = config.get("target_price")
    target_hit = False
    if price is not None and target_price is not None:
        target_hit = price >= target_price

    return {
        "price": round(price, 4) if price is not None else None,
        "target_price": target_price,
        "target_hit": target_hit,
        "ma_status": ma_status,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def list_alerts():
    """Return all saved alert configs."""
    return _load()


@router.get("/triggered")
def get_triggered():
    """
    Batch-check all configured alerts and return only those with
    at least one triggered condition (target_hit or near/crossed MA).
    """
    configs = _load()
    triggered = {}
    for ticker, config in configs.items():
        status = _compute_status(ticker, config)
        any_triggered = status["target_hit"] or any(
            v["near"] or v["crossed"] for v in status["ma_status"].values()
        )
        if any_triggered:
            triggered[ticker] = status
    return triggered


@router.get("/{ticker}/status")
def get_alert_status(ticker: str):
    """Live-check alert status for a single ticker."""
    ticker = ticker.upper()
    configs = _load()
    config = configs.get(ticker, {})
    if not config:
        raise HTTPException(status_code=404, detail=f"No alert config for {ticker}")
    status = _compute_status(ticker, config)
    return {"ticker": ticker, **status}


@router.put("/{ticker}")
def upsert_alert(ticker: str, body: AlertConfig):
    """Create or update alert config for a ticker."""
    ticker = ticker.upper()
    configs = _load()
    configs[ticker] = body.model_dump()
    _save(configs)
    return {"ticker": ticker, **configs[ticker]}


@router.delete("/{ticker}")
def delete_alert(ticker: str):
    """Remove alert config for a ticker."""
    ticker = ticker.upper()
    configs = _load()
    if ticker not in configs:
        raise HTTPException(status_code=404, detail=f"No alert config for {ticker}")
    del configs[ticker]
    _save(configs)
    return {"deleted": ticker}
