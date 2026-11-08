"""
Scrape router — lets users add a ticker to a group and trigger data scraping.

POST /api/scrape          → start a background scrape job
GET  /api/scrape          → list available industry groups
GET  /api/scrape/{job_id} → poll job status
POST /api/scrape/mimi-all → auto-scrape all mimi watchlist tickers (sequential, 6h cooldown)
"""
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Industry, MimiWatchlist

# Ensure project root is on sys.path so scraper imports work
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import STOCK_LIST  # noqa: E402 – imported after path fix

router = APIRouter()

# In-memory job store (keyed by job_id)
_jobs: Dict[str, Dict[str, Any]] = {}

# Cooldown guard for bulk mimi scrape
_mimi_scrape_started_at: Optional[datetime] = None
_MIMI_SCRAPE_COOLDOWN_HOURS = 6

STATIC_GROUPS: list[str] = list(STOCK_LIST.keys())


# ---------------------------------------------------------------------------
# Bulk mimi-all scrape (3-worker parallel)
# ---------------------------------------------------------------------------

_MIMI_WORKERS = 3

def _scrape_one(ticker: str, group: str) -> None:
    job_id = f"mimi_{ticker.lower()}"
    _jobs[job_id] = {
        "job_id": job_id,
        "ticker": ticker,
        "group": group,
        "status": "running",
        "steps": [f"Auto-scrape for {ticker}…"],
        "error": None,
        "started_at": datetime.now().isoformat(),
    }
    _run_scrape(job_id, ticker, group)


def _run_scrape_all(tickers_with_groups: list) -> None:
    """Scrape all mimi tickers with a 3-worker thread pool."""
    with ThreadPoolExecutor(max_workers=_MIMI_WORKERS) as pool:
        pool.map(lambda t: _scrape_one(t[0], t[1]), tickers_with_groups)


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class ScrapeRequest(BaseModel):
    ticker: str
    group: str


# ---------------------------------------------------------------------------
# Background scraping function (runs in a thread pool via BackgroundTasks)
# ---------------------------------------------------------------------------

def _run_scrape(job_id: str, ticker: str, group: str) -> None:
    """Execute ratios, statistics, PE, forecast scrapers then compute valuations."""
    job = _jobs[job_id]

    def step(msg: str) -> None:
        job["steps"].append(msg)

    try:
        snapshot_date = date.today()

        # ── Step 1: Ratios ──────────────────────────────────────────────────
        step(f"Scraping financial ratios for {ticker}…")
        from scripts.scrape_ratios import scrape_ratios, save_ratios  # noqa: PLC0415

        df = scrape_ratios(ticker)
        if df is None:
            raise RuntimeError(f"Could not scrape ratios for {ticker}. "
                               "Check the ticker symbol and try again.")
        save_ratios(ticker, df, industry=group)
        step(f"Saved {len(df)} ratio metrics to database.")

        # ── Step 2: Statistics ──────────────────────────────────────────────
        step(f"Scraping key statistics for {ticker}…")
        from src.scrapers.statistics_scraper import StatisticsScraper  # noqa: PLC0415

        statistics_scraper = StatisticsScraper()
        statistics_data = statistics_scraper.scrape_statistics(ticker)
        if statistics_data is None:
            raise RuntimeError(
                f"Could not scrape statistics for {ticker}. "
                "The stock page may be unavailable or blocked by the data source."
            )
        statistics_scraper.save(ticker, statistics_data)
        sections = len(statistics_data.get("sections", []))
        items = sum(len(section.get("items", [])) for section in statistics_data.get("sections", []))
        step(f"Saved {items} statistics across {sections} sections.")

        # ── Step 3: Metrics Breakdown ───────────────────────────────────────
        step(f"Scraping metrics breakdown for {ticker}…")
        from src.scrapers.metrics_scraper import MetricsScraper  # noqa: PLC0415

        metrics_scraper = MetricsScraper()
        metrics_df = metrics_scraper.scrape_metrics(ticker)
        if metrics_df is not None:
            metrics_scraper.save(ticker, metrics_df)
            metric_sections = metrics_df["Category"].nunique() if "Category" in metrics_df.columns else 0
            step(f"Saved {len(metrics_df)} metrics breakdown rows across {metric_sections} sections.")
        else:
            step("No metrics breakdown data available — skipped.")

        # ── Step 4: PE Median (via fullratio.com) ───────────────────────────
        step(f"Scraping PE median for {ticker} from fullratio.com…")
        from scripts.scrape_fullratio_pe import scrape_ticker as fr_scrape, write_json as fr_write  # noqa: PLC0415
        from src.db_service import save_pe_median  # noqa: PLC0415

        fr_result = fr_scrape(ticker)
        if fr_result:
            fr_write(fr_result)
            summary = fr_result.get("summary", {})
            # Prefer the site's own "Median"; fall back to our IQR-filtered median
            pe_median = summary.get("Median") or fr_result.get("pe_median")
            if pe_median:
                save_pe_median(ticker, float(pe_median), snapshot_date)
                today_pe = summary.get("Today", "N/A")
                avg_pe   = summary.get("Average", "N/A")
                step(
                    f"PE median saved: {pe_median} "
                    f"(today={today_pe}, avg={avg_pe}, "
                    f"min={summary.get('Minimum','N/A')}, max={summary.get('Maximum','N/A')})"
                )
            else:
                step("fullratio returned data but no Median value — skipped.")
        else:
            step("No PE data found on fullratio.com — skipped.")

        # ── Step 5: Forecast ────────────────────────────────────────────────
        step(f"Scraping analyst forecasts for {ticker}…")
        from scripts.scrape_forecast import scrape_forecast, save_to_db, save_ticker_json  # noqa: PLC0415

        forecast_data = scrape_forecast(ticker)
        if forecast_data:
            save_to_db(ticker, forecast_data)
            save_ticker_json(ticker, forecast_data)
            step("Saved forecast data to database and JSON file.")
        else:
            step("No forecast data available — skipped.")

        # ── Step 6: Valuations ──────────────────────────────────────────────
        step("Computing valuations…")
        from src.db_service import compute_valuations_for_date  # noqa: PLC0415

        compute_valuations_for_date(snapshot_date)
        step("Valuation computation complete.")

        job["status"] = "completed"

    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(exc)
        step(f"Error: {exc}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _is_valid_group_name(group: str) -> bool:
    """Allow user-defined group names while rejecting empty/control-heavy input."""
    return bool(group) and len(group) <= 50 and not any(c in group for c in "\r\n\t")


@router.post("/mimi-all")
async def scrape_mimi_all(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Auto-scrape all mimi watchlist tickers sequentially. Enforces a 6h cooldown."""
    global _mimi_scrape_started_at

    if _mimi_scrape_started_at:
        elapsed_h = (datetime.now() - _mimi_scrape_started_at).total_seconds() / 3600
        if elapsed_h < _MIMI_SCRAPE_COOLDOWN_HOURS:
            return {
                "ok": False,
                "skipped": True,
                "message": f"Last bulk scrape started {elapsed_h:.1f}h ago — cooldown {_MIMI_SCRAPE_COOLDOWN_HOURS}h",
                "count": 0,
            }

    rows = db.query(MimiWatchlist).all()
    tickers_with_groups = []
    for r in rows:
        ticker: str = r.ticker  # type: ignore[assignment]
        if not ticker:
            continue
        sector: str = r.sector or ""  # type: ignore[assignment]
        tickers_with_groups.append((ticker, sector.strip() or "其他"))

    if not tickers_with_groups:
        return {"ok": False, "message": "No tickers in mimi watchlist", "count": 0}

    _mimi_scrape_started_at = datetime.now()
    background_tasks.add_task(_run_scrape_all, tickers_with_groups)

    return {
        "ok": True,
        "count": len(tickers_with_groups),
        "tickers": [t[0] for t in tickers_with_groups],
        "started_at": _mimi_scrape_started_at.isoformat(),
    }


@router.get("")
def list_groups(db: Session = Depends(get_db)):
    """Return available industry groups."""
    db_groups = [name for (name,) in db.query(Industry.name).order_by(Industry.name).all()]
    groups = sorted(set(STATIC_GROUPS) | set(db_groups))
    return {"groups": groups}


@router.post("")
async def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    """
    Validate ticker + group then kick off a background scrape job.
    Returns a job_id to poll with GET /api/scrape/{job_id}.
    """
    ticker = request.ticker.strip().upper()
    group = request.group.strip()

    # Basic ticker validation (letters/numbers, 1–10 chars)
    if not ticker or not all(c.isalnum() or c == "." for c in ticker) or len(ticker) > 10:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol.")

    if not _is_valid_group_name(group):
        raise HTTPException(status_code=400, detail="Group must be 1-50 characters.")

    # Dedup: if this ticker already has a running job, return it instead of
    # starting a second one — concurrent jobs hammer the data source and
    # trigger HTTP 429 rate limiting, which makes the whole add fail.
    for existing in _jobs.values():
        if existing["ticker"] == ticker and existing["status"] == "running":
            return {
                "job_id": existing["job_id"],
                "ticker": ticker,
                "group": existing["group"],
                "status": "running",
                "deduped": True,
            }

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "job_id": job_id,
        "ticker": ticker,
        "group": group,
        "status": "running",
        "steps": [f"Job started — scraping {ticker} ({group})…"],
        "error": None,
        "started_at": datetime.now().isoformat(),
    }

    background_tasks.add_task(_run_scrape, job_id, ticker, group)

    return {
        "job_id": job_id,
        "ticker": ticker,
        "group": group,
        "status": "running",
    }


@router.get("/{job_id}")
def get_scrape_status(job_id: str):
    """Poll the status of a scrape job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job
