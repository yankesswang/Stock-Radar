"""
Weekly data refresh scheduler.

Runs in the background as an asyncio task (started via FastAPI lifespan).
Every hour it checks whether 7 days have passed since the last full refresh.
If so, it re-scrapes all tickers in STOCK_LIST: ratios → PE median → forecast → valuations.

Endpoints:
  GET  /api/scheduler/status  – current state (last run, next run, progress)
  POST /api/scheduler/run     – trigger an immediate refresh (non-blocking)
"""
import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STATE_FILE = PROJECT_ROOT / "data" / "scheduler_state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

REFRESH_INTERVAL_DAYS = 7
CHECK_INTERVAL_SECONDS = 60            # check every minute
TICKER_DELAY_SECONDS   = 2             # throttle between tickers
REPORT_DAILY_HOUR = 8                  # local server time

# ── In-memory state ───────────────────────────────────────────────────────────
_state: dict[str, Any] = {
    "status":    "idle",       # idle | running
    "last_run":  None,         # ISO datetime string
    "next_run":  None,         # ISO datetime string
    "total":     0,
    "done":      0,
    "current":   None,         # ticker being scraped right now
    "errors":    [],
    "log":       [],
    "report_status":        "idle",     # idle | running
    "report_last_run":      None,       # ISO datetime string
    "report_last_run_date": None,       # YYYY-MM-DD
    "report_next_run":      None,       # ISO datetime string
    "report_done":          0,
    "report_total":         0,
    "report_errors":        [],
}

_refresh_task: asyncio.Task | None = None


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state() -> None:
    global _state
    try:
        saved = json.loads(STATE_FILE.read_text())
        _state["last_run"] = saved.get("last_run")
        _state["next_run"] = saved.get("next_run")
        _state["report_last_run"] = saved.get("report_last_run")
        _state["report_last_run_date"] = saved.get("report_last_run_date")
        _state["report_next_run"] = saved.get("report_next_run")
    except Exception:
        pass


def _save_state() -> None:
    try:
        STATE_FILE.write_text(json.dumps({
            "last_run": _state["last_run"],
            "next_run": _state["next_run"],
            "report_last_run": _state["report_last_run"],
            "report_last_run_date": _state["report_last_run_date"],
            "report_next_run": _state["report_next_run"],
        }))
    except Exception:
        pass


def _calc_next_report_run(now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    target = now.replace(hour=REPORT_DAILY_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return target


def _should_run_daily_report(now: datetime) -> tuple[bool, str]:
    """
    Decide whether daily report should run now.
    Rules:
    - normal schedule: run once per day at/after REPORT_DAILY_HOUR
    - catch-up mode: if reports are missing for >=2 days, run immediately
    """
    if _state.get("report_status") == "running":
        return False, "already-running"

    today = now.date().isoformat()
    last_run_date = _state.get("report_last_run_date")
    if last_run_date == today:
        return False, "already-ran-today"

    # First run ever
    if not last_run_date:
        if now.hour >= REPORT_DAILY_HOUR:
            return True, "first-run"
        return False, "before-scheduled-hour"

    # Parse last run date; if invalid treat as stale and run now
    try:
        last_d = date.fromisoformat(last_run_date)
    except Exception:
        return True, "invalid-last-run-date"

    gap_days = (now.date() - last_d).days
    if now.hour >= REPORT_DAILY_HOUR:
        return True, "scheduled-hour"
    if gap_days >= 2:
        return True, f"catch-up-gap-{gap_days}d"
    return False, "before-scheduled-hour"


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    _state["log"].append(entry)
    if len(_state["log"]) > 200:
        _state["log"] = _state["log"][-200:]
    print(f"[scheduler] {entry}")


# ── Sync scraping helpers (run in executor so they don't block event loop) ────

def _scrape_one(ticker: str, group: str) -> str:
    """Run full pipeline for a single ticker. Returns brief result string."""
    from datetime import date as _date
    snapshot = _date.today()

    # 1. Ratios
    try:
        from scripts.scrape_ratios import scrape_ratios, save_ratios
        df = scrape_ratios(ticker)
        if df is not None:
            save_ratios(ticker, df, industry=group)
    except Exception as e:
        pass  # non-fatal

    # 2. PE median
    try:
        from scripts.scrape_fullratio_pe import scrape_ticker as fr_scrape
        from src.db_service import save_pe_median
        fr = fr_scrape(ticker)
        if fr:
            pe = fr.get("summary", {}).get("Median") or fr.get("pe_median")
            if pe:
                save_pe_median(ticker, float(pe), snapshot)
    except Exception:
        pass

    # 3. Forecast
    try:
        from scripts.scrape_forecast import scrape_forecast, save_to_db
        fd = scrape_forecast(ticker)
        if fd:
            save_to_db(ticker, fd)
    except Exception:
        pass

    return ticker


def _run_valuations() -> None:
    from datetime import date as _date
    from src.db_service import compute_valuations_for_date
    compute_valuations_for_date(_date.today())


# ── Core refresh coroutine ────────────────────────────────────────────────────

async def _do_refresh() -> None:
    global _state

    if _state["status"] == "running":
        _log("Refresh already in progress — skipped.")
        return

    from src.config import STOCK_LIST

    # Build deduplicated (ticker, group) list
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for group, tickers in STOCK_LIST.items():
        for t in tickers:
            if t not in seen:
                seen.add(t)
                pairs.append((t, group))

    _state.update({
        "status":  "running",
        "total":   len(pairs),
        "done":    0,
        "current": None,
        "errors":  [],
    })
    _log(f"Starting full refresh — {len(pairs)} tickers.")

    loop = asyncio.get_event_loop()

    for ticker, group in pairs:
        _state["current"] = ticker
        try:
            await loop.run_in_executor(None, _scrape_one, ticker, group)
            _log(f"✓ {ticker}")
        except Exception as exc:
            err = f"{ticker}: {exc}"
            _state["errors"].append(err)
            _log(f"✗ {err}")
        _state["done"] += 1
        await asyncio.sleep(TICKER_DELAY_SECONDS)

    # Final valuation pass for all tickers
    _log("Computing valuations…")
    try:
        await loop.run_in_executor(None, _run_valuations)
        _log("Valuations done.")
    except Exception as exc:
        _log(f"Valuations error: {exc}")

    now = datetime.now()
    next_run = now + timedelta(days=REFRESH_INTERVAL_DAYS)
    _state.update({
        "status":    "idle",
        "last_run":  now.isoformat(timespec="seconds"),
        "next_run":  next_run.isoformat(timespec="seconds"),
        "current":   None,
    })
    _save_state()
    _log(f"Refresh complete. {len(_state['errors'])} error(s). Next: {next_run.strftime('%Y-%m-%d %H:%M')}")


def _run_daily_reports_sync() -> dict[str, Any]:
    """Generate daily portfolio diagnosis snapshots for all CSV portfolios."""
    from ..database import SessionLocal
    from .portfolio import get_csv_portfolio, _generate_csv_portfolio_report

    db = SessionLocal()
    try:
        payload = get_csv_portfolio(name=None, db=db)
        names = payload.get("portfolio_names", [])
        ok: list[str] = []
        errors: list[str] = []
        for name in names:
            try:
                _generate_csv_portfolio_report(portfolio=name, period="daily", db=db)
                ok.append(name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        return {"ok": ok, "errors": errors}
    finally:
        db.close()


async def _do_daily_report_refresh() -> None:
    if _state["report_status"] == "running":
        return

    _state.update({
        "report_status": "running",
        "report_done": 0,
        "report_total": 0,
        "report_errors": [],
    })
    _log("Starting daily portfolio report generation.")

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _run_daily_reports_sync)
        ok = result.get("ok", [])
        errors = result.get("errors", [])
    except Exception as exc:
        ok = []
        errors = [str(exc)]
    now = datetime.now()

    _state.update({
        "report_status": "idle",
        "report_last_run": now.isoformat(timespec="seconds"),
        "report_last_run_date": now.date().isoformat(),
        "report_next_run": _calc_next_report_run(now).isoformat(timespec="seconds"),
        "report_done": len(ok),
        "report_total": len(ok) + len(errors),
        "report_errors": errors,
    })
    _save_state()
    _log(
        f"Daily portfolio reports complete. generated={len(ok)} "
        f"errors={len(errors)} next={_state['report_next_run']}"
    )


# ── Background scheduler loop ─────────────────────────────────────────────────

async def scheduler_loop() -> None:
    """Started once in FastAPI lifespan. Checks every hour."""
    _load_state()
    if not _state.get("report_next_run"):
        _state["report_next_run"] = _calc_next_report_run().isoformat(timespec="seconds")
        _save_state()
    _log("Scheduler started.")

    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        if _state["status"] == "running":
            continue

        last = _state.get("last_run")
        if last is None:
            _log("No previous run found — scheduling first refresh.")
            asyncio.create_task(_do_refresh())
            continue

        last_dt = datetime.fromisoformat(last)
        if (datetime.now() - last_dt).days >= REFRESH_INTERVAL_DAYS:
            _log("7 days elapsed — starting scheduled refresh.")
            asyncio.create_task(_do_refresh())

        now = datetime.now()
        should_run, reason = _should_run_daily_report(now)
        if should_run:
            _log(f"Daily report trigger ({reason}) — starting report generation.")
            asyncio.create_task(_do_daily_report_refresh())


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.get("/status")
def get_status():
    """Return scheduler state."""
    return {
        "status":   _state["status"],
        "last_run": _state["last_run"],
        "next_run": _state["next_run"],
        "total":    _state["total"],
        "done":     _state["done"],
        "current":  _state["current"],
        "errors":   _state["errors"][-10:],   # last 10 errors only
        "log":      _state["log"][-30:],       # last 30 log lines
        "report": {
            "status": _state["report_status"],
            "last_run": _state["report_last_run"],
            "last_run_date": _state["report_last_run_date"],
            "next_run": _state["report_next_run"],
            "done": _state["report_done"],
            "total": _state["report_total"],
            "errors": _state["report_errors"][-10:],
        },
    }


@router.post("/run")
async def trigger_run():
    """Manually trigger a full refresh (non-blocking)."""
    if _state["status"] == "running":
        raise HTTPException(status_code=409, detail="Refresh already in progress.")
    asyncio.create_task(_do_refresh())
    return {"ok": True, "message": "Refresh started in background."}


@router.post("/report/run")
async def trigger_report_run():
    """Manually trigger daily portfolio report generation (non-blocking)."""
    if _state["report_status"] == "running":
        raise HTTPException(status_code=409, detail="Report generation already in progress.")
    asyncio.create_task(_do_daily_report_refresh())
    return {"ok": True, "message": "Daily report generation started in background."}
