"""
個股新聞 — 與 stock-digest 同源。

stock-digest 的 horizon 流程透過 Google News RSS
(`news.google.com/rss/search?q={TICKER}+stock`) 抓取每檔個股的新聞
（horizon/data/config.json 的 `stock-news` 來源）。這裡直接對任意 ticker
即時抓同一個 feed，讓 stock-radar 的個股頁也能看新聞，不必預先設定。

Endpoint:
  GET /api/stocks/{ticker}/news?limit=15  — 該個股最新新聞（新到舊）
"""
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockRadar/1.0)"}

# 語系 → Google News 的 (hl, gl, ceid)。台股用美國英文 feed 幾乎抓不到東西，
# scripts/market_news.py 對 .TW 標的會指定 zh-TW。
_LANGS = {
    "en": ("en-US", "US", "US:en"),
    "zh-TW": ("zh-TW", "TW", "TW:zh-Hant"),
}

# 簡單記憶體快取，避免短時間內重複抓同一檔（Google News 會擋）
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 600  # 秒


def _parse_pubdate(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return raw


def _fetch_news(query: str, limit: int, lang: str = "en") -> list[dict]:
    hl, gl, ceid = _LANGS.get(lang, _LANGS["en"])
    url = _GOOGLE_NEWS_RSS.format(query=quote(query), hl=hl, gl=gl, ceid=ceid)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"fetch news failed: {e}") from e
    except ET.ParseError as e:
        raise HTTPException(status_code=502, detail=f"parse news feed failed: {e}") from e

    items: list[dict] = []
    for it in root.findall(".//item"):
        title = it.findtext("title") or ""
        source_el = it.find("{*}source")
        source = source_el.text if source_el is not None else None
        # Google News 標題格式為 "標題 - 來源"，去掉尾端來源讓畫面乾淨
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)]
        items.append(
            {
                "title": title.strip(),
                "url": it.findtext("link"),
                "source": source,
                "published_at": _parse_pubdate(it.findtext("pubDate")),
            }
        )
        if len(items) >= limit:
            break
    return items


@router.get("/{ticker}/news")
def get_stock_news(
    ticker: str,
    limit: int = Query(15, ge=1, le=50),
    company: str | None = Query(None, description="公司名稱，可提升搜尋精準度"),
) -> dict:
    ticker = ticker.upper().strip()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", ticker):
        raise HTTPException(status_code=400, detail="invalid ticker")

    cache_key = f"{ticker}:{limit}:{company or ''}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return {"ticker": ticker, "cached": True, "items": cached[1]}

    # 用 ".TW" 之類後綴的純代號 + "stock" 搜尋，與 horizon 的 stock-news 來源一致
    base = ticker.split(".")[0]
    query = f'{company} {base} stock' if company else f"{base} stock"
    items = _fetch_news(query, limit)
    _CACHE[cache_key] = (now, items)
    return {"ticker": ticker, "cached": False, "items": items}
