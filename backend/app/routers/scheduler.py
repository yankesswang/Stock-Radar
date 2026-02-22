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
from datetime import datetime, timedelta
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
CHECK_INTERVAL_SECONDS = 3600          # check every hour
TICKER_DELAY_SECONDS   = 2             # throttle between tickers

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
}

_refresh_task: asyncio.Task | None = None


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state() -> None:
    global _state
    try:
        saved = json.loads(STATE_FILE.read_text())
        _state["last_run"] = saved.get("last_run")
        _state["next_run"] = saved.get("next_run")
    except Exception:
        pass


def _save_state() -> None:
    try:
        STATE_FILE.write_text(json.dumps({
            "last_run": _state["last_run"],
            "next_run": _state["next_run"],
        }))
    except Exception:
        pass


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


# ── Background scheduler loop ─────────────────────────────────────────────────

async def scheduler_loop() -> None:
    """Started once in FastAPI lifespan. Checks every hour."""
    _load_state()
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
    }


@router.post("/run")
async def trigger_run():
    """Manually trigger a full refresh (non-blocking)."""
    if _state["status"] == "running":
        raise HTTPException(status_code=409, detail="Refresh already in progress.")
    asyncio.create_task(_do_refresh())
    return {"ok": True, "message": "Refresh started in background."}
