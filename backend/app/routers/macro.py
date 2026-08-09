"""
Macro / economy overview via yfinance.
GET /api/macro/overview - Treasury yields, commodities, DXY, VIX (cached 30 min)
"""
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
from fastapi import APIRouter

router = APIRouter()

MACRO_SYMBOLS = [
    # Treasury yields (yfinance returns the actual yield value, e.g. 4.52 for 4.52%)
    {"symbol": "^IRX", "label": "3M T-Bill",    "type": "yield",     "unit": "%"},
    {"symbol": "^FVX", "label": "5Y Treasury",   "type": "yield",     "unit": "%"},
    {"symbol": "^TNX", "label": "10Y Treasury",  "type": "yield",     "unit": "%"},
    {"symbol": "^TYX", "label": "30Y Treasury",  "type": "yield",     "unit": "%"},
    # Commodities
    {"symbol": "CL=F", "label": "Crude Oil",     "type": "commodity", "unit": "$/bbl"},
    {"symbol": "GC=F", "label": "Gold",          "type": "commodity", "unit": "$/oz"},
    {"symbol": "NG=F", "label": "Nat Gas",       "type": "commodity", "unit": "$/MMBtu"},
    # Currency / volatility
    {"symbol": "DX-Y.NYB", "label": "US Dollar Index", "type": "currency",   "unit": ""},
    {"symbol": "^VIX",     "label": "VIX",              "type": "volatility", "unit": ""},
]

_CACHE: dict = {"data": None, "ts": 0}
CACHE_TTL = 30 * 60  # 30 minutes


def _fetch_symbol(meta: dict) -> dict:
    symbol = meta["symbol"]
    try:
        hist = yf.Ticker(symbol).history(period="1mo", auto_adjust=True)
        if hist is None or hist.empty:
            return {**meta, "ok": False, "price": None, "sparkline": []}

        close = hist["Close"].dropna()
        if close.empty:
            return {**meta, "ok": False, "price": None, "sparkline": []}

        price = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) >= 2 else None
        change = price - prev if prev is not None else None
        change_pct = change / prev * 100 if (prev and change is not None) else None

        m1_base = float(close.iloc[0])
        m1_pct = (price - m1_base) / m1_base * 100 if m1_base else None

        # build sparkline from 1-month daily closes
        sparkline = [
            {"date": str(idx)[:10][5:], "close": round(float(row), 4)}
            for idx, row in close.items()
        ]

        return {
            **meta,
            "ok": True,
            "price": round(price, 4),
            "change": round(change, 4) if change is not None else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "change_1m_pct": round(m1_pct, 2) if m1_pct is not None else None,
            "last_date": str(close.index[-1])[:10],
            "sparkline": sparkline,
        }
    except Exception as exc:
        return {**meta, "ok": False, "price": None, "error": str(exc), "sparkline": []}


def _fetch_1y_history(symbol: str) -> list[dict]:
    """Return daily close history for the past year."""
    try:
        hist = yf.Ticker(symbol).history(period="1y", auto_adjust=True)
        if hist is None or hist.empty:
            return []
        rows = []
        for idx, row in hist.iterrows():
            close_val = row["Close"]
            try:
                c = float(close_val)
                if math.isnan(c):
                    continue
            except (TypeError, ValueError):
                continue
            date_str = str(idx)[:10]
            rows.append({"date": date_str, "close": round(c, 4)})
        return rows
    except Exception:
        return []


@router.get("/overview")
def get_macro_overview():
    """Current snapshot of key macro indicators with yield-curve spread."""
    global _CACHE
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < CACHE_TTL:
        return _CACHE["data"]

    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(MACRO_SYMBOLS)) as pool:
        futures = {pool.submit(_fetch_symbol, meta): meta["symbol"] for meta in MACRO_SYMBOLS}
        for fut in as_completed(futures):
            items.append(fut.result())

    order = {m["symbol"]: i for i, m in enumerate(MACRO_SYMBOLS)}
    items.sort(key=lambda x: order.get(x["symbol"], 99))

    prices = {item["symbol"]: item.get("price") for item in items}
    tnx = prices.get("^TNX")
    irx = prices.get("^IRX")
    spread_10y_3m = round(tnx - irx, 3) if (tnx and irx) else None

    fvx = prices.get("^FVX")
    spread_10y_5y = round(tnx - fvx, 3) if (tnx and fvx) else None

    payload = {
        "items": items,
        "yield_curve": {
            "spread_10y_3m": spread_10y_3m,
            "spread_10y_5y": spread_10y_5y,
            "inverted_10y_3m": spread_10y_3m < 0 if spread_10y_3m is not None else None,
        },
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _CACHE = {"data": payload, "ts": now}
    return payload


@router.get("/history/{symbol}")
def get_macro_history(symbol: str):
    """1-year daily close history for a single macro symbol (e.g. ^TNX, CL=F)."""
    history = _fetch_1y_history(symbol.upper())
    return {
        "symbol": symbol.upper(),
        "history": history,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.delete("/cache")
def clear_macro_cache():
    global _CACHE
    _CACHE = {"data": None, "ts": 0}
    return {"cleared": True}
