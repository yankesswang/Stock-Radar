"""
Macro / economy overview via yfinance.

Endpoints:
  GET /api/macro/overview           — 現值快照 + 殖利率利差（快取 30 分）
  GET /api/macro/history/{symbol}   — 單一指標的日收盤序列（?period=）
  GET /api/macro/series             — 多指標一次抓齊，供疊圖比較（?symbols=&period=）
  GET /api/macro/curve              — 殖利率曲線期限結構（今天／1個月前／1年前）+ 利差歷史
  DELETE /api/macro/cache           — 清掉上面全部的快取

歷史序列全部走 _history()，它有自己的 (symbol, period) 快取 —— 曲線頁一次要
四條公債殖利率的兩年序列，沒有快取的話每次切期間都是四趟 yfinance。
"""
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

# yfinance 接受的期間；前端的區間切換只能送這幾個
PERIODS = ("1mo", "3mo", "6mo", "1y", "2y", "5y", "max")

# 公債殖利率的期限結構 —— yfinance 只有這四個天期（沒有 2Y）
CURVE = [
    {"symbol": "^IRX", "label": "3M", "months": 3},
    {"symbol": "^FVX", "label": "5Y", "months": 60},
    {"symbol": "^TNX", "label": "10Y", "months": 120},
    {"symbol": "^TYX", "label": "30Y", "months": 360},
]

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


_HIST_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
HIST_TTL = 30 * 60


def _history(symbol: str, period: str = "1y") -> list[dict]:
    """Daily closes for one symbol, cached per (symbol, period)."""
    key = (symbol, period)
    hit = _HIST_CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < HIST_TTL:
        return hit[1]
    try:
        hist = yf.Ticker(symbol).history(period=period, auto_adjust=True)
        if hist is None or hist.empty:
            return []
        rows = []
        for idx, row in hist.iterrows():
            try:
                c = float(row["Close"])
                if math.isnan(c):
                    continue
            except (TypeError, ValueError):
                continue
            rows.append({"date": str(idx)[:10], "close": round(c, 4)})
    except Exception:
        return []
    _HIST_CACHE[key] = (now, rows)
    return rows


def _stats(rows: list[dict]) -> dict:
    """區間高低與現值在區間內的百分位 —— 圖旁邊要寫的那幾個數字。"""
    closes = [r["close"] for r in rows]
    if not closes:
        return {}
    first, last = closes[0], closes[-1]
    lo, hi = min(closes), max(closes)
    return {
        "first": first,
        "last": last,
        "min": lo,
        "max": hi,
        "change": round(last - first, 4),
        # 殖利率本身就是百分比，變動用「變動了幾 %」對它沒有意義；
        # 兩個都給，前端依 unit 決定顯示哪一個。
        "change_pct": round((last - first) / first * 100, 2) if first else None,
        # 現值落在區間高低之間的哪裡（0=區間低點、100=區間高點）
        "percentile": round((last - lo) / (hi - lo) * 100) if hi > lo else 50,
        "n": len(closes),
    }


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
def get_macro_history(symbol: str, period: str = Query("1y", pattern="|".join(PERIODS))):
    """Daily close history for a single macro symbol (e.g. ^TNX, CL=F)."""
    sym = symbol.upper()
    history = _history(sym, period)
    return {
        "symbol": sym,
        "period": period,
        "history": history,
        "stats": _stats(history),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.get("/series")
def get_macro_series(
    symbols: str = Query(..., description="逗號分隔，最多 12 檔"),
    period: str = Query("1y", pattern="|".join(PERIODS)),
):
    """多指標的日收盤序列，一次抓齊 —— 疊圖比較用。

    每檔另附 rebased：以區間第一天為 100 的相對走勢。單位不同的東西
    （殖利率 % vs 油價 $）只有 rebase 過才比得起來，也才不用畫雙 Y 軸。
    """
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()][:12]
    if not wanted:
        raise HTTPException(status_code=400, detail="symbols is required")

    known = {m["symbol"]: m for m in MACRO_SYMBOLS}
    out = {}
    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        futures = {pool.submit(_history, s, period): s for s in wanted}
        for fut in as_completed(futures):
            sym = futures[fut]
            rows = fut.result()
            base = rows[0]["close"] if rows else None
            out[sym] = {
                "label": known.get(sym, {}).get("label", sym),
                "unit": known.get(sym, {}).get("unit", ""),
                "stats": _stats(rows),
                "points": [
                    {
                        "date": r["date"],
                        "close": r["close"],
                        "rebased": round(r["close"] / base * 100, 2) if base else None,
                    }
                    for r in rows
                ],
            }
    return {
        "period": period,
        "symbols": wanted,
        "series": {s: out.get(s, {"points": []}) for s in wanted},
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _asof(rows: list[dict], days_back: int) -> float | None:
    """往回 N 個交易日的收盤（不足就取最早一筆）。"""
    if not rows:
        return None
    return rows[max(0, len(rows) - 1 - days_back)]["close"]


@router.get("/curve")
def get_yield_curve(period: str = Query("2y", pattern="|".join(PERIODS))):
    """殖利率曲線的期限結構 + 利差歷史。

    期限結構給三條線（今天／約 1 個月前／約 1 年前）—— 曲線是往上還是倒過來、
    這一年整條往上平移還是變陡，一張圖就看得出來，比四張各自的走勢圖直接。
    利差歷史（10Y−3M、10Y−5Y）附上，倒掛期間前端塗色。

    period 只影響利差歷史。期限結構的兩個錨點（1 個月前／1 年前）是固定語意，
    一律從 2y 序列往回數 —— 否則使用者選 3M 時，「1 年前」會悄悄退化成
    「這段區間的第一天」，標籤說謊。
    """
    anchor_period = "2y"
    wanted = {(c["symbol"], anchor_period) for c in CURVE}
    wanted |= {(c["symbol"], period) for c in CURVE}

    hist: dict[tuple[str, str], list[dict]] = {}
    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        futures = {pool.submit(_history, sym, per): (sym, per) for sym, per in wanted}
        for fut in as_completed(futures):
            hist[futures[fut]] = fut.result()

    # 交易日：1 個月 ≈ 21 天、1 年 ≈ 252 天
    points = []
    for c in CURVE:
        rows = hist.get((c["symbol"], anchor_period), [])
        points.append({
            **c,
            "now": rows[-1]["close"] if rows else None,
            # 序列不夠長就給 None（畫不出那條線），不要拿區間起點頂替
            "m1": _asof(rows, 21) if len(rows) > 21 else None,
            "y1": _asof(rows, 252) if len(rows) > 252 else None,
        })

    # 利差要逐日對齊 —— 四條序列的交易日不一定完全一致（假日規則不同）
    tnx = {r["date"]: r["close"] for r in hist.get(("^TNX", period), [])}
    irx = {r["date"]: r["close"] for r in hist.get(("^IRX", period), [])}
    fvx = {r["date"]: r["close"] for r in hist.get(("^FVX", period), [])}
    spreads = [
        {
            "date": d,
            "s10y3m": round(tnx[d] - irx[d], 3) if d in irx else None,
            "s10y5y": round(tnx[d] - fvx[d], 3) if d in fvx else None,
        }
        for d in sorted(tnx)
    ]
    inverted = [s for s in spreads if s["s10y3m"] is not None and s["s10y3m"] < 0]

    return {
        "period": period,
        "points": points,
        "spreads": spreads,
        "inverted_days": len(inverted),
        "last_inverted": inverted[-1]["date"] if inverted else None,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.delete("/cache")
def clear_macro_cache():
    global _CACHE
    _CACHE = {"data": None, "ts": 0}
    _HIST_CACHE.clear()
    return {"cleared": True}
