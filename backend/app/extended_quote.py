"""
Pre-market / post-market quotes derived from the Yahoo chart API.

Yahoo's v7 quote endpoint (which carries preMarketPrice/postMarketPrice
directly) sits behind the rate-limited cookie/crumb flow, so we derive the
same numbers from the v8 chart endpoint instead — the one path in this app
that keeps working when yfinance starts returning YFRateLimitError.

Derivation, matching Yahoo's own definition:
  base      = meta.regularMarketPrice   (pre-market: the prior session's
              close; post-market: today's close — Yahoo only advances this
              field when the regular session ends)
  extended  = close of the last 1-minute bar inside the currently active
              pre/post window from meta.currentTradingPeriod
  change_%  = (extended - base) / base * 100

Outside the pre/post windows there is nothing to show, so we return None and
the UI renders no badge.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests

# Quotes go stale fast, so this cache is deliberately far shorter than the
# 10-minute one in chart.py.
_CACHE: dict = {}
_CACHE_TTL = 45  # seconds

_SESSIONS = ("pre", "post")


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    now = time.time()
    for k in [k for k, v in _CACHE.items() if now - v["ts"] >= _CACHE_TTL]:
        del _CACHE[k]
    _CACHE[key] = {"data": data, "ts": now}


def _active_session(periods: dict, now: int) -> str | None:
    """Which extended window, if any, the clock is currently inside."""
    for name in _SESSIONS:
        window = periods.get(name) or {}
        start, end = window.get("start"), window.get("end")
        if start is not None and end is not None and start <= now < end:
            return name
    return None


def fetch_extended_quote(ticker: str) -> dict | None:
    """Pre/post-market quote for one ticker, or None if not in an extended session."""
    cached = _cache_get(ticker.upper())
    if cached is not None:
        return cached.get("quote")

    quote_data = None
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker.upper(), safe='')}",
            params={"range": "1d", "interval": "1m", "includePrePost": "true"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        response.raise_for_status()
        result = ((response.json().get("chart") or {}).get("result") or [None])[0]
        if result:
            quote_data = _derive(result)
    except Exception:
        quote_data = None

    _cache_set(ticker.upper(), {"quote": quote_data})
    return quote_data


def _derive(result: dict) -> dict | None:
    meta = result.get("meta") or {}
    periods = meta.get("currentTradingPeriod") or {}
    session = _active_session(periods, int(time.time()))
    if not session:
        return None

    base = meta.get("regularMarketPrice")
    if not base:
        return None

    window = periods[session]
    timestamps = result.get("timestamp") or []
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []

    last_ts, last_close = None, None
    for ts, close in zip(timestamps, closes):
        if close is not None and window["start"] <= ts < window["end"]:
            last_ts, last_close = ts, close

    # An extended session with no prints yet (thin names right after the bell)
    # is a real state, not an error — just nothing to render.
    if last_close is None:
        return None

    change = last_close - base
    return {
        "session":    session,          # "pre" | "post"
        "price":      round(last_close, 2),
        "prev_close": round(base, 2),
        "change":     round(change, 2),
        "change_pct": round(change / base * 100, 2),
        "time":       last_ts,
    }


def fetch_extended_quotes(tickers: list[str]) -> dict[str, dict]:
    """Batch version. Tickers not in an extended session are simply absent."""
    unique = {t.upper() for t in tickers if t and t.strip()}
    if not unique:
        return {}

    quotes: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_extended_quote, t): t for t in unique}
        for fut in as_completed(futures):
            data = fut.result()
            if data is not None:
                quotes[futures[fut]] = data
    return quotes
