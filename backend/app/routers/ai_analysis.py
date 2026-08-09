"""
單檔 AI 決策卡 — 把 stock-radar 已有的技術面 + 估值 + 新聞 + 大盤背景
餵給 `claude -p`，產出結構化的「決策儀表板」：

  綜合評分 · 多空評級 · 趨勢 · 操作建議 · 買進區 / 目標價 / 停損 ·
  風險警報 · 催化因素 · 完整分析正文

概念取自 ZhuLinsen/daily_stock_analysis 的 per-stock decision dashboard，
但完全復用 stock-radar 既有資料來源（不另接 A股/港股 data provider）。

Endpoint:
  GET /api/stocks/{ticker}/ai-analysis            — 產生 / 讀取當日決策卡（含快取）
  GET /api/stocks/{ticker}/ai-analysis?refresh=1  — 強制重新產生

claude 呼叫慢（~20-90s）且會花費，故每檔每日快取到
  {DATA_DIR}/ai_analysis/{TICKER}_{YYYY-MM-DD}.json
"""
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Stock, Valuation
from .news import _fetch_news
from .portfolio import _fetch_technicals

router = APIRouter()

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
_CACHE_DIR = Path(settings.DATA_DIR) / "ai_analysis"
_MARKET_CONTEXT = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "^VIX": "VIX 恐慌指數"}


# ── 資料蒐集 ────────────────────────────────────────────────────────────────────

def _live_price(ticker: str) -> dict | None:
    """即時報價（沿用 stocks.get_stock_price 的精簡版）。"""
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev = info.get("previousClose")
        if price is None:
            return None
        change = round(price - prev, 2) if prev else None
        change_pct = round(change / prev * 100, 2) if (change and prev) else None
        return {
            "price": round(float(price), 2),
            "change": change,
            "change_pct": change_pct,
            "market_state": info.get("marketState", "UNKNOWN"),
        }
    except Exception:
        return None


def _market_context() -> dict[str, dict]:
    """SPY / QQQ / VIX 當日漲跌，提供大盤背景。"""
    def one(sym: str) -> tuple[str, dict | None]:
        try:
            closes = yf.Ticker(sym).history(period="5d", auto_adjust=True)["Close"].dropna()
            if len(closes) < 2:
                return sym, None
            price, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            return sym, {"price": round(price, 2), "change_pct": round((price - prev) / prev * 100, 2)}
        except Exception:
            return sym, None

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for sym, data in ex.map(one, _MARKET_CONTEXT):
            if data:
                out[sym] = data
    return out


def _valuation_context(ticker: str, db: Session) -> tuple[str, dict]:
    """從 DB 撈最新估值 / 基本面；回傳 (company_name, valuation_dict)。"""
    stock = db.query(Stock).filter(func.upper(Stock.ticker) == ticker.upper()).first()
    if not stock:
        return ticker, {}
    v = (
        db.query(Valuation)
        .filter_by(stock_id=stock.id)
        .order_by(Valuation.snapshot_date.desc())
        .first()
    )
    company = stock.company_name or ticker
    if not v:
        return company, {"industry": stock.industry.name if stock.industry else None}
    return company, {
        "industry": stock.industry.name if stock.industry else None,
        "current_price": v.current_price,
        "pe_median_5y": v.pe_median_5y,
        "pe_median_price": v.pe_median_price,
        "current_year_eps": v.current_year_eps,
        "next_year_eps": v.next_year_eps,
        "next_year_fair_price": v.next_year_fair_price,
        "valuation_status": v.valuation_status,
        "valuation_diff_pct": v.valuation_diff_pct,
        "eps_growth_5y": v.eps_growth_5y,
        "revenue_growth_5y": v.revenue_growth_5y,
    }


# ── Prompt ─────────────────────────────────────────────────────────────────────

_OUTPUT_SCHEMA = """你必須只輸出一個 JSON 物件（不要任何前後說明、不要 markdown code fence），欄位如下：
{
  "score": <0-100 整數，綜合評分；越高越偏多>,
  "rating": "<強烈看多|偏多|中性|偏空|強烈看空 擇一>",
  "action": "<分批買入|買入|持有|減碼|賣出|觀望 擇一>",
  "trend": "<一句話：中期與短線趨勢判讀>",
  "summary": "<一句話核心結論，30 字內>",
  "buy_zone": "<建議進場價格區間，如 $215-225；無則填 —>",
  "target": "<目標價，如 $260；無則填 —>",
  "stop_loss": "<停損參考價，如 $205；無則填 —>",
  "risks": ["<風險警報 1>", "<風險 2>", "..."],
  "catalysts": ["<催化因素 1>", "<催化 2>", "..."],
  "analysis": "<完整分析正文，markdown 格式，涵蓋：技術面、估值、新聞/催化、操作邏輯>"
}
risks 與 catalysts 各 2-4 條。analysis 用繁體中文，可用標題與條列。"""


def _build_prompt(
    ticker: str,
    company: str,
    price: dict | None,
    valuation: dict,
    technicals: dict | None,
    news: list[dict],
    market: dict[str, dict],
) -> str:
    now = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M 台北時間")
    L: list[str] = []
    L.append(f"你是 Arthur 的專屬股票分析師。請針對 {ticker}（{company}）做一份收盤決策分析。")
    L.append(f"資料時間：{now}。以下是 stock-radar 蒐集到的即時資料，請完全基於這些事實分析，不要編造數字。")
    L.append("")

    # 大盤背景
    if market:
        L.append("## 大盤背景")
        for sym, label in _MARKET_CONTEXT.items():
            d = market.get(sym)
            if d:
                L.append(f"- {label}: {d['price']}（{d['change_pct']:+.2f}%）")
        L.append("")

    # 報價
    L.append("## 即時報價")
    if price:
        chg = f"{price['change']:+.2f}（{price['change_pct']:+.2f}%）" if price.get("change_pct") is not None else "—"
        L.append(f"- 現價: ${price['price']}｜今日: {chg}｜盤態: {price.get('market_state')}")
    else:
        L.append("- 無法取得即時報價")
    L.append("")

    # 技術面
    L.append("## 技術面")
    if technicals:
        t = technicals
        L.append(f"- RSI-14: {t.get('rsi_14')}")
        L.append(f"- MA5 / MA20 / MA50: {t.get('ma5')} / {t.get('ma20')} / {t.get('ma50')}")
        if t.get("price_vs_ma20_pct") is not None:
            L.append(f"- 現價相對 MA20: {t['price_vs_ma20_pct']:+.2f}%")
        if t.get("ma_cross"):
            L.append(f"- 均線交叉: {'黃金交叉' if t['ma_cross'] == 'golden' else '死亡交叉'}")
        if t.get("volume_ratio"):
            L.append(f"- 成交量: {t['volume_ratio']}× 20日均量")
        for lvl in t.get("near_support", []):
            L.append(f"- 近支撐: ${lvl['price']}（距 {lvl['dist_pct']}%，強度 {lvl['strength']}）")
        for lvl in t.get("near_resistance", []):
            L.append(f"- 近壓力: ${lvl['price']}（距 {lvl['dist_pct']}%，強度 {lvl['strength']}）")
    else:
        L.append("- 技術指標無法取得")
    L.append("")

    # 估值 / 基本面
    L.append("## 估值與基本面")
    if valuation:
        v = valuation
        if v.get("industry"):
            L.append(f"- 產業: {v['industry']}")
        if v.get("pe_median_price") is not None:
            L.append(f"- PE 中位數合理價: ${v['pe_median_price']}（5年中位 PE {v.get('pe_median_5y')}）")
        if v.get("next_year_fair_price") is not None:
            L.append(f"- 明年 EPS 合理價: ${v['next_year_fair_price']}")
        if v.get("valuation_status"):
            diff = v.get("valuation_diff_pct")
            L.append(f"- 估值狀態: {v['valuation_status']}" + (f"（{diff:+.1f}%）" if diff is not None else ""))
        if v.get("current_year_eps") is not None:
            L.append(f"- 今年 EPS: ${v['current_year_eps']}｜明年 EPS: ${v.get('next_year_eps')}")
        if v.get("eps_growth_5y") is not None:
            L.append(f"- 5年 EPS 成長: {v['eps_growth_5y']}%｜營收成長: {v.get('revenue_growth_5y')}%")
    else:
        L.append("- DB 無估值資料（此檔可能尚未爬取）")
    L.append("")

    # 新聞
    L.append("## 近期新聞標題")
    if news:
        for n in news[:10]:
            src = f"（{n['source']}）" if n.get("source") else ""
            L.append(f"- {n['title']}{src}")
    else:
        L.append("- 無新聞")
    L.append("")

    L.append("---")
    L.append(_OUTPUT_SCHEMA)
    return "\n".join(L)


# ── claude 執行 + JSON 解析 ──────────────────────────────────────────────────────

def _run_claude(prompt: str) -> str:
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(settings.PROJECT_ROOT),
        )
        return (result.stdout or "").strip()
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail="claude 分析超時（>5 分鐘）") from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail="找不到 claude CLI，請確認已安裝並在 PATH 中") from e


def _parse_json(raw: str) -> dict:
    """從 claude 回應抽出 JSON 物件（容忍 code fence / 前後雜訊）。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=502,
            detail=f"claude 回應無法解析為 JSON：{e}. 原始開頭：{raw[:200]}",
        ) from e


# ── 可複用核心：產生單檔決策卡 ────────────────────────────────────────────────────
# endpoint 與 scheduler 的 AI 持倉日報都呼叫這個函數，避免邏輯重複。

def generate_card(ticker: str, db: Session, refresh: bool = False) -> dict:
    """蒐集資料 → claude -p → 解析 → 存當日快取，回傳決策卡 payload。"""
    ticker = ticker.upper().strip()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", ticker):
        raise HTTPException(status_code=400, detail="invalid ticker")

    today = date.today().isoformat()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{ticker}_{today}.json"

    if cache_file.exists() and not refresh:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        payload["cached"] = True
        return payload

    # 1) 蒐集資料
    company, valuation = _valuation_context(ticker, db)
    price = _live_price(ticker)
    technicals = _fetch_technicals(ticker)
    try:
        news = _fetch_news(f"{ticker.split('.')[0]} stock", limit=10)
    except HTTPException:
        news = []
    market = _market_context()

    # 2) 產生決策卡
    prompt = _build_prompt(ticker, company, price, valuation, technicals, news, market)
    raw = _run_claude(prompt)
    if not raw:
        raise HTTPException(status_code=502, detail="claude 無回應")
    card = _parse_json(raw)

    payload = {
        "ticker": ticker,
        "company_name": company,
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(),
        "cached": False,
        "price": price,
        "score": card.get("score"),
        "rating": card.get("rating"),
        "action": card.get("action"),
        "trend": card.get("trend"),
        "summary": card.get("summary"),
        "buy_zone": card.get("buy_zone"),
        "target": card.get("target"),
        "stop_loss": card.get("stop_loss"),
        "risks": card.get("risks") or [],
        "catalysts": card.get("catalysts") or [],
        "analysis": card.get("analysis"),
        "news": news,
    }

    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


# ── Endpoint ─────────────────────────────────────────────────────────────────────

@router.get("/{ticker}/ai-analysis")
def get_ai_analysis(
    ticker: str,
    refresh: bool = Query(False, description="強制重新產生，忽略當日快取"),
    db: Session = Depends(get_db),
) -> dict:
    return generate_card(ticker, db, refresh=refresh)
