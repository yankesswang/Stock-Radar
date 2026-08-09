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
from pydantic import BaseModel

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
# AI 持倉決策日報「不」由本內部排程自動觸發 —— 改由 stock digest 流程
# （~/Library/Scripts/stock-digest-portfolio.sh，launchd 6:30）呼叫
# POST /api/scheduler/ai-report/run 觸發。此處僅保留手動端點與狀態。

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
    "refresh_enabled": True,
    "report_status":        "idle",     # idle | running
    "report_enabled":       True,
    "report_last_run":      None,       # ISO datetime string
    "report_last_run_date": None,       # YYYY-MM-DD
    "report_next_run":      None,       # ISO datetime string
    "report_done":          0,
    "report_total":         0,
    "report_errors":        [],
    "ai_report_status":        "idle",   # idle | running
    "ai_report_enabled":       True,
    "ai_report_last_run":      None,     # ISO datetime string
    "ai_report_last_run_date": None,     # YYYY-MM-DD
    "ai_report_next_run":      None,     # ISO datetime string
    "ai_report_done":          0,
    "ai_report_total":         0,
    "ai_report_errors":        [],
}

_refresh_task: asyncio.Task | None = None


class SchedulerConfigIn(BaseModel):
    refresh_enabled: bool | None = None
    report_enabled: bool | None = None
    ai_report_enabled: bool | None = None


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state() -> None:
    global _state
    try:
        saved = json.loads(STATE_FILE.read_text())
        _state["last_run"] = saved.get("last_run")
        _state["next_run"] = saved.get("next_run")
        _state["refresh_enabled"] = bool(saved.get("refresh_enabled", True))
        _state["report_enabled"] = bool(saved.get("report_enabled", True))
        _state["report_last_run"] = saved.get("report_last_run")
        _state["report_last_run_date"] = saved.get("report_last_run_date")
        _state["report_next_run"] = saved.get("report_next_run")
        _state["ai_report_enabled"] = bool(saved.get("ai_report_enabled", True))
        _state["ai_report_last_run"] = saved.get("ai_report_last_run")
        _state["ai_report_last_run_date"] = saved.get("ai_report_last_run_date")
        _state["ai_report_next_run"] = saved.get("ai_report_next_run")
    except Exception:
        pass


def _save_state() -> None:
    try:
        STATE_FILE.write_text(json.dumps({
            "last_run": _state["last_run"],
            "next_run": _state["next_run"],
            "refresh_enabled": _state["refresh_enabled"],
            "report_enabled": _state["report_enabled"],
            "report_last_run": _state["report_last_run"],
            "report_last_run_date": _state["report_last_run_date"],
            "report_next_run": _state["report_next_run"],
            "ai_report_enabled": _state["ai_report_enabled"],
            "ai_report_last_run": _state["ai_report_last_run"],
            "ai_report_last_run_date": _state["ai_report_last_run_date"],
            "ai_report_next_run": _state["ai_report_next_run"],
        }))
    except Exception:
        pass


def _calc_next_run_at(hour: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return target


def _calc_next_report_run(now: datetime | None = None) -> datetime:
    return _calc_next_run_at(REPORT_DAILY_HOUR, now)


def _should_run_at(now: datetime, hour: int, last_run_date: str | None, is_running: bool) -> tuple[bool, str]:
    """
    Decide whether a daily task should run now.
    Rules:
    - normal schedule: run once per day at/after `hour`
    - catch-up mode: if it's been missing for >=2 days, run immediately
    """
    if is_running:
        return False, "already-running"

    today = now.date().isoformat()
    if last_run_date == today:
        return False, "already-ran-today"

    # First run ever
    if not last_run_date:
        if now.hour >= hour:
            return True, "first-run"
        return False, "before-scheduled-hour"

    # Parse last run date; if invalid treat as stale and run now
    try:
        last_d = date.fromisoformat(last_run_date)
    except Exception:
        return True, "invalid-last-run-date"

    gap_days = (now.date() - last_d).days
    if now.hour >= hour:
        return True, "scheduled-hour"
    if gap_days >= 2:
        return True, f"catch-up-gap-{gap_days}d"
    return False, "before-scheduled-hour"


def _should_run_daily_report(now: datetime) -> tuple[bool, str]:
    return _should_run_at(
        now, REPORT_DAILY_HOUR,
        _state.get("report_last_run_date"),
        _state.get("report_status") == "running",
    )


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
    from datetime import date as _date
    from ..database import SessionLocal
    from .portfolio import get_csv_portfolio, _generate_csv_portfolio_report

    # Refresh every stock's valuation from its latest metrics/forecast/PE first,
    # so verdicts (and the reports below) don't show stale over/under-valued
    # results for tickers that weren't re-scraped today.
    try:
        from src.db_service import recompute_all_valuations
        recompute_all_valuations(_date.today())
    except Exception as exc:
        _log(f"Daily valuation recompute error: {exc}")

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


def _run_ai_report_sync() -> dict[str, Any]:
    """
    批次產生並「快取」所有持倉的 per-stock AI 決策卡。

    這不再是獨立推播的日報 —— 卡片只寫入快取（data/ai_analysis/{TICKER}_{date}.json
    + data/ai_reports/{date}.json 供前端閱覽），供 scripts/daily_analysis.py 收盤分析
    當作逐股 context 讀取。故 to_obsidian / to_telegram 皆為 False。
    """
    from .ai_report import generate_ai_portfolio_report
    return generate_ai_portfolio_report(refresh=False, to_obsidian=False, to_telegram=False)


async def _do_ai_report_refresh() -> None:
    if _state["ai_report_status"] == "running":
        return

    _state.update({
        "ai_report_status": "running",
        "ai_report_done": 0,
        "ai_report_total": 0,
        "ai_report_errors": [],
    })
    _log("Starting AI portfolio decision report.")

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _run_ai_report_sync)
        done = result.get("ok", 0)
        total = result.get("total", 0)
        errors = result.get("errors", [])
    except Exception as exc:
        done, total, errors = 0, 0, [str(exc)]
    now = datetime.now()

    _state.update({
        "ai_report_status": "idle",
        "ai_report_last_run": now.isoformat(timespec="seconds"),
        "ai_report_last_run_date": now.date().isoformat(),
        "ai_report_next_run": None,   # 由 stock digest 流程外部排程，非內部自動觸發
        "ai_report_done": done,
        "ai_report_total": total,
        "ai_report_errors": errors,
    })
    _save_state()
    _log(f"AI report complete. ok={done}/{total} errors={len(errors)} next={_state['ai_report_next_run']}")


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

        if _state.get("refresh_enabled", True):
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
        if _state.get("report_enabled", True):
            should_run, reason = _should_run_daily_report(now)
            if should_run:
                _log(f"Daily report trigger ({reason}) — starting report generation.")
                asyncio.create_task(_do_daily_report_refresh())

        # AI 持倉日報不在此自動觸發 —— 由 stock digest 流程（launchd 6:30）呼叫
        # POST /api/scheduler/ai-report/run 驅動，見 trigger_ai_report_run()。


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.get("/status")
def get_status():
    """Return scheduler state."""
    return {
        "status":   _state["status"],
        "refresh_enabled": _state["refresh_enabled"],
        "last_run": _state["last_run"],
        "next_run": _state["next_run"],
        "total":    _state["total"],
        "done":     _state["done"],
        "current":  _state["current"],
        "errors":   _state["errors"][-10:],   # last 10 errors only
        "log":      _state["log"][-30:],       # last 30 log lines
        "report": {
            "enabled": _state["report_enabled"],
            "status": _state["report_status"],
            "last_run": _state["report_last_run"],
            "last_run_date": _state["report_last_run_date"],
            "next_run": _state["report_next_run"],
            "done": _state["report_done"],
            "total": _state["report_total"],
            "errors": _state["report_errors"][-10:],
        },
        "ai_report": {
            "enabled": _state["ai_report_enabled"],
            "status": _state["ai_report_status"],
            "last_run": _state["ai_report_last_run"],
            "last_run_date": _state["ai_report_last_run_date"],
            "next_run": _state["ai_report_next_run"],
            "done": _state["ai_report_done"],
            "total": _state["ai_report_total"],
            "errors": _state["ai_report_errors"][-10:],
        },
    }


@router.post("/config")
def update_config(payload: SchedulerConfigIn):
    """Enable or disable background scheduler tasks."""
    if payload.refresh_enabled is not None:
        _state["refresh_enabled"] = payload.refresh_enabled
    if payload.report_enabled is not None:
        _state["report_enabled"] = payload.report_enabled
    if payload.ai_report_enabled is not None:
        _state["ai_report_enabled"] = payload.ai_report_enabled
    _save_state()
    return {
        "ok": True,
        "refresh_enabled": _state["refresh_enabled"],
        "report_enabled": _state["report_enabled"],
        "ai_report_enabled": _state["ai_report_enabled"],
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


@router.post("/ai-report/run")
async def trigger_ai_report_run():
    """Manually trigger AI portfolio decision report generation (non-blocking)."""
    if _state["ai_report_status"] == "running":
        raise HTTPException(status_code=409, detail="AI report generation already in progress.")
    asyncio.create_task(_do_ai_report_refresh())
    return {"ok": True, "message": "AI portfolio report generation started in background."}
