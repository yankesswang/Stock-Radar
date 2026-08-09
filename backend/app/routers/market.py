from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import io
import time
from urllib.parse import quote

from fastapi import APIRouter, Query
import requests
import yfinance as yf

from ..extended_quote import fetch_extended_quotes

router = APIRouter()

MARKET_SYMBOLS = [
    {"symbol": "SPY", "label": "S&P 500", "asset_type": "ETF"},
    {"symbol": "QQQ", "label": "Nasdaq 100", "asset_type": "ETF"},
    {"symbol": "IWM", "label": "Russell 2000", "asset_type": "ETF"},
    {"symbol": "^DJI", "label": "Dow Jones", "asset_type": "Index"},
    {"symbol": "^VIX", "label": "VIX", "asset_type": "Volatility"},
    {"symbol": "BTC-USD", "label": "Bitcoin", "asset_type": "Crypto"},
    {"symbol": "ETH-USD", "label": "Ethereum", "asset_type": "Crypto"},
    {"symbol": "GLD", "label": "Gold", "asset_type": "Commodity"},
    {"symbol": "TLT", "label": "20Y Treasury", "asset_type": "Rates"},
    {"symbol": "UUP", "label": "US Dollar", "asset_type": "Currency"},
]

_CACHE: dict = {"data": None, "ts": 0}
CACHE_TTL_SECONDS = 5 * 60
HTTP_TIMEOUT_SECONDS = 8
STOOQ_SYMBOLS = {
    "SPY": "spy.us",
    "QQQ": "qqq.us",
    "IWM": "iwm.us",
    "^DJI": "^dji",
    "^VIX": "^vix",
    "GLD": "gld.us",
    "TLT": "tlt.us",
    "UUP": "uup.us",
}
COINGECKO_IDS = {
    "BTC-USD": "bitcoin",
    "ETH-USD": "ethereum",
}


def _symbol_meta(symbol: str) -> dict:
    upper = symbol.upper()
    for item in MARKET_SYMBOLS:
        if item["symbol"].upper() == upper:
            return item
    return {"symbol": symbol, "label": symbol, "asset_type": "Custom"}


def _fetch_market_symbol(symbol: str) -> dict:
    meta = _symbol_meta(symbol)
    result = _fetch_yfinance_symbol(symbol, meta)
    if result.get("ok"):
        return {**result, **_fetch_performance_stats(symbol)}

    if symbol.upper() in COINGECKO_IDS:
        fallback = _fetch_coingecko_symbol(symbol, meta)
        if not fallback.get("ok"):
            fallback = _fetch_yahoo_chart_symbol(symbol, meta)
    else:
        fallback = _fetch_yahoo_chart_symbol(symbol, meta)
        if not fallback.get("ok"):
            fallback = _fetch_stooq_quote_symbol(symbol, meta)

    if fallback.get("ok"):
        return {**fallback, **_fetch_performance_stats(symbol)}
    return {**result, "fallback_error": fallback.get("error")}


def _fetch_yfinance_symbol(symbol: str, meta: dict) -> dict:
    try:
        hist = yf.Ticker(symbol).history(period="5d", auto_adjust=True)
        if hist is None or hist.empty:
            return {**meta, "ok": False, "error": "No price data"}

        close = hist["Close"].dropna()
        if close.empty:
            return {**meta, "ok": False, "error": "No close data"}

        price = float(close.iloc[-1])
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else None
        change = price - prev_close if prev_close else None
        change_pct = change / prev_close * 100 if prev_close else None

        return {
            **meta,
            "ok": True,
            "price": round(price, 2),
            "previous_close": round(prev_close, 2) if prev_close else None,
            "change": round(change, 2) if change is not None else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "last_date": str(close.index[-1].date()),
        }
    except Exception as exc:
        return {**meta, "ok": False, "error": str(exc)}


def _fetch_stooq_symbol(symbol: str, meta: dict) -> dict:
    stooq_symbol = STOOQ_SYMBOLS.get(symbol.upper())
    if not stooq_symbol:
        return {**meta, "ok": False, "error": "No Stooq mapping"}

    try:
        url = f"https://stooq.com/q/d/l/?s={quote(stooq_symbol)}&i=d"
        response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()

        rows = list(csv.DictReader(io.StringIO(response.text)))
        rows = [
            row for row in rows
            if row.get("Close") not in (None, "", "N/D") and row.get("Date") not in (None, "", "N/D")
        ]
        if len(rows) < 2:
            return {**meta, "ok": False, "error": "No Stooq history"}

        latest = rows[-1]
        previous = rows[-2]
        price = float(latest["Close"])
        prev_close = float(previous["Close"])
        if prev_close <= 0:
            return {**meta, "ok": False, "error": "Invalid Stooq previous close"}

        change = price - prev_close
        change_pct = change / prev_close * 100
        return {
            **meta,
            "ok": True,
            "price": round(price, 2),
            "previous_close": round(prev_close, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "last_date": latest["Date"],
            "source": "stooq",
        }
    except Exception as exc:
        return {**meta, "ok": False, "error": f"Stooq failed: {exc}"}


def _fetch_yahoo_chart_symbol(symbol: str, meta: dict) -> dict:
    try:
        pairs = _fetch_yahoo_chart_closes(symbol, "5d")
        if len(pairs) < 2:
            return {**meta, "ok": False, "error": "No Yahoo chart close history"}

        latest_ts, latest_close = pairs[-1]
        _, previous_close = pairs[-2]
        price = float(latest_close)
        prev_close = float(previous_close)
        if prev_close <= 0:
            return {**meta, "ok": False, "error": "Invalid Yahoo chart previous close"}

        change = price - prev_close
        change_pct = change / prev_close * 100
        return {
            **meta,
            "ok": True,
            "price": round(price, 2),
            "previous_close": round(prev_close, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "last_date": time.strftime("%Y-%m-%d", time.gmtime(int(latest_ts))),
            "source": "yahoo_chart",
        }
    except Exception as exc:
        return {**meta, "ok": False, "error": f"Yahoo chart failed: {exc}"}


def _fetch_yahoo_chart_closes(symbol: str, range_value: str) -> list[tuple[int, float]]:
    response = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}",
        params={"range": range_value, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    result = ((response.json().get("chart") or {}).get("result") or [None])[0]
    if not result:
        return []

    timestamps = result.get("timestamp") or []
    quote_data = (((result.get("indicators") or {}).get("quote") or [{}])[0])
    closes = quote_data.get("close") or []
    return [
        (int(ts), float(close))
        for ts, close in zip(timestamps, closes)
        if close is not None
    ]


def _pct_change(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base <= 0:
        return None
    return round((current - base) / base * 100, 2)


def _max_drawdown(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    peak = closes[0]
    max_dd = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak > 0:
            max_dd = min(max_dd, (close - peak) / peak)
    return round(max_dd * 100, 2)


def _fetch_performance_stats(symbol: str) -> dict:
    try:
        pairs = _fetch_yahoo_chart_closes(symbol, "1y")
        if len(pairs) < 2:
            return {"ytd_return_pct": None, "one_month_return_pct": None, "max_drawdown_pct": None, "sparkline": []}

        latest_ts, latest_close = pairs[-1]
        latest_year = time.gmtime(latest_ts).tm_year
        ytd_base = next((close for ts, close in pairs if time.gmtime(ts).tm_year == latest_year), None)
        one_month_base = pairs[-22][1] if len(pairs) >= 22 else pairs[0][1]
        closes = [close for _, close in pairs]

        # last 30 trading days as sparkline
        spark_pairs = pairs[-30:]
        sparkline = [
            {"date": time.strftime("%m/%d", time.gmtime(int(ts))), "close": round(float(c), 4)}
            for ts, c in spark_pairs
        ]

        return {
            "ytd_return_pct": _pct_change(latest_close, ytd_base),
            "one_month_return_pct": _pct_change(latest_close, one_month_base),
            "max_drawdown_pct": _max_drawdown(closes),
            "stats_period": "1y",
            "sparkline": sparkline,
        }
    except Exception:
        return {
            "ytd_return_pct": None,
            "one_month_return_pct": None,
            "max_drawdown_pct": None,
            "stats_period": None,
            "sparkline": [],
        }


def _fetch_stooq_quote_symbol(symbol: str, meta: dict) -> dict:
    stooq_symbol = STOOQ_SYMBOLS.get(symbol.upper())
    if not stooq_symbol:
        return {**meta, "ok": False, "error": "No Stooq mapping"}

    try:
        response = requests.get(
            "https://stooq.com/q/l/",
            params={"s": stooq_symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(response.text)))
        if not rows:
            return {**meta, "ok": False, "error": "No Stooq quote"}

        row = rows[0]
        if row.get("Close") in (None, "", "N/D"):
            return {**meta, "ok": False, "error": "No Stooq quote close"}

        price = float(row["Close"])
        open_price = float(row["Open"]) if row.get("Open") not in (None, "", "N/D") else None
        change = price - open_price if open_price else None
        change_pct = change / open_price * 100 if open_price else None
        return {
            **meta,
            "ok": True,
            "price": round(price, 2),
            "previous_close": round(open_price, 2) if open_price else None,
            "change": round(change, 2) if change is not None else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "last_date": row.get("Date"),
            "source": "stooq_quote",
        }
    except Exception as exc:
        return {**meta, "ok": False, "error": f"Stooq quote failed: {exc}"}


def _fetch_coingecko_symbol(symbol: str, meta: dict) -> dict:
    coin_id = COINGECKO_IDS.get(symbol.upper())
    if not coin_id:
        return {**meta, "ok": False, "error": "No CoinGecko mapping"}

    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json().get(coin_id) or {}
        price = data.get("usd")
        change_pct = data.get("usd_24h_change")
        if price is None or change_pct is None:
            return {**meta, "ok": False, "error": "No CoinGecko price"}

        prev_close = float(price) / (1 + float(change_pct) / 100)
        change = float(price) - prev_close
        return {
            **meta,
            "ok": True,
            "price": round(float(price), 2),
            "previous_close": round(prev_close, 2),
            "change": round(change, 2),
            "change_pct": round(float(change_pct), 2),
            "last_date": time.strftime("%Y-%m-%d", time.gmtime()),
            "source": "coingecko",
        }
    except Exception as exc:
        return {**meta, "ok": False, "error": f"CoinGecko failed: {exc}"}


@router.get("/overview")
def get_market_overview(symbols: str | None = Query(None, description="Comma-separated symbols")):
    global _CACHE

    requested = [
        s.strip().upper()
        for s in (symbols.split(",") if symbols else [item["symbol"] for item in MARKET_SYMBOLS])
        if s.strip()
    ]
    cache_key = ",".join(requested)
    now = time.time()
    if (
        _CACHE["data"] is not None
        and _CACHE.get("key") == cache_key
        and now - _CACHE["ts"] < CACHE_TTL_SECONDS
    ):
        return _CACHE["data"]

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_market_symbol, symbol): symbol for symbol in requested}
        for future in as_completed(futures):
            rows.append(future.result())

    order = {symbol: idx for idx, symbol in enumerate(requested)}
    rows.sort(key=lambda item: order.get(item["symbol"].upper(), 999))

    valid = [item for item in rows if item.get("ok") and item.get("change_pct") is not None]
    risk_on_count = sum(1 for item in valid if item["change_pct"] > 0)
    risk_off_count = sum(1 for item in valid if item["change_pct"] < 0)
    tone = "risk_on" if risk_on_count > risk_off_count else "risk_off" if risk_off_count > risk_on_count else "mixed"

    payload = {
        "items": rows,
        "summary": {
            "tone": tone,
            "positive_count": risk_on_count,
            "negative_count": risk_off_count,
            "total_count": len(valid),
        },
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if valid:
        _CACHE = {"data": payload, "ts": now, "key": cache_key}
    return payload


# ── Sparklines ────────────────────────────────────────────────────────────────

_SPARK_CACHE: dict = {}
_SPARK_TTL = 15 * 60  # 15 min


def _fetch_sparkline(symbol: str, days: int) -> list[dict]:
    """Return the last `days` daily closes for a symbol."""
    range_map = {7: "5d", 30: "1mo", 90: "3mo"}
    range_val = range_map.get(days, "1mo")
    try:
        pairs = _fetch_yahoo_chart_closes(symbol, range_val)
        cutoff = len(pairs) - days
        pairs = pairs[max(0, cutoff):]
        return [
            {"date": time.strftime("%m/%d", time.gmtime(int(ts))), "close": round(float(c), 4)}
            for ts, c in pairs
        ]
    except Exception:
        return []


@router.get("/sparklines")
def get_sparklines(
    symbols: str = Query(..., description="Comma-separated symbols"),
    days: int = Query(30, ge=5, le=90),
):
    """Batch 30-day sparkline data for multiple symbols. Cached 15 min."""
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    now = time.time()

    result: dict[str, list[dict]] = {}
    to_fetch = []
    for sym in requested:
        cache_key = f"{sym}_{days}"
        if cache_key in _SPARK_CACHE and now - _SPARK_CACHE[cache_key]["ts"] < _SPARK_TTL:
            result[sym] = _SPARK_CACHE[cache_key]["data"]
        else:
            to_fetch.append(sym)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(len(to_fetch), 12)) as pool:
            futures = {pool.submit(_fetch_sparkline, sym, days): sym for sym in to_fetch}
            for fut in as_completed(futures):
                sym = futures[fut]
                data = fut.result()
                cache_key = f"{sym}_{days}"
                _SPARK_CACHE[cache_key] = {"data": data, "ts": now}
                result[sym] = data

    return {"sparklines": result, "days": days, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


@router.get("/extended")
def get_extended_quotes(symbols: str = Query(..., description="Comma-separated symbols")):
    """Pre/post-market quotes. Symbols not currently in an extended session are
    omitted from `quotes`, so an empty dict means "regular hours or closed"."""
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    quotes = fetch_extended_quotes(requested)
    return {
        "quotes": quotes,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
