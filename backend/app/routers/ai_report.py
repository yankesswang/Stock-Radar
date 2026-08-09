"""
AI 持倉卡批次預產（快取來源）—— 對所有 portfolio 持倉批次跑 per-stock AI 決策卡，
寫入快取，供 scripts/daily_analysis.py 收盤分析當逐股 context 讀取。

⚠️ 這「不是」獨立推播的日報。預設只產卡 + 快取，不推 Telegram、不寫 Obsidian。
   收盤通知由 scripts/daily_analysis.py 統一產出（它會讀 data/ai_analysis 的卡片快取）。

由 stock digest 家族的 launchd（~/Library/Scripts/stock-digest-portfolio.sh，台北 5:00，
排在 5:30 收盤分析之前）觸發 POST /api/scheduler/ai-report/run；也可前端手動觸發。

產出：
  - data/ai_analysis/{TICKER}_{date}.json  每檔卡片快取（generate_card 寫，收盤分析讀）
  - data/ai_reports/{date}.json            當日彙整（前端 /ai-report 一覽用）
  （to_obsidian / to_telegram 預設 False；僅在明確要求時才額外推播）

讀取 endpoints（前端用）：
  GET /api/ai-report           — 可用日期清單（新到舊）
  GET /api/ai-report/latest    — 最新一份
  GET /api/ai-report/{date}    — 指定日期
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..database import SessionLocal
from .ai_analysis import generate_card

router = APIRouter()

PROJECT_ROOT = Path(settings.PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
REPORT_DIR = Path(settings.DATA_DIR) / "ai_reports"
OBSIDIAN_DIR = Path("/Users/yankesswang/Documents/arthurwang_DB/投資/Stock Digest")
_DATE_RE_LEN = 10  # YYYY-MM-DD

# 批次跑 claude 的併發數（claude CLI 偏慢，保守限流避免被卡）
_MAX_WORKERS = 2


# ── 持倉蒐集 ────────────────────────────────────────────────────────────────────

def _collect_holdings() -> list[dict]:
    """所有 CSV portfolio 的非現金持倉，去重後保留所屬組合與權重。"""
    from .portfolio import get_csv_portfolio

    db = SessionLocal()
    try:
        payload = get_csv_portfolio(name=None, db=db)
    finally:
        db.close()

    portfolios = payload.get("portfolios", {}) if isinstance(payload, dict) else {}
    names = payload.get("portfolio_names", []) if isinstance(payload, dict) else []

    seen: dict[str, dict] = {}
    for pname in names:
        for h in portfolios.get(pname, {}).get("holdings", []):
            t = (h.get("ticker") or "").upper()
            if not t or t == "CASH":
                continue
            # 同一檔可能跨組合持有；保留第一次出現，記下所屬組合
            if t not in seen:
                seen[t] = {
                    "ticker": t,
                    "company_name": h.get("company_name", t),
                    "portfolio": pname,
                    "weight_pct": h.get("weight_pct"),
                    "return_pct": h.get("return_pct"),
                }
    # 依權重排序（高→低）
    return sorted(seen.values(), key=lambda x: -(x.get("weight_pct") or 0))


# ── 單檔卡片（獨立 session，供多執行緒使用）──────────────────────────────────────

def _card_for(ticker: str, refresh: bool) -> dict | None:
    db = SessionLocal()
    try:
        return generate_card(ticker, db, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 — 單檔失敗不該中斷整份日報
        print(f"[ai-report] {ticker} 失敗：{exc}")
        return None
    finally:
        db.close()


# ── Markdown / 摘要組裝 ──────────────────────────────────────────────────────────

def _rating_icon(rating: str | None) -> str:
    return {
        "強烈看多": "🟢🟢", "偏多": "🟢", "中性": "⚪", "偏空": "🔴", "強烈看空": "🔴🔴",
    }.get(rating or "", "⚪")


def _build_markdown(report_date: str, holdings: list[dict], cards: dict[str, dict]) -> str:
    now = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M 台北時間")
    L: list[str] = []
    L.append(f"# 🤖 AI 持倉決策日報 — {report_date}")
    L.append(f"> 產生於 {now}｜共 {len(cards)} 檔持倉")
    L.append("")

    # 摘要表
    L.append("## 📊 持倉決策摘要")
    L.append("")
    L.append("| 持倉 | 權重 | 評分 | 評級 | 建議 | 核心結論 |")
    L.append("|------|------|------|------|------|---------|")
    for h in holdings:
        c = cards.get(h["ticker"])
        if not c:
            continue
        w = f"{h['weight_pct']:.1f}%" if h.get("weight_pct") is not None else "—"
        L.append(
            f"| {h['ticker']} | {w} | {c.get('score', '—')} | "
            f"{_rating_icon(c.get('rating'))} {c.get('rating', '—')} | "
            f"{c.get('action', '—')} | {c.get('summary', '—')} |"
        )
    L.append("")

    # 逐股
    L.append("## 💼 逐檔分析")
    L.append("")
    for h in holdings:
        c = cards.get(h["ticker"])
        if not c:
            continue
        w = f"權重 {h['weight_pct']:.1f}%" if h.get("weight_pct") is not None else ""
        ret = f"｜總報酬 {h['return_pct']:+.1f}%" if h.get("return_pct") is not None else ""
        L.append(f"### {h['ticker']}（{c.get('company_name', h['company_name'])}）— {w}{ret}")
        L.append("")
        L.append(f"**評分 {c.get('score', '—')}／100｜{c.get('rating', '—')}｜建議：{c.get('action', '—')}**")
        L.append("")
        L.append(f"- 趨勢：{c.get('trend', '—')}")
        L.append(f"- 進場區：{c.get('buy_zone', '—')}｜目標：{c.get('target', '—')}｜停損：{c.get('stop_loss', '—')}")
        if c.get("risks"):
            L.append("- ⚠️ 風險：" + "；".join(c["risks"]))
        if c.get("catalysts"):
            L.append("- ⚡ 催化：" + "；".join(c["catalysts"]))
        L.append("")
        if c.get("analysis"):
            L.append(c["analysis"].strip())
        L.append("")
        L.append("---")
        L.append("")

    L.append("*由 claude 依 stock-radar 即時資料產生，僅供參考，非投資建議。*")
    return "\n".join(L)


def _build_telegram_summary(report_date: str, holdings: list[dict], cards: dict[str, dict]) -> str:
    lines = [f"🤖 AI 持倉決策日報｜{report_date}", ""]
    for h in holdings:
        c = cards.get(h["ticker"])
        if not c:
            continue
        lines.append(
            f"{_rating_icon(c.get('rating'))} {h['ticker']} "
            f"評分{c.get('score', '—')}｜{c.get('action', '—')}"
        )
        if c.get("summary"):
            lines.append(f"    {c['summary']}")
    return "\n".join(lines)


# ── 核心：產生整份日報（供 scheduler / 手動觸發呼叫）──────────────────────────────

def generate_ai_portfolio_report(
    *,
    refresh: bool = False,
    to_obsidian: bool = False,
    to_telegram: bool = False,
) -> dict:
    """批次跑所有持倉的 AI 決策卡，寫入快取。回傳 {date, total, ok, errors, path}。

    預設只產卡 + 快取（給收盤分析當 context）；to_obsidian/to_telegram 預設 False。
    """
    report_date = date.today().isoformat()
    holdings = _collect_holdings()
    if not holdings:
        raise RuntimeError("找不到任何持倉（portfolio 為空？）")

    tickers = [h["ticker"] for h in holdings]
    cards: dict[str, dict] = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        for t, card in zip(tickers, ex.map(lambda x: _card_for(x, refresh), tickers)):
            if card:
                cards[t] = card
            else:
                errors.append(t)

    markdown = _build_markdown(report_date, holdings, cards)
    generated_at = datetime.now(TAIPEI_TZ).isoformat()

    # 1) 存檔（前端讀）
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": report_date,
        "generated_at": generated_at,
        "total": len(holdings),
        "ok": len(cards),
        "errors": errors,
        "holdings": holdings,
        "cards": [cards[h["ticker"]] for h in holdings if h["ticker"] in cards],
        "markdown": markdown,
    }
    (REPORT_DIR / f"{report_date}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORT_DIR / f"{report_date}.md").write_text(markdown, encoding="utf-8")

    # 2) Obsidian 備份
    if to_obsidian:
        try:
            day_dir = OBSIDIAN_DIR / report_date
            day_dir.mkdir(parents=True, exist_ok=True)
            (day_dir / "🤖 AI 持倉分析.md").write_text(markdown, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"[ai-report] Obsidian 寫入失敗：{exc}")

    # 3) Telegram 摘要
    if to_telegram and cards:
        try:
            from scripts.daily_analysis import send_telegram
            send_telegram(_build_telegram_summary(report_date, holdings, cards))
        except Exception as exc:  # noqa: BLE001
            print(f"[ai-report] Telegram 推播失敗：{exc}")

    return {
        "date": report_date,
        "total": len(holdings),
        "ok": len(cards),
        "errors": errors,
        "path": str(REPORT_DIR / f"{report_date}.json"),
    }


# ── 讀取 endpoints ───────────────────────────────────────────────────────────────

def _list_dates() -> list[str]:
    if not REPORT_DIR.exists():
        return []
    dates = [p.stem for p in REPORT_DIR.glob("*.json") if len(p.stem) == _DATE_RE_LEN]
    return sorted(dates, reverse=True)


@router.get("")
def list_reports() -> dict:
    return {"dates": _list_dates()}


@router.get("/latest")
def latest_report() -> dict:
    dates = _list_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="尚無 AI 持倉日報")
    return json.loads((REPORT_DIR / f"{dates[0]}.json").read_text(encoding="utf-8"))


@router.get("/{report_date}")
def get_report(report_date: str) -> dict:
    fp = REPORT_DIR / f"{report_date}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"無 {report_date} 的日報")
    return json.loads(fp.read_text(encoding="utf-8"))
