"""
Data refresh scheduler.

Runs in the background as an asyncio task (started via FastAPI lifespan)。
管線與節奏：

  weekly refresh   每 7 天，全部 ticker：ratios → PE median → forecast → statistics → valuations
                   （ratios 那步同時寫進 metrics 表，所以 metrics 是新的）
  daily report     每天 08:00，各投組的每日體檢報告
  statements       每天 04:00，補抓最舊的損益表／現金流量表（見下）
  prices           每天 05:30，把最新一筆估值快照的現價換成真收盤價（見下）

statistics 這步是 2026-09 才補的。在那之前它不在任何排程裡，只能手動跑
`scripts/scrape_statistics.py`，結果整張表停在兩個月前 —— 同業比較的利潤率、
成長預估、Altman/Piotroski、RSI、目標價全都吃這張表。

它跟其他步驟一起走每週那一輪就夠了（這些是季度財報等級的數字，不是報價）。
之所以塞得進每週那輪，是因為改抓頁面自己的 JSON 端點（StatisticsScraper.DATA_URL）：
15KB 而不是 123KB 的 HTML，不必再用 10–30 秒的重延遲。

要單獨補（剛加了新股、或某批特別舊）用 POST /api/scheduler/statistics/run，
它挑最舊的 N 檔，不記進度、每次重挑，所以中斷重啟都不會亂。

Endpoints:
  GET  /api/scheduler/status          – current state (last run, next run, progress)
  POST /api/scheduler/run             – trigger an immediate weekly refresh
  POST /api/scheduler/report/run      – trigger daily portfolio reports
  POST /api/scheduler/ai-report/run   – trigger AI per-stock decision cards
  POST /api/scheduler/statistics/run  – trigger a statistics top-up batch
  POST /api/scheduler/prices/run      – trigger an immediate price refresh
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

# 手動補抓 statistics 用。走 JSON 端點之後每次只有 15KB，間隔可以從原本的
# 10–30 秒收到 3–6 秒 —— 對方的總負載仍遠低於爬 HTML（8 倍頻寬差）。
STATS_BATCH_SIZE = 60
STATS_MIN_DELAY, STATS_MAX_DELAY = 3, 6

# 損益表 / 現金流量表補抓：2026-09 補的第二條。這兩張表整批停在 2026-02，而且
# 只覆蓋 181/262 檔 —— DCF 的「五年常態化 FCF margin」、SBC 稀釋、利息還原、
# 歷史 EV/FCF 退出倍數全都吃這兩張表，沒有就退回單點 spot 值。
# 一檔要抓兩頁，所以批量開一半；15 檔 × 2 頁 × 10–30 秒 ≈ 5–15 分鐘。
STATEMENTS_DAILY_HOUR = 4
STATEMENTS_BATCH_SIZE = 15
STATEMENTS_MAX_AGE_DAYS = 100          # 超過這個天數就排進補抓佇列

# 現價每日更新：valuations.current_price 原本只由每週那輪的比率表爬蟲寫入，抄的是
# stockanalysis 的「Last Close Price」，而爬蟲排在台北凌晨（美股盤中），所以那個價
# 最好也落後一個交易日，最壞會停整整一週以上。valuation_status / valuation_diff_pct
# 是拿它跟合理價比出來的，價一舊，高估／低估跟著錯。05:30 挑在美東收盤（台北 04:00／
# 夏令 05:00）之後、08:00 的每日報告之前，讓報告讀到的是當天的收盤。
PRICES_DAILY_HOUR = 5
PRICES_DAILY_MINUTE = 30
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
    "stats_status":        "idle",      # idle | running
    "stats_enabled":       True,
    "stats_last_run":      None,
    "stats_last_run_date": None,
    "stats_next_run":      None,
    "stats_done":          0,
    "stats_total":         0,
    "stats_current":       None,
    "stats_errors":        [],
    "stats_oldest":        None,        # 全庫最舊的 statistics 日期＝這條管線的體檢指標
    "stats_missing":       0,           # 完全沒有 statistics 的檔數
    "statements_status":        "idle",  # idle | running
    "statements_enabled":       True,
    "statements_last_run":      None,
    "statements_last_run_date": None,
    "statements_next_run":      None,
    "statements_done":          0,
    "statements_total":         0,
    "statements_current":       None,
    "statements_errors":        [],
    "statements_oldest":        None,   # 全庫最舊的損益/現金流日期
    "statements_missing":       0,      # 完全沒有這兩張表的檔數
    "prices_status":        "idle",     # idle | running
    "prices_enabled":       True,
    "prices_last_run":      None,
    "prices_last_run_date": None,
    "prices_next_run":      None,
    "prices_updated":       0,          # 上一輪實際寫入的檔數
    "prices_total":         0,
    "prices_missing":       [],         # 抓不到報價的（多半是下市／改名）
}

_refresh_task: asyncio.Task | None = None


class SchedulerConfigIn(BaseModel):
    refresh_enabled: bool | None = None
    report_enabled: bool | None = None
    ai_report_enabled: bool | None = None
    stats_enabled: bool | None = None
    statements_enabled: bool | None = None
    prices_enabled: bool | None = None


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
        _state["stats_enabled"] = bool(saved.get("stats_enabled", True))
        _state["statements_enabled"] = bool(saved.get("statements_enabled", True))
        _state["statements_last_run"] = saved.get("statements_last_run")
        _state["statements_last_run_date"] = saved.get("statements_last_run_date")
        _state["statements_next_run"] = saved.get("statements_next_run")
        _state["stats_last_run"] = saved.get("stats_last_run")
        _state["stats_last_run_date"] = saved.get("stats_last_run_date")
        _state["stats_next_run"] = saved.get("stats_next_run")
        _state["prices_enabled"] = bool(saved.get("prices_enabled", True))
        _state["prices_last_run"] = saved.get("prices_last_run")
        _state["prices_last_run_date"] = saved.get("prices_last_run_date")
        _state["prices_next_run"] = saved.get("prices_next_run")
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
            "stats_enabled": _state["stats_enabled"],
            "stats_last_run": _state["stats_last_run"],
            "stats_last_run_date": _state["stats_last_run_date"],
            "stats_next_run": _state["stats_next_run"],
            "statements_enabled": _state["statements_enabled"],
            "statements_last_run": _state["statements_last_run"],
            "statements_last_run_date": _state["statements_last_run_date"],
            "statements_next_run": _state["statements_next_run"],
            "prices_enabled": _state["prices_enabled"],
            "prices_last_run": _state["prices_last_run"],
            "prices_last_run_date": _state["prices_last_run_date"],
            "prices_next_run": _state["prices_next_run"],
        }))
    except Exception:
        pass


def _calc_next_run_at(hour: int, now: datetime | None = None, minute: int = 0) -> datetime:
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return target


def _calc_next_report_run(now: datetime | None = None) -> datetime:
    return _calc_next_run_at(REPORT_DAILY_HOUR, now)


def _should_run_at(now: datetime, hour: int, last_run_date: str | None, is_running: bool,
                   minute: int = 0) -> tuple[bool, str]:
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

    due = (now.hour, now.minute) >= (hour, minute)

    # First run ever
    if not last_run_date:
        if due:
            return True, "first-run"
        return False, "before-scheduled-hour"

    # Parse last run date; if invalid treat as stale and run now
    try:
        last_d = date.fromisoformat(last_run_date)
    except Exception:
        return True, "invalid-last-run-date"

    gap_days = (now.date() - last_d).days
    if due:
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


# statistics 沒有自己的每日排程 —— 併進每週那一輪（_scrape_one 的第 4 步）。
# 這裡留下的是手動補抓的路徑與體檢數字。


def _should_run_prices(now: datetime) -> tuple[bool, str]:
    return _should_run_at(
        now, PRICES_DAILY_HOUR,
        _state.get("prices_last_run_date"),
        _state.get("prices_status") == "running",
        minute=PRICES_DAILY_MINUTE,
    )


def _should_run_statements(now: datetime) -> tuple[bool, str]:
    return _should_run_at(
        now, STATEMENTS_DAILY_HOUR,
        _state.get("statements_last_run_date"),
        _state.get("statements_status") == "running",
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

    # 2. PE median ＋ PE 歷史檔（PE 歷史分頁讀的是 JSON 檔，不是 DB；
    #    2026-09 以前這裡只存中位數、把整頁歷史丟掉，結果 430 多檔的 PE 分頁一直是空的）
    try:
        from scripts.scrape_fullratio_pe import scrape_ticker as fr_scrape, write_json as fr_write
        from src.db_service import save_pe_median
        fr = fr_scrape(ticker)
        if fr:
            fr_write(fr)
            pe = fr.get("summary", {}).get("Median") or fr.get("pe_median")
            if pe:
                save_pe_median(ticker, float(pe), snapshot)
    except Exception:
        pass

    # 3. Forecast
    try:
        # JSON 也要存：分析師預估頁、DCF 輸入、摘要、產業比較、上修下修追蹤全讀這個檔，
        # 不讀 DB。2026-09 以前這裡只存 DB，350 檔整頁空白、其餘停在 7/8 月。
        from scripts.scrape_forecast import scrape_forecast, save_to_db, save_ticker_json
        fd = scrape_forecast(ticker)
        if fd:
            save_ticker_json(ticker, fd)
            save_to_db(ticker, fd)
    except Exception:
        pass

    # 4. Statistics（利潤率、成長預估、Altman/Piotroski、RSI、目標價…）
    #    走頁面的 JSON 端點，解析不出來才退回爬 HTML —— 見 StatisticsScraper.DATA_URL
    try:
        from src.scrapers.statistics_scraper import StatisticsScraper
        sc = StatisticsScraper()
        sdata = sc.scrape_statistics(ticker)
        if sdata:
            sc.save(ticker, sdata)
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
    # statistics 是這輪的第 4 步，跑完順手更新它的體檢數字
    try:
        fresh = await loop.run_in_executor(None, _stats_freshness)
        _state["stats_oldest"] = fresh["oldest"]
        _state["stats_missing"] = fresh["missing"]
        _state["stats_last_run"] = now.isoformat(timespec="seconds")
    except Exception as exc:
        _log(f"Statistics freshness probe failed: {exc}")
    _state.update({
        "status":    "idle",
        "last_run":  now.isoformat(timespec="seconds"),
        "next_run":  next_run.isoformat(timespec="seconds"),
        "stats_next_run": next_run.isoformat(timespec="seconds"),
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


# ── statistics 補抓（每天一小批，挑最舊的）──────────────────────────────────

def _stats_freshness() -> dict[str, Any]:
    """全庫 statistics 的新舊分布 —— 這條管線有沒有在跑，看這個就知道。"""
    from sqlalchemy import func as _f
    from ..database import SessionLocal
    from ..models import Statistic, Stock

    db = SessionLocal()
    try:
        latest = (
            db.query(Statistic.stock_id, _f.max(Statistic.snapshot_date).label("d"))
            .group_by(Statistic.stock_id)
            .subquery()
        )
        rows = (
            db.query(Stock.ticker, latest.c.d)
            .outerjoin(latest, latest.c.stock_id == Stock.id)
            .all()
        )
    finally:
        db.close()

    dates = [d for _, d in rows if d]
    return {
        "oldest": str(min(dates)) if dates else None,
        "newest": str(max(dates)) if dates else None,
        "missing": sum(1 for _, d in rows if not d),
        "total": len(rows),
    }


def _stats_batch(limit: int) -> list[str]:
    """挑最舊的 N 檔（完全沒有的排最前面）。

    不記進度、每次重新挑 —— 跑完的那批 snapshot_date 變成今天就自動排到最後，
    所以中斷、重啟、手動插隊都不會亂掉。
    """
    from sqlalchemy import func as _f
    from ..database import SessionLocal
    from ..models import Statistic, Stock

    db = SessionLocal()
    try:
        latest = (
            db.query(Statistic.stock_id, _f.max(Statistic.snapshot_date).label("d"))
            .group_by(Statistic.stock_id)
            .subquery()
        )
        rows = (
            db.query(Stock.ticker, latest.c.d)
            .outerjoin(latest, latest.c.stock_id == Stock.id)
            .all()
        )
    finally:
        db.close()

    today = date.today()
    # 今天已經抓過的不再排進來，免得同一天手動觸發兩次就重抓同一批
    pending = [(d, t) for t, d in rows if d != today]
    pending.sort(key=lambda x: (x[0] is not None, x[0] or date.min))
    return [t for _, t in pending[:limit]]


def _scrape_statistics_one(ticker: str) -> None:
    """抓一檔並寫進 DB（scraper.save 會同時落 JSON 與 statistics 表）。"""
    from src.scrapers.statistics_scraper import StatisticsScraper
    scraper = StatisticsScraper()
    data = scraper.scrape_statistics(ticker)
    if not data:
        raise RuntimeError("no data returned")
    scraper.save(ticker, data)


async def _do_statistics_refresh(limit: int = STATS_BATCH_SIZE, scheduled: bool = True) -> None:
    """把最舊的一批 statistics 補到今天。

    節流用 asyncio.sleep 而不是 scraper.run() 內建的 time.sleep —— 後者會讓
    executor 的執行緒空等十幾分鐘；這裡只有 HTTP 那一下進 executor。

    scheduled=False（手動觸發）不會蓋掉 last_run_date —— 那個欄位是排程用來
    判斷「今天的班跑了沒」的，手動補幾檔不該讓當天的排程整個被跳過。
    """
    import random

    if _state["stats_status"] == "running":
        return

    loop = asyncio.get_event_loop()
    tickers = await loop.run_in_executor(None, _stats_batch, limit)
    if not tickers:
        _log("Statistics: 全部都是今天的，沒有要補的。")
        return

    _state.update({
        "stats_status": "running",
        "stats_total": len(tickers),
        "stats_done": 0,
        "stats_current": None,
        "stats_errors": [],
    })
    _log(f"Statistics top-up 開始 —— {len(tickers)} 檔（挑最舊的）。")

    for i, ticker in enumerate(tickers):
        _state["stats_current"] = ticker
        try:
            await loop.run_in_executor(None, _scrape_statistics_one, ticker)
            _log(f"✓ stats {ticker}")
        except Exception as exc:
            _state["stats_errors"].append(f"{ticker}: {exc}")
            _log(f"✗ stats {ticker}: {exc}")
        _state["stats_done"] += 1
        if i < len(tickers) - 1:
            await asyncio.sleep(random.uniform(STATS_MIN_DELAY, STATS_MAX_DELAY))

    fresh = await loop.run_in_executor(None, _stats_freshness)
    now = datetime.now()
    _state.update({
        "stats_status": "idle",
        "stats_current": None,
        "stats_last_run": now.isoformat(timespec="seconds"),
        "stats_oldest": fresh["oldest"],
        "stats_missing": fresh["missing"],
    })
    if scheduled:
        _state["stats_last_run_date"] = now.date().isoformat()
    _save_state()
    _log(
        f"Statistics top-up 完成。ok={_state['stats_done'] - len(_state['stats_errors'])}"
        f"/{_state['stats_total']} 最舊={fresh['oldest']} 缺={fresh['missing']}"
    )


# ── 損益表 / 現金流量表補抓（每天一小批，挑最舊的）────────────────────────────

def _statements_paths(ticker: str) -> dict[str, Path | None]:
    fin = PROJECT_ROOT / "data" / "financials"
    out: dict[str, Path | None] = {}
    for kind in ("income", "cashflow"):
        files = sorted(fin.glob(f"{ticker.upper()}_{kind}_*.csv"), reverse=True)
        out[kind] = files[0] if files else None
    return out


def _statements_age(ticker: str) -> int | None:
    """這檔最舊的那張表幾天沒更新；缺任何一張就回 None（排最前面）。"""
    import re as _re

    ages = []
    for path in _statements_paths(ticker).values():
        if path is None:
            return None
        m = _re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        if not m:
            return None
        ages.append((date.today() - date.fromisoformat(m.group(1))).days)
    return max(ages)


def _all_tickers() -> list[str]:
    from ..database import SessionLocal
    from ..models import Stock

    db = SessionLocal()
    try:
        return sorted(t for (t,) in db.query(Stock.ticker).all() if t)
    finally:
        db.close()


def _statements_freshness() -> dict[str, Any]:
    tickers = _all_tickers()
    ages = [(_statements_age(t), t) for t in tickers]
    dated = [a for a, _ in ages if a is not None]
    return {
        "oldest": max(dated) if dated else None,      # 天數，不是日期
        "missing": sum(1 for a, _ in ages if a is None),
        "total": len(tickers),
    }


def _statements_batch(limit: int) -> list[str]:
    """挑最舊的 N 檔（完全沒有的排最前面）。

    和 statistics 同一套策略：不記進度、每次重新挑，跑完的自然排到隊尾，
    所以中斷、重啟、手動插隊都不會亂掉。
    """
    scored = []
    for t in _all_tickers():
        age = _statements_age(t)
        if age is None:
            scored.append((-1, t))                     # 缺檔優先
        elif age > STATEMENTS_MAX_AGE_DAYS:
            scored.append((-age, t))
    scored.sort()
    return [t for _, t in scored[:limit]]


def _scrape_statements_one(ticker: str) -> None:
    """抓一檔的損益表與現金流量表並落 CSV。

    不用兩個 scraper 的 save_*() —— 它們把日期綁在模組匯入時的 DATE_STR 上，
    對一支跑幾週的 server 來說那個日期會凍在啟動當天，檔名就會謊報新鮮度。
    這裡自己用今天的日期落檔。
    """
    from scripts.scrape_cashflow import scrape_cashflow
    from scripts.scrape_income import scrape_income

    out_dir = PROJECT_ROOT / "data" / "financials"
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    for kind, scrape in (("income", scrape_income), ("cashflow", scrape_cashflow)):
        df = scrape(ticker)
        if df is None or df.empty:
            raise RuntimeError(f"{kind}: no data returned")
        df.to_csv(out_dir / f"{ticker.upper()}_{kind}_{today}.csv", index=False)


def _recompute_dcf(tickers: list[str]) -> int:
    """重算這批的 DCF —— 新的報表一落地，估值就該跟著換，不必等下一輪。"""
    from src.analysis.dcf_calculator import DCFCalculator

    calc = DCFCalculator(data_dir=PROJECT_ROOT / "data")
    ok = 0
    for t in tickers:
        try:
            if calc.compute_and_save(t, force=True):
                ok += 1
        except Exception as exc:  # noqa: BLE001 — 單檔失敗不該擋住整批
            _log(f"✗ dcf {t}: {exc}")
    return ok


async def _do_prices_refresh(scheduled: bool = True) -> None:
    """把每檔最新一筆估值快照的現價換成真收盤價，並重算高估／低估。

    只改價格與由價格衍生的四個欄位，不碰 EPS、PE 中位數、合理價那些爬蟲的產物，
    也不回頭改歷史快照。抓不到報價的（下市、改名）保留原值 —— 絕不歸零，
    0 會被下游當成「合理價 $0」。
    """
    if _state["prices_status"] == "running":
        return

    _state.update({"prices_status": "running", "prices_updated": 0, "prices_total": 0})
    _log("現價更新開始 —— 重抓全庫最新快照的收盤價。")

    loop = asyncio.get_event_loop()
    try:
        from scripts.refresh_prices import refresh
        r = await loop.run_in_executor(None, refresh)
    except Exception as exc:
        _state["prices_status"] = "idle"
        _log(f"✗ 現價更新失敗：{exc}")
        return

    now = datetime.now()
    _state.update({
        "prices_status":   "idle",
        "prices_last_run": now.isoformat(timespec="seconds"),
        "prices_updated":  r["updated"],
        "prices_total":    r["total"],
        "prices_missing":  r["missing"][:20],
    })
    if scheduled:
        _state["prices_last_run_date"] = now.date().isoformat()
        _state["prices_next_run"] = _calc_next_run_at(
            PRICES_DAILY_HOUR, now, PRICES_DAILY_MINUTE
        ).isoformat(timespec="seconds")
    _save_state()
    _log(
        f"現價更新完成：{r['total']} 檔中更新 {r['updated']}、"
        f"無變動 {r['unchanged']}、抓不到 {len(r['missing'])}"
    )


async def _do_statements_refresh(
    limit: int = STATEMENTS_BATCH_SIZE, scheduled: bool = True
) -> None:
    """把最舊的一批損益表 / 現金流量表補到今天，然後重算它們的 DCF。"""
    import random

    if _state["statements_status"] == "running":
        return

    loop = asyncio.get_event_loop()
    tickers = await loop.run_in_executor(None, _statements_batch, limit)
    if not tickers:
        _log(f"Statements: 全部都在 {STATEMENTS_MAX_AGE_DAYS} 天內，沒有要補的。")
        return

    _state.update({
        "statements_status": "running",
        "statements_total": len(tickers),
        "statements_done": 0,
        "statements_current": None,
        "statements_errors": [],
    })
    _log(f"Statements top-up 開始 —— {len(tickers)} 檔（挑最舊的）。")

    refreshed = []
    for i, ticker in enumerate(tickers):
        _state["statements_current"] = ticker
        try:
            await loop.run_in_executor(None, _scrape_statements_one, ticker)
            refreshed.append(ticker)
            _log(f"✓ statements {ticker}")
        except Exception as exc:
            _state["statements_errors"].append(f"{ticker}: {exc}")
            _log(f"✗ statements {ticker}: {exc}")
        _state["statements_done"] += 1
        if i < len(tickers) - 1:
            await asyncio.sleep(random.uniform(STATS_MIN_DELAY, STATS_MAX_DELAY))

    if refreshed:
        done = await loop.run_in_executor(None, _recompute_dcf, refreshed)
        _log(f"DCF 重算完成 {done}/{len(refreshed)}")

    fresh = await loop.run_in_executor(None, _statements_freshness)
    now = datetime.now()
    _state.update({
        "statements_status": "idle",
        "statements_current": None,
        "statements_last_run": now.isoformat(timespec="seconds"),
        "statements_oldest": fresh["oldest"],
        "statements_missing": fresh["missing"],
    })
    if scheduled:
        _state["statements_last_run_date"] = now.date().isoformat()
        _state["statements_next_run"] = _calc_next_run_at(
            STATEMENTS_DAILY_HOUR, now
        ).isoformat(timespec="seconds")
    _save_state()
    _log(
        f"Statements top-up 完成。ok={_state['statements_done'] - len(_state['statements_errors'])}"
        f"/{_state['statements_total']} 最舊={fresh['oldest']}天 缺={fresh['missing']}"
    )


# ── Background scheduler loop ─────────────────────────────────────────────────

async def scheduler_loop() -> None:
    """Started once in FastAPI lifespan. Checks every hour."""
    _load_state()
    if not _state.get("report_next_run"):
        _state["report_next_run"] = _calc_next_report_run().isoformat(timespec="seconds")
        _save_state()
    # statistics 跟著每週那輪跑，下次時間就是 refresh 的下次時間
    _state["stats_next_run"] = _state.get("next_run")
    if not _state.get("statements_next_run"):
        _state["statements_next_run"] = _calc_next_run_at(
            STATEMENTS_DAILY_HOUR
        ).isoformat(timespec="seconds")
        _save_state()
    if not _state.get("prices_next_run"):
        _state["prices_next_run"] = _calc_next_run_at(
            PRICES_DAILY_HOUR, minute=PRICES_DAILY_MINUTE
        ).isoformat(timespec="seconds")
        _save_state()
    # 開機就量一次 statistics 的新舊 —— 前端要能立刻說出「資料多舊」
    try:
        fresh = await asyncio.get_event_loop().run_in_executor(None, _stats_freshness)
        _state["stats_oldest"] = fresh["oldest"]
        _state["stats_missing"] = fresh["missing"]
    except Exception as exc:
        _log(f"Statistics freshness probe failed: {exc}")
    try:
        fresh = await asyncio.get_event_loop().run_in_executor(None, _statements_freshness)
        _state["statements_oldest"] = fresh["oldest"]
        _state["statements_missing"] = fresh["missing"]
    except Exception as exc:
        _log(f"Statements freshness probe failed: {exc}")
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

        if _state.get("prices_enabled", True):
            should_run, reason = _should_run_prices(now)
            if should_run:
                _log(f"現價更新觸發（{reason}）—— 開始重抓全庫收盤價。")
                asyncio.create_task(_do_prices_refresh())

        if _state.get("statements_enabled", True):
            should_run, reason = _should_run_statements(now)
            if should_run:
                _log(f"Statements trigger ({reason}) —— 開始補抓損益/現金流最舊的一批。")
                asyncio.create_task(_do_statements_refresh())

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
        "statistics": {
            "enabled": _state["stats_enabled"],
            "status": _state["stats_status"],
            "last_run": _state["stats_last_run"],
            "last_run_date": _state["stats_last_run_date"],
            "next_run": _state["stats_next_run"],
            "done": _state["stats_done"],
            "total": _state["stats_total"],
            "current": _state["stats_current"],
            "oldest": _state["stats_oldest"],
            "missing": _state["stats_missing"],
            "batch_size": STATS_BATCH_SIZE,
            "errors": _state["stats_errors"][-10:],
        },
        "statements": {
            "enabled": _state["statements_enabled"],
            "status": _state["statements_status"],
            "last_run": _state["statements_last_run"],
            "last_run_date": _state["statements_last_run_date"],
            "next_run": _state["statements_next_run"],
            "done": _state["statements_done"],
            "total": _state["statements_total"],
            "current": _state["statements_current"],
            "oldest_age_days": _state["statements_oldest"],
            "missing": _state["statements_missing"],
            "batch_size": STATEMENTS_BATCH_SIZE,
            "errors": _state["statements_errors"][-10:],
        },
        "prices": {
            "enabled": _state["prices_enabled"],
            "status": _state["prices_status"],
            "last_run": _state["prices_last_run"],
            "last_run_date": _state["prices_last_run_date"],
            "next_run": _state["prices_next_run"],
            "updated": _state["prices_updated"],
            "total": _state["prices_total"],
            "missing": _state["prices_missing"],
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
    if payload.stats_enabled is not None:
        _state["stats_enabled"] = payload.stats_enabled
    if payload.statements_enabled is not None:
        _state["statements_enabled"] = payload.statements_enabled
    if payload.prices_enabled is not None:
        _state["prices_enabled"] = payload.prices_enabled
    _save_state()
    return {
        "ok": True,
        "refresh_enabled": _state["refresh_enabled"],
        "report_enabled": _state["report_enabled"],
        "ai_report_enabled": _state["ai_report_enabled"],
        "stats_enabled": _state["stats_enabled"],
        "statements_enabled": _state["statements_enabled"],
        "prices_enabled": _state["prices_enabled"],
    }


@router.post("/prices/run")
async def trigger_prices_run():
    """手動重抓全庫現價並重算高估／低估，非阻塞。"""
    if _state["prices_status"] == "running":
        raise HTTPException(status_code=409, detail="現價更新正在進行中。")
    asyncio.create_task(_do_prices_refresh(scheduled=False))
    return {"ok": True, "message": "現價更新已在背景開始。"}


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


@router.post("/statistics/run")
async def trigger_statistics_run(limit: int = STATS_BATCH_SIZE):
    """手動補抓一批 statistics（挑最舊的），非阻塞。

    平常不需要呼叫 —— statistics 跟著每週的 refresh 一起更新。
    這個端點是給「剛加了新股」「某批特別舊要立刻補」用的。
    走 JSON 端點之後 270 檔約 15–30 分鐘（間隔 3–6 秒）。
    """
    if _state["stats_status"] == "running":
        raise HTTPException(status_code=409, detail="Statistics refresh already in progress.")
    limit = max(1, min(limit, 300))
    asyncio.create_task(_do_statistics_refresh(limit, scheduled=False))
    return {"ok": True, "message": f"Statistics top-up started for up to {limit} tickers."}


@router.post("/statements/run")
async def trigger_statements_run(limit: int = STATEMENTS_BATCH_SIZE):
    """手動補抓一批損益表 / 現金流量表（挑最舊的），非阻塞。

    一檔要抓兩頁，來源要 10–30 秒的間隔 —— 全庫 262 檔約 1.5–2.5 小時。
    跑完的那批會順手重算 DCF。
    """
    if _state["statements_status"] == "running":
        raise HTTPException(status_code=409, detail="Statements refresh already in progress.")
    limit = max(1, min(limit, 300))
    asyncio.create_task(_do_statements_refresh(limit, scheduled=False))
    return {"ok": True, "message": f"Statements top-up started for up to {limit} tickers."}


@router.post("/ai-report/run")
async def trigger_ai_report_run():
    """Manually trigger AI portfolio decision report generation (non-blocking)."""
    if _state["ai_report_status"] == "running":
        raise HTTPException(status_code=409, detail="AI report generation already in progress.")
    asyncio.create_task(_do_ai_report_refresh())
    return {"ok": True, "message": "AI portfolio report generation started in background."}
