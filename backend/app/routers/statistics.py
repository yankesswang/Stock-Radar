"""
Serve the scraped statistics JSON data for a given ticker.

JSON files live at  data/statistics/{TICKER}_statistics_{date}.json
Always returns the most recent file available.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter()

STATS_DIR: Path = settings.DATA_DIR / "statistics"


def _find_latest_json(ticker: str) -> Path | None:
    """Return the newest statistics JSON for *ticker*."""
    for pattern in (
        f"{ticker.upper()}_statistics_*.json",
        f"{ticker.lower()}_statistics_*.json",
    ):
        files = sorted(STATS_DIR.glob(pattern), reverse=True)
        if files:
            return files[0]
    return None


@router.get("/{ticker}/statistics")
def get_statistics(ticker: str):
    path = _find_latest_json(ticker)
    if not path:
        raise HTTPException(
            status_code=404,
            detail=f"No statistics data found for {ticker.upper()}. Run the statistics scraper first.",
        )

    import json

    with open(path, encoding="utf-8") as f:
        return json.load(f)
