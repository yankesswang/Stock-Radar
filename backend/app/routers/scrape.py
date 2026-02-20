"""
Scrape router — lets users add a ticker to a group and trigger data scraping.

POST /api/scrape          → start a background scrape job
GET  /api/scrape          → list available industry groups
GET  /api/scrape/{job_id} → poll job status
"""
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

# Ensure project root is on sys.path so scraper imports work
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import STOCK_LIST  # noqa: E402 – imported after path fix

router = APIRouter()

# In-memory job store (keyed by job_id)
_jobs: Dict[str, Dict[str, Any]] = {}

KNOWN_GROUPS: list[str] = list(STOCK_LIST.keys())


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
    """Execute ratio + forecast scrapers then compute valuations."""
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

        # ── Step 2: PE Median (via fullratio.com) ───────────────────────────
        step(f"Scraping PE median for {ticker} from fullratio.com…")
        from scripts.scrape_fullratio_pe import scrape_ticker as fr_scrape  # noqa: PLC0415
        from src.db_service import save_pe_median  # noqa: PLC0415

        fr_result = fr_scrape(ticker)
        if fr_result:
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

        # ── Step 3: Forecast ────────────────────────────────────────────────
        step(f"Scraping analyst forecasts for {ticker}…")
        from scripts.scrape_forecast import scrape_forecast, save_to_db  # noqa: PLC0415

        forecast_data = scrape_forecast(ticker)
        if forecast_data:
            save_to_db(ticker, forecast_data)
            step("Saved forecast data to database.")
        else:
            step("No forecast data available — skipped.")

        # ── Step 4: Valuations ──────────────────────────────────────────────
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

@router.get("")
def list_groups():
    """Return available industry groups."""
    return {"groups": KNOWN_GROUPS}


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

    if group not in KNOWN_GROUPS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown group '{group}'. Available: {', '.join(KNOWN_GROUPS)}",
        )

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
