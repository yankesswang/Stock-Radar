"""發現層 universe — 手寫桶之外的全市場個股，讓沒研究過的機會也掃得到。

Stage-1 預篩原本只掃 `config.STOCK_LIST` ∪ Mimi ∪ vault，全是「我自己抓過的」；
真正的漏網之魚（沒進過筆記、也沒在名單裡的）永遠不會出現在榜上。本模組把全美
上市個股拉進來當第二層，過三道品質門檻後併入掃描。

兩層的差別只在可信度標記，訊號計算完全一樣：
  - curated   手寫主題桶 / Mimi / vault —— 我研究過或正在看的
  - discovery 自動撈進來的 —— 榜上標「未研究」，看到要自己先做功課

## 三道品質門檻與它們各自的資料源

| 門檻 | 值 | 資料源 | 為什麼是這個源 |
|---|---|---|---|
| 市值 | ≥ 20 億美元 | Nasdaq screener（**一次呼叫回全市場**）| 微型股訊號不可靠 |
| 日均成交額 | ≥ 2000 萬美元 | Yahoo v8 chart 的 3 個月均量 × 價 | 沒量的進得去出不來 |
| 分析師覆蓋 | ≥ 3 位 | stockanalysis.com forecast 頁 | 沒人覆蓋的估值類 bucket 一定缺資料白掃 |

**這三個源都刻意避開 yfinance 的 `.info`**：它走 Yahoo 的 crumb 端點，掃到幾百檔就
整個 session 被封（`YFRateLimitError`），連續一秒一發也一樣封 —— 專案其他地方
（`portfolio._fetch_price_info`、`earnings/price_technicals`）早就為同一個原因繞開它。
成交額特意不用 Nasdaq 那欄「當日成交量」：那是單日值，冷清一天就會把好股票踢出去，
所以另外用 chart 端點取 3 個月均量。

漏斗是逐層收斂的（先市值再成交額最後分析師），愈貴的檢查放愈後面。分析師那關順便
把 ETF、封閉式基金、SPAC 濾掉 —— 它們沒有個股 forecast 頁。

抓一輪 ~3 分鐘，所以快取在 data/universe/discovery.json，預設 7 天過期。抓不到就退回
上次的快取，快取也沒有就回空 —— 發現層是加分項，永遠不該把主掃描弄掛。
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import requests

from backend.app.config import settings

# ── 品質門檻 ──────────────────────────────────────────────────────────────────
MIN_MARKET_CAP = 2_000_000_000      # 市值 20 億美元
MIN_DOLLAR_VOLUME = 20_000_000      # 3 個月日均成交額 2000 萬美元
MIN_ANALYSTS = 3                    # 分析師覆蓋家數

# 第一關先放寬到一半，才不會因為「昨天剛好冷清」就把股票擋在第二關的正式量測之外
_PRESCREEN_DOLLAR_VOLUME = MIN_DOLLAR_VOLUME // 2

CACHE_PATH = settings.DATA_DIR / "universe" / "discovery.json"
CACHE_MAX_AGE_DAYS = 7

NASDAQ_SCREENER = "https://api.nasdaq.com/api/screener/stocks"
STOCKANALYSIS_FORECAST = "https://stockanalysis.com/stocks/{ticker}/forecast/"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 「N 位分析師」在 stockanalysis 的兩種寫法都要認得，只認一種會漏掉一整批
_ANALYST_PATTERNS = (
    re.compile(r"Based on\s+(\d+)\s+analyst", re.I),
    re.compile(r"The\s+(\d+)\s+analysts?\s+with\s+12-month price forecasts", re.I),
    re.compile(r"according to\s+(\d+)\s+analyst", re.I),
)

# Nasdaq 的 sector 名 → 掃描器裡的主題桶名。發現層個股沒有手寫桶，用這張表落到中文桶
# 裡，強弱榜才排得進去（對不到的掛「其他」）。名字刻意跟手寫桶不同，一眼就分得出層別。
SECTOR_TO_THEME = {
    "Technology": "＊科技",
    "Information Technology": "＊科技",
    "Health Care": "＊醫療保健",
    "Healthcare": "＊醫療保健",
    "Finance": "＊金融",
    "Financials": "＊金融",
    "Financial Services": "＊金融",
    "Consumer Discretionary": "＊非必需消費",
    "Consumer Cyclical": "＊非必需消費",
    "Consumer Staples": "＊必需消費",
    "Consumer Defensive": "＊必需消費",
    "Industrials": "＊工業製造",
    "Energy": "＊能源",
    "Utilities": "＊電力公用",
    "Basic Materials": "＊原物料",
    "Materials": "＊原物料",
    "Real Estate": "＊不動產",
    "Telecommunications": "＊電信媒體",
    "Communication Services": "＊電信媒體",
}
FALLBACK_THEME = "＊其他"


def theme_for_sector(sector: str | None) -> str:
    return SECTOR_TO_THEME.get((sector or "").strip(), FALLBACK_THEME)


def _num(raw) -> float:
    """Nasdaq 回的是 '$150.86' / '42531786914.00' / '1,825,447' 這種字串。"""
    try:
        return float(str(raw).replace("$", "").replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


# ── 第一關：全市場 + 市值（Nasdaq screener，一次呼叫）──────────────────────────

def _fetch_market_rows() -> list[dict]:
    resp = requests.get(
        NASDAQ_SCREENER,
        headers={"User-Agent": _UA, "Accept": "application/json"},
        params={"tableonly": "true", "limit": "0", "offset": "0", "download": "true"},
        timeout=60,
    )
    resp.raise_for_status()
    rows = ((resp.json() or {}).get("data") or {}).get("rows") or []
    if not rows:
        raise RuntimeError("Nasdaq screener 回了空清單")
    return rows


def _gate_market_cap(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        ticker = str(row.get("symbol") or "").strip().upper()
        if not ticker or not re.fullmatch(r"[A-Z][A-Z0-9\.\-]{0,6}", ticker):
            continue
        cap = _num(row.get("marketCap"))
        if cap < MIN_MARKET_CAP:
            continue
        price = _num(row.get("lastsale"))
        if price * _num(row.get("volume")) < _PRESCREEN_DOLLAR_VOLUME:
            continue
        out[ticker] = {
            "name": str(row.get("name") or "").strip(),
            "sector": (str(row.get("sector") or "").strip() or None),
            "industry": (str(row.get("industry") or "").strip() or None),
            "country": (str(row.get("country") or "").strip() or None),
            "market_cap": int(cap),
        }
    return out


# ── 第二關：3 個月日均成交額（Yahoo v8 chart，不走 crumb）─────────────────────

def _avg_dollar_volume(ticker: str) -> int | None:
    from backend.app.routers.chart import _fetch_yahoo_chart_df

    try:
        df = _fetch_yahoo_chart_df(ticker, "3mo", "1d")
    except Exception:
        return None
    if df is None or df.empty or "Volume" not in df or "Close" not in df:
        return None
    vol = df["Volume"].dropna().astype(float)
    close = df["Close"].dropna().astype(float)
    if len(vol) < 20 or close.empty:
        return None
    return int(float(vol.tail(63).mean()) * float(close.iloc[-1]))


def _gate_dollar_volume(meta: dict[str, dict], workers: int = 10) -> dict[str, dict]:
    tickers = sorted(meta)
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ticker, dv in zip(tickers, ex.map(_avg_dollar_volume, tickers)):
            if dv is not None and dv >= MIN_DOLLAR_VOLUME:
                out[ticker] = {**meta[ticker], "dollar_volume": dv}
    return out


# ── 第三關：分析師覆蓋（stockanalysis.com）────────────────────────────────────

def _analyst_count(ticker: str) -> int | None:
    """None = 頁面抓不到或根本沒有個股 forecast 頁（ETF / 基金 / SPAC）。"""
    try:
        resp = requests.get(
            STOCKANALYSIS_FORECAST.format(ticker=ticker.lower().replace(".", "-")),
            headers={"User-Agent": _UA},
            timeout=25,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    for pattern in _ANALYST_PATTERNS:
        m = pattern.search(resp.text)
        if m:
            return int(m.group(1))
    return None


def _gate_analysts(meta: dict[str, dict], workers: int = 8) -> dict[str, dict]:
    tickers = sorted(meta)
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ticker, n in zip(tickers, ex.map(_analyst_count, tickers)):
            if n is not None and n >= MIN_ANALYSTS:
                out[ticker] = {**meta[ticker], "analysts": n}
    return out


# ── 快取 ──────────────────────────────────────────────────────────────────────

def _read_cache() -> dict | None:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_is_fresh(cache: dict, max_age_days: int) -> bool:
    try:
        built = date.fromisoformat(cache["built_on"])
    except (KeyError, TypeError, ValueError):
        return False
    return (date.today() - built) <= timedelta(days=max_age_days)


def rebuild(verbose: bool = True) -> dict:
    """跑完整條漏斗，寫進快取並回傳。任何一關掛掉就整批放棄（呼叫端會退回舊快取）。"""
    started = time.time()

    rows = _fetch_market_rows()
    stage1 = _gate_market_cap(rows)
    if verbose:
        print(f"[discovery] 全市場 {len(rows)} 檔 → 市值 ≥ {MIN_MARKET_CAP/1e9:.0f}B：{len(stage1)} 檔")

    stage2 = _gate_dollar_volume(stage1)
    if verbose:
        print(f"[discovery] → 3 個月日均成交額 ≥ {MIN_DOLLAR_VOLUME/1e6:.0f}M：{len(stage2)} 檔")

    stage3 = _gate_analysts(stage2)
    if verbose:
        print(f"[discovery] → 分析師覆蓋 ≥ {MIN_ANALYSTS}：{len(stage3)} 檔")

    stocks = {
        ticker: {**meta, "theme": theme_for_sector(meta.get("sector"))}
        for ticker, meta in sorted(stage3.items())
    }
    payload = {
        "built_on": date.today().isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "gates": {
            "min_market_cap": MIN_MARKET_CAP,
            "min_dollar_volume": MIN_DOLLAR_VOLUME,
            "min_analysts": MIN_ANALYSTS,
        },
        "funnel": {
            "listed": len(rows),
            "after_market_cap": len(stage1),
            "after_dollar_volume": len(stage2),
            "after_analysts": len(stage3),
        },
        "stocks": stocks,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print(f"[discovery] 完成 {len(stocks)} 檔（{time.time() - started:.0f}s）→ {CACHE_PATH}")
    return payload


def load(max_age_days: int = CACHE_MAX_AGE_DAYS, auto_refresh: bool = True) -> dict[str, dict]:
    """{TICKER: meta}。快取新鮮就直接用，過期才重抓，重抓失敗退回舊快取。"""
    cache = _read_cache()
    if cache and _cache_is_fresh(cache, max_age_days):
        return cache.get("stocks") or {}
    if auto_refresh:
        try:
            return rebuild(verbose=False).get("stocks") or {}
        except Exception as exc:
            print(f"[discovery] 重建失敗（{type(exc).__name__}: {exc}），沿用舊快取")
    return (cache or {}).get("stocks") or {}


def tickers(max_age_days: int = CACHE_MAX_AGE_DAYS, auto_refresh: bool = True) -> set[str]:
    return set(load(max_age_days, auto_refresh))
