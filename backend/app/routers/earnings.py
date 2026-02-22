"""
Earnings calendar: fetch upcoming earnings dates for all DB tickers via yfinance.
Results cached in-memory for 1 hour to avoid hammering yfinance.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta, datetime as _datetime

import yfinance as yf
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Stock

router = APIRouter()

# ── 1-hour TTL cache ─────────────────────────────────────────────────────────
_CACHE: dict = {"data": None, "ts": 0}
CACHE_TTL = 3600  # 1 hour


def _fetch_earnings(ticker: str, company_name: str) -> list[dict] | None:
    """Fetch earnings calendar for a single ticker. Returns list of events."""
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        if not cal:
            return None

        # yfinance may return a dict or a DataFrame — normalise to dict
        if not isinstance(cal, dict):
            cal = dict(cal) if hasattr(cal, "items") else {}

        raw_dates = cal.get("Earnings Date") or cal.get("earningsDate") or []

        # normalize: could be a list, a single Timestamp, or a dict
        if not isinstance(raw_dates, list):
            raw_dates = [raw_dates]

        # keep only valid date objects
        # yfinance may return datetime.date, datetime.datetime, or pd.Timestamp
        parsed = []
        for d in raw_dates:
            try:
                if isinstance(d, _datetime):
                    dt = d.date()          # datetime.datetime / pd.Timestamp
                elif isinstance(d, date):
                    dt = d                 # already a datetime.date
                elif hasattr(d, "date"):
                    dt = d.date()          # other Timestamp-like objects
                else:
                    continue
                parsed.append(dt)
            except Exception:
                continue

        if not parsed:
            return None

        # use the first (earliest) date as the canonical date
        earnings_date = min(parsed)

        # only include events within ±30 days past and +90 days future
        today = date.today()
        if earnings_date < today - timedelta(days=30) or earnings_date > today + timedelta(days=90):
            return None

        def _safe(v):
            try:
                if v is None:
                    return None
                f = float(v)
                import math
                return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
            except Exception:
                return None

        return [{
            "ticker": ticker,
            "company_name": company_name,
            "date": earnings_date.isoformat(),
            "date_range": [d.isoformat() for d in sorted(parsed)],
            "eps_estimate": _safe(cal.get("Earnings Average") or cal.get("epsAverage")),
            "eps_low":      _safe(cal.get("Earnings Low")     or cal.get("epsLow")),
            "eps_high":     _safe(cal.get("Earnings High")    or cal.get("epsHigh")),
            "rev_estimate": _safe(cal.get("Revenue Average")  or cal.get("revenueAverage")),
            "rev_low":      _safe(cal.get("Revenue Low")      or cal.get("revenueLow")),
            "rev_high":     _safe(cal.get("Revenue High")     or cal.get("revenueHigh")),
        }]
    except Exception:
        return None


@router.get("/calendar")
def get_earnings_calendar(db: Session = Depends(get_db)):
    """
    Return upcoming earnings events for all tracked tickers.
    Fetches from yfinance, cached for 1 hour.
    """
    global _CACHE
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < CACHE_TTL:
        return _CACHE["data"]

    # load all tickers from DB
    stocks = db.query(Stock.ticker, Stock.company_name).all()

    events: list[dict] = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_fetch_earnings, s.ticker, s.company_name or s.ticker): s.ticker
                   for s in stocks}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                events.extend(result)

    # sort by date
    events.sort(key=lambda e: e["date"])

    payload = {"events": events, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _CACHE = {"data": payload, "ts": now}
    return payload


@router.delete("/calendar/cache")
def clear_cache():
    """Clear the in-memory earnings calendar cache."""
    global _CACHE
    _CACHE = {"data": None, "ts": 0}
    return {"ok": True}
