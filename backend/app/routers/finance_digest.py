"""
Finance Digest — 投資日報.
讀取 financial_digest 產生的每日報告 JSON（data/finance-digest/），供前端閱讀。

來源：financial_digest/scripts/digest-substack.ts 每天爬 Substack → claude 整理投資筆記
      → 寫入 data/finance-digest/{date}.json + index.json

Endpoints:
  GET /api/finance-digest            — 可用日期清單（新到舊）
  GET /api/finance-digest/latest     — 最新一份日報
  GET /api/finance-digest/by-ticker  — 依 ticker 查近 N 天提及該股的文章（War Room 溯源用）
  GET /api/finance-digest/recent     — 近 N 天所有文章的精簡列表（大盤敘事脈絡）
  GET /api/finance-digest/{date}     — 指定日期的日報（YYYY-MM-DD）
"""
import json
import re
import time
from datetime import date as date_cls, timedelta

from fastapi import APIRouter, HTTPException, Query

from ..config import settings

router = APIRouter()

DIGEST_DIR = settings.DATA_DIR / "finance-digest"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TLDR_MAX = 600           # by-ticker / recent 只回截斷 TLDR，全文請走 url 或 /{date}
_CACHE_TTL = 300.0       # 5 分鐘：digest 一天只更新一次，快取幾乎零成本
_cache: dict[str, tuple[float, list[dict]]] = {}


def _list_dates() -> list[str]:
    if not DIGEST_DIR.exists():
        return []
    dates = [p.stem for p in DIGEST_DIR.glob("*.json") if _DATE_RE.match(p.stem)]
    dates.sort(reverse=True)
    return dates


def _truncate(text: str, limit: int = TLDR_MAX) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def load_recent_items(days: int = 21) -> list[dict]:
    """近 N 天所有 digest 文章的精簡 dict（不含 content 全文），新到舊。

    防禦式：缺檔 / 壞 JSON / 結構異常都跳過該日，絕不 raise。
    scripts/war_room.py 與 market_scan.py 直接 import 此函式（不打自己的 HTTP）。
    """
    days = max(1, min(int(days or 21), 365))
    key = f"recent:{days}"
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    cutoff = date_cls.today() - timedelta(days=days)
    items: list[dict] = []
    for d in _list_dates():
        try:
            if date_cls.fromisoformat(d) < cutoff:
                continue
            with open(DIGEST_DIR / f"{d}.json", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue  # 壞 JSON / 讀不到 → 跳過該日，不影響其他天
        if not isinstance(payload, dict):
            continue
        for it in payload.get("items") or []:
            if not isinstance(it, dict):
                continue
            tickers = [
                str(t).strip().upper()
                for t in (it.get("tickers") or [])
                if str(t).strip()
            ]
            items.append({
                "date": d,
                "title": it.get("title") or "",
                "sector": it.get("sector") or "",
                "url": it.get("url") or "",
                "tickers": tickers,
                "tldr": _truncate(it.get("tldr") or ""),
            })
    _cache[key] = (now, items)
    return items


def _load(date: str) -> dict:
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="invalid date format")
    path = DIGEST_DIR / f"{date}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no digest for {date}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@router.get("")
def list_digests() -> dict:
    return {"dates": _list_dates()}


@router.get("/latest")
def latest_digest() -> dict:
    dates = _list_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="no digests yet")
    return _load(dates[0])


@router.get("/by-ticker")
def digest_by_ticker(
    tickers: str = Query("", description="逗號分隔 ticker 清單，如 NVDA,META"),
    days: int = Query(21, ge=1, le=365, description="回溯天數"),
) -> dict:
    """近 N 天內提及各 ticker 的 digest 文章（只回截斷 TLDR，不回 content 全文）。

    給 War Room 前端做「📰 財經 DIGEST」溯源 chip 的批次判定，也給 agent curl。
    """
    wanted = [t.strip().upper() for t in (tickers or "").split(",") if t.strip()]
    result: dict[str, list[dict]] = {t: [] for t in wanted}
    if wanted:
        wanted_set = set(wanted)
        for item in load_recent_items(days):
            hit = wanted_set.intersection(item["tickers"])
            if not hit:
                continue
            entry = {
                "date": item["date"],
                "title": item["title"],
                "sector": item["sector"],
                "url": item["url"],
                "tldr": item["tldr"],
            }
            for t in hit:
                result[t].append(entry)
    return {
        "days": days,
        "items": result,
        "tickers_with_news": [t for t in wanted if result[t]],
    }


@router.get("/recent")
def recent_digests(days: int = Query(7, ge=1, le=365)) -> dict:
    """近 N 天所有 digest 文章的精簡列表 — 給 agent 當大盤敘事脈絡。"""
    items = load_recent_items(days)
    return {"days": days, "count": len(items), "items": items}


@router.get("/{date}")
def get_digest(date: str) -> dict:
    return _load(date)
