"""
Serve the scraped forecast detail JSON data for a given ticker.

JSON files live at  data/forecast/detail/{TICKER}_forecast_{date}.json
Always returns the most recent file available.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter()

FORECAST_DIR: Path = settings.DATA_DIR / "forecast" / "detail"


def _find_latest_json(ticker: str) -> Path | None:
    """Return the newest forecast JSON for *ticker*."""
    for pattern in (
        f"{ticker.upper()}_forecast_*.json",
        f"{ticker.lower()}_forecast_*.json",
    ):
        files = sorted(FORECAST_DIR.glob(pattern), reverse=True)
        if files:
            return files[0]
    return None


@router.get("/{ticker}/forecast")
def get_forecast_detail(ticker: str):
    path = _find_latest_json(ticker)
    if not path:
        raise HTTPException(
            status_code=404,
            detail=f"No forecast data found for {ticker.upper()}. Run the forecast scraper first.",
        )

    import json

    with open(path, encoding="utf-8") as f:
        return json.load(f)
