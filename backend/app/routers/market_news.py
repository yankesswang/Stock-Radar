"""
Market News — 每日市場新聞策展（portal 每日報告「市場新聞」分頁的來源）。

讀 scripts/market_news.py 產出的 data/market-news/{date}.json：
大盤/宏觀要點 + 我的持倉與觀察名單當日必讀新聞，每則都附回原始出處。
與 finance_digest（外部投資部落格筆記）無關 —— 那份仍留在 /api/finance-digest，
供 war_room / market_scan 當質化脈絡。

Endpoints:
  GET  /api/market-news            — 可用日期清單（新到舊）
  GET  /api/market-news/latest     — 最新一份
  GET  /api/market-news/status     — 手動產生的執行狀態
  POST /api/market-news/run        — 立即產生今日（背景跑腳本，前端輪詢 status）
  GET  /api/market-news/{date}     — 指定日期（YYYY-MM-DD）
"""
import asyncio
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..config import settings

router = APIRouter()

NEWS_DIR = Path(settings.DATA_DIR) / "market-news"
SCRIPT_PATH = Path(settings.PROJECT_ROOT) / "scripts" / "market_news.py"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RUN_TIMEOUT = 1500          # 25 分鐘：抓 ~40 檔新聞 + 一輪 claude 策展綽綽有餘

_state: dict[str, Any] = {
    "status": "idle",        # idle | running
    "last_run": None,        # ISO datetime
    "last_date": None,       # 產出的報告日期
    "error": None,
}


def _list_dates() -> list[str]:
    if not NEWS_DIR.exists():
        return []
    dates = [p.stem for p in NEWS_DIR.glob("*.json") if _DATE_RE.match(p.stem)]
    dates.sort(reverse=True)
    return dates


def _load(date: str) -> dict:
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="invalid date format")
    path = NEWS_DIR / f"{date}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no market news for {date}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _run_script_sync(model: str) -> None:
    """同步跑策展腳本（在 executor 裡執行，別擋住事件迴圈）。"""
    result = subprocess.run(
        ["uv", "run", "python", str(SCRIPT_PATH), "--model", model],
        cwd=str(settings.PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=_RUN_TIMEOUT,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "無輸出").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else f"exit {result.returncode}")


async def _do_run(model: str) -> None:
    _state.update({"status": "running", "error": None})
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _run_script_sync, model)
        _state["error"] = None
    except Exception as exc:
        _state["error"] = str(exc)[:500]
        print(f"[market_news] 手動產生失敗：{exc}")
    finally:
        dates = _list_dates()
        _state.update({
            "status": "idle",
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "last_date": dates[0] if dates else None,
        })


@router.get("")
def list_news() -> dict:
    return {"dates": _list_dates()}


@router.get("/latest")
def latest_news() -> dict:
    dates = _list_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="no market news yet")
    return _load(dates[0])


@router.get("/status")
def run_status() -> dict:
    return dict(_state)


@router.post("/run")
async def run_now(model: str = Query("sonnet", description="claude 模型")) -> dict:
    """立即產生今日市場新聞（背景執行；前端輪詢 /status 等它變回 idle）。"""
    if not SCRIPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"找不到腳本：{SCRIPT_PATH}")
    if _state["status"] == "running":
        raise HTTPException(status_code=409, detail="市場新聞正在產生中")
    asyncio.create_task(_do_run(model))
    return {"status": "running"}


@router.get("/{date}")
def get_news(date: str) -> dict:
    return _load(date)
