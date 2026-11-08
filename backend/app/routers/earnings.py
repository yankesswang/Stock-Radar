"""
Earnings calendar: next report date for every tracked ticker.

Dates come from Yahoo's ``calendarEvents`` epochs, converted in **US/Eastern**,
and not from ``Ticker.calendar``, which is wrong in two ways that both leaked
into the daily review:

  * yfinance 0.2.x parses those epochs with ``datetime.fromtimestamp(d)`` — the
    *host* timezone. On a Taipei box (UTC+8) NVDA's 16:00 ET call on 08-26 comes
    back as 08-27, and so does every other after-close reporter; pre-market
    reporters (08:00 ET → 20:00 Taipei) happen to survive, which is why the bug
    looked intermittent. Reading the epoch ourselves also tells us whether the
    call is pre-market or after-close.
  * Yahoo keeps serving the date a company **already reported on** for weeks
    afterwards, while its EPS estimate has already rolled to the next quarter.
    On 2026-08-23 ADI came back as 2026-08-19 (the report it had already
    delivered) paired with 3.84 (the November quarter's estimate). A date in the
    past is therefore never reported as upcoming — we ask the schedule endpoint
    for the real next date, and if that is unavailable the ticker simply has no
    upcoming event rather than a wrong one.

Every event carries ``status``: "upcoming" (not yet reported) or "reported"
(already out, kept for the calendar UI's month grid). Anything asking "when is
the next earnings" must filter on ``status == "upcoming"``.

Results cached in-memory for 1 hour to avoid hammering yfinance.
"""
import math
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Stock

router = APIRouter()

# ── 1-hour TTL cache ─────────────────────────────────────────────────────────
_CACHE: dict = {"data": None, "ts": 0}
CACHE_TTL = 3600  # 1 hour

ET = ZoneInfo("America/New_York")          # earnings calls are scheduled in ET
# 財報筆記管線的個股情報表（scripts/earnings/）。earnings_events 的 report_date 是
# EDGAR 8-K Item 2.02 確認過的**實際公布日** — 唯一不會騙人的一格。
REGISTRY_DB = Path(__file__).resolve().parents[3] / "data" / "earnings_registry.db"
UPCOMING_WINDOW_DAYS = 90                  # how far ahead we report
REPORTED_WINDOW_DAYS = 30                  # how far back we keep reported events


def _safe(v):
    """Round to a JSON-safe float, or None for NaN/inf/garbage."""
    try:
        if v is None:
            return None
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except Exception:
        return None


def _time_of_day(hour: int) -> str:
    """Yahoo schedules pre-market calls at 07:00-08:00 ET and post-close at 15:00-16:00 ET."""
    if hour < 12:
        return "BMO"        # before market open
    if hour >= 15:
        return "AMC"        # after market close
    return "TBD"


def _load_registry() -> tuple[dict, dict]:
    """(EDGAR 確認的最後公布日, 存檔的下次財報日) — 兩者都以 ticker 為 key。

    Yahoo 在公司公布財報後還會拿舊日期充當「下一次」好幾週，光靠它自己分不出來。
    EDGAR 的 8-K 申報日不會動：只要 Yahoo 給的日期不晚於它，那就是已經公布過的那一場。
    """
    last: dict[str, str] = {}
    stored: dict[str, str] = {}
    if not REGISTRY_DB.exists():
        return last, stored
    try:
        con = sqlite3.connect(f"file:{REGISTRY_DB}?mode=ro", uri=True, timeout=5)
    except Exception:
        return last, stored
    try:
        for t, d in con.execute(
            "SELECT ticker, MAX(report_date) FROM earnings_events "
            "WHERE report_date IS NOT NULL AND report_date != '' GROUP BY ticker"
        ):
            if t and d:
                last[str(t).upper()] = str(d)
        for t, d in con.execute(
            "SELECT ticker, next_report_date FROM earnings_calendar "
            "WHERE next_report_date IS NOT NULL AND next_report_date != ''"
        ):
            if t and d:
                stored[str(t).upper()] = str(d)
    except Exception:
        pass
    finally:
        con.close()
    return last, stored


def _fetch_calendar_events(ticker: str) -> dict:
    """Yahoo calendarEvents for one ticker: ET-correct datetimes + estimate ranges.

    Goes to the raw quoteSummary payload because yfinance's own parsing drops the
    time of day and converts the epoch in the host timezone (see module docstring).
    Falls back to Ticker.calendar if Yahoo's response shape ever changes — that
    path loses the ET correction, so it is flagged as such by the caller.
    """
    try:
        raw = yf.Ticker(ticker)._quote._fetch(modules=["calendarEvents"])
        earnings = raw["quoteSummary"]["result"][0]["calendarEvents"]["earnings"]
    except Exception:
        return _legacy_calendar(ticker)

    stamps: list[datetime] = []
    for epoch in earnings.get("earningsDate", []) or []:
        try:
            stamps.append(datetime.fromtimestamp(int(epoch), ET))
        except Exception:
            continue
    return {
        "stamps": sorted(stamps),
        "tz_correct": True,
        "is_estimate": bool(earnings.get("isEarningsDateEstimate")),
        "eps_estimate": _safe(earnings.get("earningsAverage")),
        "eps_low":      _safe(earnings.get("earningsLow")),
        "eps_high":     _safe(earnings.get("earningsHigh")),
        "rev_estimate": _safe(earnings.get("revenueAverage")),
        "rev_low":      _safe(earnings.get("revenueLow")),
        "rev_high":     _safe(earnings.get("revenueHigh")),
    }


def _legacy_calendar(ticker: str) -> dict:
    """Ticker.calendar fallback — dates here are host-timezone, so a day may be off."""
    try:
        cal = yf.Ticker(ticker).calendar
    except Exception:
        return {"stamps": [], "tz_correct": False, "is_estimate": False}
    if not cal:
        return {"stamps": [], "tz_correct": False, "is_estimate": False}
    if not isinstance(cal, dict):
        cal = dict(cal) if hasattr(cal, "items") else {}

    raw_dates = cal.get("Earnings Date") or cal.get("earningsDate") or []
    if not isinstance(raw_dates, list):
        raw_dates = [raw_dates]
    stamps: list[datetime] = []
    for d in raw_dates:
        try:
            if isinstance(d, datetime):
                stamps.append(d if d.tzinfo else d.replace(tzinfo=ET))
            elif isinstance(d, date):
                stamps.append(datetime(d.year, d.month, d.day, tzinfo=ET))
        except Exception:
            continue
    return {
        "stamps": sorted(stamps),
        "tz_correct": False,
        "is_estimate": False,
        "eps_estimate": _safe(cal.get("Earnings Average") or cal.get("epsAverage")),
        "eps_low":      _safe(cal.get("Earnings Low")     or cal.get("epsLow")),
        "eps_high":     _safe(cal.get("Earnings High")    or cal.get("epsHigh")),
        "rev_estimate": _safe(cal.get("Revenue Average")  or cal.get("revenueAverage")),
        "rev_low":      _safe(cal.get("Revenue Low")      or cal.get("revenueLow")),
        "rev_high":     _safe(cal.get("Revenue High")     or cal.get("revenueHigh")),
    }


def _next_from_schedule(ticker: str, today: date) -> datetime | None:
    """Next scheduled call from the earnings-dates table.

    Only consulted when calendarEvents is stale (still pointing at a report that
    already happened). On yfinance 0.2.x this is an HTML scrape that Yahoo rate
    limits, so failure is expected and simply means "no known next date" — which
    is the honest answer, and better than repeating the stale one.
    """
    try:
        df = yf.Ticker(ticker).get_earnings_dates(limit=8)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    future = []
    for idx, row in df.iterrows():
        try:
            stamp = idx.to_pydatetime()
            stamp = stamp.astimezone(ET) if stamp.tzinfo else stamp.replace(tzinfo=ET)
        except Exception:
            continue
        if stamp.date() >= today and _safe(row.get("Reported EPS")) is None:
            future.append(stamp)
    return min(future) if future else None


def _fetch_earnings(ticker: str, company_name: str,
                    edgar_last: str | None = None,
                    stored_next: str | None = None) -> list[dict]:
    """Next (and most recently reported) earnings event for one ticker.

    ``edgar_last`` is the EDGAR-confirmed date this company last reported on and
    ``stored_next`` the date the earnings-note pipeline has on file — both from
    the local registry, both optional (it only tracks the curated watchlist).
    """
    today = datetime.now(ET).date()
    cal = _fetch_calendar_events(ticker)
    stamps = cal.get("stamps") or []

    # A Yahoo date that is not later than the 8-K we already have on file is the
    # report that already happened, whatever Yahoo labels it.
    future = [s for s in stamps if s.date() >= today
              and (not edgar_last or s.date().isoformat() > edgar_last)]
    past   = [s for s in stamps if s.date() < today]

    events: list[dict] = []

    def base(d: date) -> dict:
        return {
            "ticker": ticker,
            "company_name": company_name,
            "date": d.isoformat(),
            "date_range": sorted({s.date().isoformat() for s in stamps}),
            "days_until": (d - today).days,
        }

    upcoming = min(future) if future else None
    date_source = "calendar_events" if cal.get("tz_correct") else "calendar_host_tz"
    if upcoming is None and (past or edgar_last):
        # Yahoo is still pointing at the report this company already delivered.
        upcoming = _next_from_schedule(ticker, today)
        date_source = "earnings_dates"
        if upcoming is None and stored_next and stored_next > today.isoformat() \
                and (not edgar_last or stored_next > edgar_last):
            # Yahoo rate-limited us; fall back to what the pipeline stored.
            upcoming = datetime.fromisoformat(stored_next).replace(tzinfo=ET)
            date_source = "registry"

    if upcoming is not None and upcoming.date() <= today + timedelta(days=UPCOMING_WINDOW_DAYS):
        estimated = cal.get("is_estimate") or date_source == "calendar_host_tz"
        # estimates belong to the next event, so they only travel with a date we
        # actually got from Yahoo's calendar — never with a scraped replacement.
        with_est = date_source in ("calendar_events", "calendar_host_tz")
        events.append({
            **base(upcoming.date()),
            "status": "upcoming",
            "time_of_day": ("TBD" if date_source == "registry" or not cal.get("tz_correct")
                            else _time_of_day(upcoming.hour)),
            "date_source": date_source,
            "date_confirmed": not estimated,
            "eps_estimate": cal.get("eps_estimate") if with_est else None,
            "eps_low":      cal.get("eps_low")      if with_est else None,
            "eps_high":     cal.get("eps_high")     if with_est else None,
            "rev_estimate": cal.get("rev_estimate") if with_est else None,
            "rev_low":      cal.get("rev_low")      if with_est else None,
            "rev_high":     cal.get("rev_high")     if with_est else None,
        })

    if edgar_last and (not past or edgar_last >= max(past).date().isoformat()):
        # EDGAR beats Yahoo on "when did this actually come out"
        d = date.fromisoformat(edgar_last)
        if d >= today - timedelta(days=REPORTED_WINDOW_DAYS):
            events.append({
                **base(d),
                "status": "reported",
                "time_of_day": "TBD",
                "date_source": "edgar_8k",
                "date_confirmed": True,
                "eps_estimate": None, "eps_low": None, "eps_high": None,
                "rev_estimate": None, "rev_low": None, "rev_high": None,
            })
    elif past:
        latest = max(past)
        if latest.date() >= today - timedelta(days=REPORTED_WINDOW_DAYS):
            events.append({
                **base(latest.date()),
                "status": "reported",
                "time_of_day": _time_of_day(latest.hour) if cal.get("tz_correct") else "TBD",
                "date_source": date_source if date_source != "earnings_dates" else "calendar_events",
                "date_confirmed": True,
                "eps_estimate": None, "eps_low": None, "eps_high": None,
                "rev_estimate": None, "rev_low": None, "rev_high": None,
            })

    return events


@router.get("/calendar")
def get_earnings_calendar(db: Session = Depends(get_db)):
    """
    Earnings events for all tracked tickers: the next scheduled report plus the
    most recent one already delivered (``status`` tells them apart).
    Fetched from yfinance, cached for 1 hour.
    """
    global _CACHE
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < CACHE_TTL:
        return _CACHE["data"]

    # load all tickers from DB
    stocks = db.query(Stock.ticker, Stock.company_name).all()
    edgar_last, stored_next = _load_registry()

    events: list[dict] = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = {pool.submit(_fetch_earnings, s.ticker, s.company_name or s.ticker,
                               edgar_last.get(s.ticker.upper()),
                               stored_next.get(s.ticker.upper())): s.ticker
                   for s in stocks}
        for fut in as_completed(futures):
            try:
                events.extend(fut.result() or [])
            except Exception:
                continue

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
