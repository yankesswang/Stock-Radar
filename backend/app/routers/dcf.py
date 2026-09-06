"""
Serve pre-computed DCF valuation JSON for a given ticker.

JSON files live at  data/dcf/{TICKER}_dcf_{date}.json
Falls back to on-demand computation if no pre-computed file exists.
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter()

DCF_DIR: Path = settings.DATA_DIR / "dcf"

# A DCF older than this is recomputed on read. Twelve tickers were still serving
# a seven-month-old file because nothing ever invalidated the cache — including
# banks and delisted names the current model correctly declines to value, whose
# stale numbers looked exactly as authoritative as a fresh one.
MAX_CACHE_AGE_DAYS = 7


def _find_latest_json(ticker: str) -> Path | None:
    """Return the newest DCF JSON for *ticker*."""
    for pattern in (
        f"{ticker.upper()}_dcf_*.json",
        f"{ticker.lower()}_dcf_*.json",
    ):
        files = sorted(DCF_DIR.glob(pattern), reverse=True)
        if files:
            return files[0]
    return None


def _file_age_days(path: Path) -> int | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if not m:
        return None
    try:
        return (date.today() - datetime.strptime(m.group(1), "%Y-%m-%d").date()).days
    except ValueError:
        return None


def _compute_on_demand(ticker: str) -> dict | None:
    """Compute DCF on the fly when no pre-computed file exists."""
    # Add project root to sys.path so src.* imports work
    project_root = str(settings.PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from src.analysis.dcf_calculator import DCFCalculator

    calc = DCFCalculator(data_dir=settings.DATA_DIR)
    return calc.compute_and_save(ticker)


@router.get("/{ticker}/dcf")
def get_dcf(ticker: str):
    path = _find_latest_json(ticker)
    age = _file_age_days(path) if path else None

    if path and age is not None and age <= MAX_CACHE_AGE_DAYS:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # Missing or stale — recompute. A ticker the model can no longer value (a
    # bank, an ETF, a delisted name) must 404 rather than keep serving whatever
    # it happened to say months ago.
    result = _compute_on_demand(ticker)
    if result:
        return result

    raise HTTPException(
        status_code=404,
        detail=f"Insufficient data for DCF valuation of {ticker.upper()}. "
               f"Ensure statistics and forecast data have been scraped.",
    )
