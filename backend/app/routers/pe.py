"""
Serve PE history data scraped from fullratio.com.
Files live at  data/pe/fullratio/{TICKER}_fullratio_pe_{date}.json
"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter()

PE_DIR: Path = settings.DATA_DIR / "pe" / "fullratio"


def _find_latest(ticker: str) -> Path | None:
    pattern = f"{ticker.upper()}_fullratio_pe_*.json"
    files = sorted(PE_DIR.glob(pattern), reverse=True)
    return files[0] if files else None


@router.get("/{ticker}/pe")
def get_pe(ticker: str):
    path = _find_latest(ticker)
    if not path:
        raise HTTPException(
            status_code=404,
            detail=f"No PE data for {ticker.upper()}. Run: python3 scripts/scrape_fullratio_pe.py --ticker {ticker.upper()}",
        )
    with open(path) as f:
        return json.load(f)
