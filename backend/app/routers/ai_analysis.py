"""
單檔 AI 決策卡 — 把 stock-radar 已有的技術面 + 估值 + 新聞 + 大盤背景
餵給 `claude -p`，產出結構化的「決策儀表板」：

  綜合評分 · 多空評級 · 趨勢 · 操作建議 · 買進區 / 目標價 / 停損 ·
  風險警報 · 催化因素 · 條列式分析正文（每條附資料來源）

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
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings

# 全系統共用的 claude 額度守門員（Arthur_App/scripts/lib/claude_guard.py）。
# 每次呼叫順手記下 resetsAt，撞到上限就 raise QuotaExceeded，由呼叫端登記延後補跑。
_LIB_DIR = Path(settings.PROJECT_ROOT).parent / "scripts" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))
import claude_guard as _guard  # noqa: E402
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
  "analysis_bullets": [
    {
      "tag": "<技術面|估值|新聞|催化|風險|操作 擇一>",
      "point": "<一句話論點，60 字內，必須帶具體數字（價位／RSI／PE／百分比）>",
      "sources": ["<來源代號，見下方清單>", "..."]
    }
  ]
}

analysis_bullets 就是分析正文，規則（務必遵守）：
- 全部條列，6-10 條；一條只講一件事，不要寫成段落、不要巢狀子條列、不要在 point 內換行。
- 至少各有一條涵蓋：技術面、估值、新聞/催化、操作邏輯。
- 每條 sources 填 1-3 個「來源代號」，只能從下列挑，不可自創、不可自己寫網址或媒體名：
    報價    → 上方「即時報價」區塊
    技術面  → 上方「技術面」區塊
    估值    → 上方「估值與基本面」區塊
    大盤    → 上方「大盤背景」區塊
    新聞N   → 上方「近期新聞標題」的第 N 則，例：新聞3
- 論點若來自新聞，sources 必須指到那一則的編號；操作建議則指向它依據的資料代號。
- 上方資料沒有的東西就不要寫，寧可少一條，也不要生出沒有來源的論點。
risks 與 catalysts 各 2-4 條。全部用繁體中文。"""


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
        # 帶編號，讓模型能用「新聞N」引用來源；連結由程式回填，模型不需要（也不准）自己寫
        for i, n in enumerate(news[:10], 1):
            meta = " · ".join(x for x in [n.get("source"), (n.get("published_at") or "")[:10]] if x)
            L.append(f"- [新聞{i}] {n['title']}" + (f"（{meta}）" if meta else ""))
    else:
        L.append("- 無新聞")
    L.append("")

    L.append("---")
    L.append(_OUTPUT_SCHEMA)
    return "\n".join(L)


# ── claude 執行 + JSON 解析 ──────────────────────────────────────────────────────

def _run_claude(prompt: str) -> str:
    """跑一次 claude；額度用罄時 raise ClaudeQuotaExceeded（帶 reset 時間）而不是回垃圾。

    以前這裡直接吃 stdout，撞到額度上限時 claude 回的是
    「You've hit your session limit · resets 5:40am」這句純文字，_parse_json 會把它
    當成 JSON 解析失敗 → 每一檔都白打一次 subprocess。改走 claude_guard 之後，
    第一檔就會拿到 reset 時間並中止整批（見 ai_report.run_ai_report）。
    """
    try:
        return _guard.run(prompt, cwd=str(settings.PROJECT_ROOT), timeout=300).text
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail="claude 分析超時（>5 分鐘）") from e
    except RuntimeError as e:
        if _guard and isinstance(e, _guard.QuotaExceeded):
            raise
        raise HTTPException(status_code=500, detail=str(e)) from e


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


# ── 條列式分析 + 來源標註 ────────────────────────────────────────────────────────
# 模型只准回「來源代號」（報價/技術面/估值/大盤/新聞N），連結與媒體名一律由程式
# 從實際新聞清單回填 —— 這樣引用的網址不可能是捏造的。

_SOURCE_LABELS = {
    "報價": "stock-radar 即時報價",
    "技術面": "stock-radar 技術面",
    "估值": "stock-radar 估值 DB",
    "大盤": "stock-radar 大盤背景",
}
_NEWS_REF_RE = re.compile(r"^新聞\s*\[?(\d{1,2})\]?$")
_BULLET_TAGS = ("技術面", "估值", "新聞", "催化", "風險", "操作")


def _resolve_sources(tokens, news: list[dict]) -> list[dict]:
    """來源代號 → [{label, url, title}]；無法對應的代號直接丟掉。"""
    if isinstance(tokens, str):
        tokens = [tokens]
    out: list[dict] = []
    seen: set[str] = set()
    for tok in tokens or []:
        tok = str(tok).strip()
        m = _NEWS_REF_RE.match(tok)
        if m:
            idx = int(m.group(1)) - 1
            if not (0 <= idx < len(news)):
                continue
            n = news[idx]
            day = (n.get("published_at") or "")[:10]
            label = " · ".join(x for x in [n.get("source") or "新聞", day] if x)
            entry = {"label": label, "url": n.get("url"), "title": n.get("title")}
        elif tok in _SOURCE_LABELS:
            entry = {"label": _SOURCE_LABELS[tok], "url": None, "title": None}
        else:
            continue
        key = f"{entry['label']}|{entry['url'] or ''}"
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out[:3]


def _normalize_bullets(raw, news: list[dict]) -> list[dict]:
    """把模型的 analysis_bullets 正規化成 [{tag, point, sources}]。"""
    bullets: list[dict] = []
    for b in raw or []:
        if isinstance(b, str):
            point, tag, srcs = b.strip(), None, []
        elif isinstance(b, dict):
            point = str(b.get("point") or "").strip()
            tag = str(b.get("tag") or "").strip() or None
            srcs = b.get("sources") or b.get("source") or []
        else:
            continue
        if not point:
            continue
        if tag and tag not in _BULLET_TAGS:
            tag = next((t for t in _BULLET_TAGS if t in tag), None)
        bullets.append({"tag": tag, "point": point, "sources": _resolve_sources(srcs, news)})
    return bullets


def _sources_md(sources: list[dict]) -> str:
    if not sources:
        return ""
    parts = [f"[{s['label']}]({s['url']})" if s.get("url") else s["label"] for s in sources]
    return f"（來源：{'、'.join(parts)}）"


def _bullets_to_markdown(bullets: list[dict]) -> str:
    """條列式正文；每條結尾帶來源（新聞來源是可點的 markdown 連結）。"""
    lines = []
    for b in bullets:
        tag = f"**{b['tag']}**：" if b.get("tag") else ""
        lines.append(f"- {tag}{b['point']}{_sources_md(b.get('sources') or [])}")
    return "\n".join(lines)


# ── 可複用核心：產生單檔決策卡 ────────────────────────────────────────────────────
# endpoint 與 scheduler 的 AI 持倉日報都呼叫這個函數，避免邏輯重複。

def generate_card(
    ticker: str,
    db: Session,
    refresh: bool = False,
    as_of: date | None = None,
) -> dict:
    """蒐集資料 → claude -p → 解析 → 存當日快取，回傳決策卡 payload。

    as_of 可覆寫快取所掛的日期，用於「重跑某一天的日報」。注意行情/技術面永遠取
    當下的即時資料，所以只有在那天之後尚未開過盤時（例如週末重跑週五那份）才等價。
    """
    ticker = ticker.upper().strip()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", ticker):
        raise HTTPException(status_code=400, detail="invalid ticker")

    card_date = (as_of or date.today()).isoformat()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{ticker}_{card_date}.json"

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
    bullets = _normalize_bullets(card.get("analysis_bullets"), news)

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
        # 條列式正文：結構化 bullets（前端渲染用）+ 由 bullets 組出的 markdown（日報 / Obsidian 用）
        "analysis_bullets": bullets,
        "analysis": _bullets_to_markdown(bullets) if bullets else card.get("analysis"),
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
