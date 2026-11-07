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

# 快取裡的現價跟實際報價差超過這個比例就重算。DCF 的上漲空間、三情境估值軸、
# 反推估值全都是拿 current_price 比出來的，價一舊整組結論跟著偏 —— 而光看
# 檔案日期看不出來，模型七天內都算「新鮮」。重算一檔不到一秒，比讓頁面拿
# 一週前的價去講「現價高於最樂觀情境」划算得多。
# 只改 current_price 再把幾個欄位補一補是不行的：那些數字彼此相關，
# 補一半會自相矛盾；整份重算才會一致。
MAX_PRICE_DRIFT_PCT = 1.0


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


def _price_drifted(cached: dict) -> bool:
    """快取裡的現價是否已經跟實際報價脫節。

    兩個條件，任一成立就重算：
      1. 快取的價屬於比現在更早的一盤 —— 跨盤一定要重算，不然頁面上會拿昨天的收盤
         在講「現價高於最樂觀情境」，而且光看百分比看不出來（隔夜常常不到 1%）。
      2. 同一盤之內，價格漂移超過門檻（盤中才會發生）。

    抓不到報價就當沒漂 —— 不要為了 Yahoo 掛掉而重算。
    """
    old = cached.get("current_price")
    if not old:
        return True
    project_root = str(settings.PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from src.quotes import get_quotes

    quote = get_quotes([cached.get("ticker") or ""]).get((cached.get("ticker") or "").upper())
    live = (quote or {}).get("price")
    if not live:
        return False

    cached_date, live_date = cached.get("price_date"), (quote or {}).get("last_date")
    if cached_date and live_date and cached_date != live_date:
        return True
    if not cached_date:
        return True     # 舊格式的快取沒記交易日，無法判斷是哪一盤 —— 重算一次補上

    return abs(live - old) / old * 100 > MAX_PRICE_DRIFT_PCT


@router.get("/{ticker}/dcf")
def get_dcf(ticker: str):
    path = _find_latest_json(ticker)
    age = _file_age_days(path) if path else None

    if path and age is not None and age <= MAX_CACHE_AGE_DAYS:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        cached.setdefault("ticker", ticker.upper())
        if not _price_drifted(cached):
            return cached

    # Missing、過期、或價格已經脫節 —— 重算。一檔模型已經無法評價的（銀行、
    # ETF、下市）要 404，不能繼續端出幾個月前的說法。
    result = _compute_on_demand(ticker)
    if result:
        return result

    # 重算不出來但手上還有沒過期的快取：端出來總比 404 好，讓前端自己看日期。
    if path and age is not None and age <= MAX_CACHE_AGE_DAYS:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    raise HTTPException(
        status_code=404,
        detail=f"Insufficient data for DCF valuation of {ticker.upper()}. "
               f"Ensure statistics and forecast data have been scraped.",
    )
