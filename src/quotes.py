"""共用的報價來源。

估值頁的「現價」原本直接讀 `valuations.current_price`，而那個欄位來自每週一次的
stockanalysis 比率表爬蟲的 `Last Close Price` 欄 —— 不是報價，是「上一個收盤」，
而且爬蟲排在台北凌晨（美股盤中），所以就算剛爬完也已經落後一個交易日。

改用報價之後還踩到第二個坑（2026-09-23）：Yahoo 的**日線**在收盤後好幾個小時都還
沒結算 —— 當天那根 K 棒會帶著成交量、但 `close` 是 null。所以任何「取日線最後一根
非空收盤」的寫法（`history(...)["Close"].dropna().iloc[-1]`、`yf.download` 同理）在
台北的早上拿到的都還是前一天的價。同一時間 `meta.regularMarketPrice` 已經是正確的
當日收盤了。

所以這裡以 Yahoo chart 端點的 `meta.regularMarketPrice` 為準，日線只拿來取前一日
收盤（算漲跌用）。chart 端點另外兩個好處：不走 cookie/crumb，所以不吃 yfinance 那套
`YFRateLimitError` 限流；實測 40 檔 12 條執行緒 0.6 秒，600 檔約 10 秒，跟批次下載同級。
yfinance 只留作後備（chart 端點整批掛掉時）。

backend 的 /api/stocks/prices、個股標頭的 /price、同業比較、DCF、投組、
每日 refresh_prices 排程都走這支。
"""
from __future__ import annotations

import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote as _urlquote
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

# ticker → {"data": {...}, "ts": epoch}
_CACHE: dict[str, dict] = {}
CACHE_TTL = 120   # 秒
CHUNK = 100       # yfinance 後備一次 download 的上限，超過 Yahoo 會回洞
WORKERS = 12      # chart 端點的併發；再高邊際效益很小，不值得多壓對方

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
_UA = {"User-Agent": "Mozilla/5.0"}


def _usd_twd_rate() -> float | None:
    """1 USD 折合多少 TWD。也走 chart 端點，不要為了一個匯率去碰限流的那條路。"""
    q = _chart_one("TWD=X")
    return q["price"] if q else None


def _chart_one(sym: str, _tries: int = 3) -> dict | None:
    """單檔報價。抓不到回 None —— 絕不要把「抓不到」偽裝成 0 或舊值。

    併發打過去時 Yahoo 偶爾會短暫擋一下（同一支 ticker 前後差幾毫秒，一次失敗一次成功）。
    這條路是所有價格的主要來源，一次閃斷就會讓某檔在投組裡整格沒價，所以退避重試幾次。
    """
    res = None
    for attempt in range(_tries):
        try:
            r = requests.get(
                _CHART_URL.format(_urlquote(sym, safe="")),
                params={"range": "5d", "interval": "1d"},
                headers=_UA,
                timeout=12,
            )
            r.raise_for_status()
            res = ((r.json().get("chart") or {}).get("result") or [None])[0]
            if res:
                break
        except Exception:
            res = None
        if attempt < _tries - 1:
            _time.sleep(0.4 * (attempt + 1))
    if not res:
        return None

    try:
        meta = res.get("meta") or {}
        tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")

        # 日線：只留 close 有值的，配上該交易日在交易所當地的日期
        stamps = res.get("timestamp") or []
        closes = (((res.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
        bars = [
            (datetime.fromtimestamp(t, timezone.utc).astimezone(tz).date(), float(c))
            for t, c in zip(stamps, closes) if c is not None
        ]

        price = meta.get("regularMarketPrice")
        rmt = meta.get("regularMarketTime")
        if price is not None and rmt:
            # 這一盤的日期。當天那根 K 棒可能還沒結算（close=null），所以前一日收盤
            # 要找「日期比這一盤早」的最後一根，不能單純取倒數第二根。
            session = datetime.fromtimestamp(rmt, timezone.utc).astimezone(tz).date()
            prev = next((c for d, c in reversed(bars) if d < session), None)
            price, last_date = float(price), session.isoformat()
        elif bars:
            price, last_date = bars[-1][1], bars[-1][0].isoformat()
            prev = bars[-2][1] if len(bars) >= 2 else None
        else:
            return None

        return {
            "ticker": sym,
            "price": price,
            "previous_close": prev,
            "currency": meta.get("currency") or "USD",
            "last_date": last_date,
            "market_state": None,
            "source": "chart",
        }
    except Exception:
        return None


def _finalise(sym: str, price: float, prev: float | None, currency: str,
              last_date: str | None, usd_twd: float | None, source: str) -> dict:
    """統一成對外的欄位形狀，順手做 .TW 的 TWD→USD 換算。"""
    if sym.endswith(".TW") and usd_twd:
        price /= usd_twd
        if prev is not None:
            prev /= usd_twd
        currency = "USD"
    change = round(price - prev, 4) if prev else None
    return {
        "ticker": sym,
        "price": round(price, 4),
        "currency": currency,
        "previous_close": round(prev, 4) if prev else None,
        "change": change,
        "change_percent": round(change / prev * 100, 2) if (change and prev) else None,
        "last_date": last_date,
        "source": source,
    }


def _chart_quotes(symbols: list[str], now: float) -> dict[str, dict]:
    """主要來源：chart 端點，併發逐檔。"""
    out: dict[str, dict] = {}
    usd_twd = _usd_twd_rate() if any(s.endswith(".TW") for s in symbols) else None
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sym, q in zip(symbols, ex.map(_chart_one, symbols)):
            if not q or not q.get("price"):
                continue
            data = _finalise(sym, q["price"], q["previous_close"], q["currency"],
                             q["last_date"], usd_twd, "chart")
            out[sym] = data
            _CACHE[sym] = {"data": data, "ts": now}
    return out


def _download(symbols: list[str], now: float) -> dict[str, dict]:
    """後備：yfinance 批次日線。

    只在 chart 端點整批失敗時才走。注意它拿得到的最新收盤可能比 chart 端點舊一天
    （當日 K 棒未結算就是 NaN，dropna 之後只剩前一天），所以是後備不是主力。
    """
    out: dict[str, dict] = {}
    try:
        raw = yf.download(symbols, period="5d", interval="1d", auto_adjust=True,
                          group_by="ticker", threads=True, progress=False)
    except Exception:
        return out
    if raw is None or raw.empty:
        return out

    usd_twd = _usd_twd_rate() if any(s.endswith(".TW") for s in symbols) else None
    for sym in symbols:
        try:
            # 不能用 len(symbols) 判斷有沒有 ticker 欄位層：yfinance 1.x 即使只下載
            # 一檔，group_by="ticker" 照樣回 MultiIndex。
            frame = raw[sym] if sym in raw.columns.get_level_values(0) else raw
            closes = frame["Close"].dropna()
            if closes.empty:
                continue
            prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
            data = _finalise(sym, float(closes.iloc[-1]), prev, "USD",
                             str(closes.index[-1])[:10], usd_twd, "yfinance")
            out[sym] = data
            _CACHE[sym] = {"data": data, "ts": now}
        except Exception:
            continue
    return out


def get_quotes(tickers: list[str], use_cache: bool = True) -> dict[str, dict]:
    """批次報價，回 {TICKER: {price, previous_close, change, ...}}。

    抓不到的 ticker 直接不在回傳的 key 裡 —— 呼叫端要自己決定退回什麼，
    絕不要把「抓不到」偽裝成 0 或舊值。
    """
    wanted = [t.strip().upper() for t in tickers if t and t.strip()]
    if not wanted:
        return {}

    now = _time.time()
    out: dict[str, dict] = {}
    todo: list[str] = []
    for t in dict.fromkeys(wanted):          # 去重但保序
        hit = _CACHE.get(t) if use_cache else None
        if hit and now - hit["ts"] < CACHE_TTL:
            out[t] = hit["data"]
        else:
            todo.append(t)

    if todo:
        out.update(_chart_quotes(todo, now))

    missing = [t for t in todo if t not in out]
    if missing:
        for i in range(0, len(missing), CHUNK):
            out.update(_download(missing[i:i + CHUNK], now))
    return out


def get_price(ticker: str) -> float | None:
    """單檔最新價，抓不到回 None。"""
    q = get_quotes([ticker]).get(ticker.upper())
    return q["price"] if q else None
