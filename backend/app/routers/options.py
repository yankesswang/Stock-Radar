"""
Options chain data via yfinance.
GET /api/stocks/{ticker}/options          - IV surface summary (cached 15 min)
GET /api/stocks/{ticker}/options/{expiry} - full chain for one expiry
"""
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
from fastapi import APIRouter, HTTPException

router = APIRouter()

_CACHE: dict = {}
CACHE_TTL = 15 * 60  # 15 minutes


def _safe(val):
    try:
        if val is None:
            return None
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _expiry_summary(ticker_obj, expiration: str) -> dict | None:
    try:
        chain = ticker_obj.option_chain(expiration)
        calls = chain.calls
        puts = chain.puts

        c_iv = calls["impliedVolatility"].dropna() * 100
        p_iv = puts["impliedVolatility"].dropna() * 100
        c_oi = int(calls["openInterest"].fillna(0).sum())
        p_oi = int(puts["openInterest"].fillna(0).sum())
        c_vol = int(calls["volume"].fillna(0).sum())
        p_vol = int(puts["volume"].fillna(0).sum())

        return {
            "expiry": expiration,
            "calls_avg_iv": round(float(c_iv.mean()), 2) if not c_iv.empty else None,
            "puts_avg_iv": round(float(p_iv.mean()), 2) if not p_iv.empty else None,
            "calls_oi": c_oi,
            "puts_oi": p_oi,
            "calls_volume": c_vol,
            "puts_volume": p_vol,
        }
    except Exception:
        return None


def _chain_to_list(df) -> list[dict]:
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "strike": _safe(row.get("strike")),
            "last_price": _safe(row.get("lastPrice")),
            "bid": _safe(row.get("bid")),
            "ask": _safe(row.get("ask")),
            "volume": int(row.get("volume") or 0),
            "open_interest": int(row.get("openInterest") or 0),
            "iv_pct": round(float(row.get("impliedVolatility") or 0) * 100, 2),
            "in_the_money": bool(row.get("inTheMoney", False)),
        })
    return rows


@router.get("/{ticker}/options")
def get_options_summary(ticker: str):
    """IV surface summary across nearest 6 expirations."""
    ticker = ticker.upper()
    cache_key = f"summary_{ticker}"
    now = time.time()

    if cache_key in _CACHE and now - _CACHE[cache_key]["ts"] < CACHE_TTL:
        return _CACHE[cache_key]["data"]

    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            raise HTTPException(status_code=404, detail=f"No options data for {ticker}")

        hist = t.history(period="1d")
        current_price = float(hist["Close"].iloc[-1]) if not hist.empty else None

        # Fetch first 6 expirations in parallel
        summaries = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_expiry_summary, t, exp): exp for exp in expirations[:6]}
            for fut in as_completed(futures):
                s = fut.result()
                if s:
                    summaries.append(s)
        summaries.sort(key=lambda x: x["expiry"])

        total_c_oi = sum(s["calls_oi"] for s in summaries)
        total_p_oi = sum(s["puts_oi"] for s in summaries)
        pc_ratio = round(total_p_oi / total_c_oi, 3) if total_c_oi > 0 else None

        # 30-day IV estimate: nearest expiry avg call IV
        atm_iv = summaries[0]["calls_avg_iv"] if summaries else None

        payload = {
            "ticker": ticker,
            "current_price": _safe(current_price),
            "atm_iv_pct": atm_iv,
            "put_call_oi_ratio": pc_ratio,
            "expirations": list(expirations),
            "iv_surface": summaries,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _CACHE[cache_key] = {"data": payload, "ts": now}
        return payload

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{ticker}/options/{expiry}")
def get_options_chain(ticker: str, expiry: str):
    """Full call/put chain for a specific expiry (YYYY-MM-DD)."""
    ticker = ticker.upper()
    cache_key = f"chain_{ticker}_{expiry}"
    now = time.time()

    if cache_key in _CACHE and now - _CACHE[cache_key]["ts"] < CACHE_TTL:
        return _CACHE[cache_key]["data"]

    try:
        t = yf.Ticker(ticker)
        chain = t.option_chain(expiry)

        hist = t.history(period="1d")
        current_price = float(hist["Close"].iloc[-1]) if not hist.empty else None

        # Max pain: strike where total OI loss (calls+puts) is minimized
        all_strikes = sorted(
            set(chain.calls["strike"].dropna().tolist() + chain.puts["strike"].dropna().tolist())
        )
        max_pain_strike = None
        if current_price and all_strikes:
            min_loss = None
            for s in all_strikes:
                call_loss = sum(
                    max(0.0, float(s) - float(k)) * float(oi)
                    for k, oi in zip(chain.calls["strike"], chain.calls["openInterest"].fillna(0))
                )
                put_loss = sum(
                    max(0.0, float(k) - float(s)) * float(oi)
                    for k, oi in zip(chain.puts["strike"], chain.puts["openInterest"].fillna(0))
                )
                total = call_loss + put_loss
                if min_loss is None or total < min_loss:
                    min_loss = total
                    max_pain_strike = s

        payload = {
            "ticker": ticker,
            "expiry": expiry,
            "current_price": _safe(current_price),
            "max_pain": _safe(max_pain_strike),
            "calls": _chain_to_list(chain.calls),
            "puts": _chain_to_list(chain.puts),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _CACHE[cache_key] = {"data": payload, "ts": now}
        return payload

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{ticker}/options/cache")
def clear_options_cache(ticker: str):
    ticker = ticker.upper()
    removed = [k for k in list(_CACHE.keys()) if ticker in k]
    for k in removed:
        del _CACHE[k]
    return {"cleared": removed}
