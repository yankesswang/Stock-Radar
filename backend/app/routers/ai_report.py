"""
AI 持倉卡批次預產（快取來源）—— 對所有 portfolio 持倉批次跑 per-stock AI 決策卡，
寫入快取，供 scripts/war_room.py 盤後復盤當逐股 context 讀取。

⚠️ 這「不是」獨立推播的日報，也不再是獨立的前端分頁。預設只產卡 + 快取，
   不推 Telegram、不寫 Obsidian。
   2026-09-03 統一：組合層結論（今天要動哪幾檔／組合姿態／全組合風險）只由
   🎖️ War Room 盤後復盤給；本檔以前多跑一次 claude 做的「總體重點分析」
   （overview）與復盤講的是同一件事、卻是另一套答案，預設關掉（with_overview=False）。
   逐檔卡片在前端「持倉復盤」分頁裡當證據層，與 War Room 決策逐檔對照。

由 stock digest 家族的 launchd（~/Library/Scripts/stock-digest-portfolio.sh，台北 5:00，
排在 5:30 收盤分析之前）觸發 POST /api/scheduler/ai-report/run；也可前端手動觸發。

產出：
  - data/ai_analysis/{TICKER}_{date}.json  每檔卡片快取（generate_card 寫，收盤分析讀）
  - data/ai_reports/{date}.json            當日彙整（/api/war-room/review/{date} 的 ai_cards 讀這裡）
  （to_obsidian / to_telegram 預設 False；僅在明確要求時才額外推播）

讀取 endpoints（保留給腳本／舊連結；前端改讀 /api/war-room/review/{date}.ai_cards）：
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
from .ai_analysis import _bullets_to_markdown, _parse_json, _run_claude, generate_card
from .ai_analysis import _guard

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


# ── 額度用罄時的延後補跑 ────────────────────────────────────────────────────────

# 撞到額度上限就把整條 pipeline（刷新快照 → 批次產卡）登記到 reset 之後重跑一次。
# 實際補跑由 LaunchAgent com.arthur.claude-retry（每 10 分鐘）掃佇列執行。
_PIPELINE_SCRIPT = Path.home() / "Library" / "Scripts" / "stock-digest-portfolio.sh"


def _defer_rerun(exc) -> None:
    """把今天的持倉卡預產登記到額度 reset 之後補跑，並盡量發個告警。"""
    try:
        _guard.defer(
            "stock-digest-portfolio",
            ["/bin/bash", str(_PIPELINE_SCRIPT)],
            exc.resets_at,
            cwd=str(PROJECT_ROOT),
        )
    except Exception as e:  # noqa: BLE001 — 登記失敗不該再炸一次
        print(f"[ai-report] 登記補跑失敗：{e}")

    when = exc.resets_at_dt
    msg = (f"⚠️ AI 持倉卡預產中止：claude 額度用罄"
           f"（{exc.limit_type}）。已登記在 "
           f"{when:%m-%d %H:%M} 之後自動補跑。" if when else
           "⚠️ AI 持倉卡預產中止：claude 額度用罄，reset 時間未知。")
    print(f"[ai-report] {msg}")
    try:
        from scripts.daily_analysis import send_telegram
        send_telegram(msg)
    except Exception:  # noqa: BLE001 — 告警是加分項，不能變成新的失敗點
        pass


# ── 單檔卡片（獨立 session，供多執行緒使用）──────────────────────────────────────

def _card_for(ticker: str, refresh: bool, as_of: date | None = None) -> dict | None:
    db = SessionLocal()
    try:
        return generate_card(ticker, db, refresh=refresh, as_of=as_of)
    except _guard.QuotaExceeded:
        raise            # 額度用罄是整批的事，交給 run_ai_report 中止並登記補跑
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


_OVERVIEW_SCHEMA = """你必須只輸出一個 JSON 物件（不要任何前後說明、不要 markdown code fence）：
{
  "market_view": "<一句話：今日市場定性（risk-on / risk-off / 分歧）與主要驅動力>",
  "portfolio_stance": "<一句話：整體組合該偏攻或偏守，含集中度／現金水位的看法>",
  "key_points": [
    {
      "point": "<跨持倉的重點結論，60 字內，要帶具體數字>",
      "tickers": ["<相關持倉代號>", "..."],
      "evidence": "<依據，寫明來自哪幾檔的哪個數據，例：NVDA RSI 74 + AMD 評分 72>"
    }
  ],
  "priority_actions": [
    {"ticker": "<代號>", "action": "<加碼|減碼|停利|停損|觀望|持有|建倉 擇一>", "reason": "<一句話理由，帶價位或條件>"}
  ],
  "top_risks": ["<全組合層級風險 1>", "<風險 2>", "..."]
}

規則：
- key_points 3-6 條，講的必須是「跨持倉」的事（共同曝險、集中度、板塊輪動、評分分佈、
  一致偏多/偏空的訊號），不要只複述單一檔的卡片內容。
- priority_actions 2-5 條，只列明天真的該優先處理的，依急迫度排序。
- top_risks 2-4 條。tickers 只能填下方實際出現過的持倉代號。
- 完全根據下方卡片資料，不要編造數字或不存在的持倉。全部繁體中文。"""


def _build_overview_prompt(report_date: str, holdings: list[dict], cards: dict[str, dict]) -> str:
    """把所有決策卡壓成精簡摘要，讓模型做一份跨持倉的總體重點分析。"""
    L: list[str] = []
    L.append(f"你是 Arthur 的投資組合戰情官。以下是 {report_date} 全部持倉的 AI 決策卡摘要，")
    L.append("請做一份「全組合總體重點分析」，放在日報最上方讓他 30 秒掌握全局。")
    L.append("")
    L.append("## 持倉決策卡摘要")
    for h in holdings:
        c = cards.get(h["ticker"])
        if not c:
            continue
        w = f"{h['weight_pct']:.1f}%" if h.get("weight_pct") is not None else "—"
        ret = f"{h['return_pct']:+.1f}%" if h.get("return_pct") is not None else "—"
        px = c.get("price") or {}
        today = f"{px['change_pct']:+.2f}%" if px.get("change_pct") is not None else "—"
        L.append("")
        L.append(
            f"### {h['ticker']}（{c.get('company_name', h['ticker'])}）"
            f"｜組合 {h.get('portfolio', '—')}｜權重 {w}｜總報酬 {ret}｜今日 {today}"
        )
        L.append(
            f"- 評分 {c.get('score', '—')}/100｜{c.get('rating', '—')}｜建議 {c.get('action', '—')}"
        )
        if c.get("summary"):
            L.append(f"- 核心：{c['summary']}")
        if c.get("trend"):
            L.append(f"- 趨勢：{c['trend']}")
        L.append(
            f"- 進場 {c.get('buy_zone', '—')}｜目標 {c.get('target', '—')}｜停損 {c.get('stop_loss', '—')}"
        )
        if c.get("risks"):
            L.append("- 風險：" + "；".join(c["risks"]))
        if c.get("catalysts"):
            L.append("- 催化：" + "；".join(c["catalysts"]))
        for b in (c.get("analysis_bullets") or [])[:6]:
            tag = f"[{b['tag']}] " if b.get("tag") else ""
            L.append(f"- {tag}{b.get('point', '')}")
    L.append("")
    L.append("---")
    L.append(_OVERVIEW_SCHEMA)
    return "\n".join(L)


def _generate_overview(report_date: str, holdings: list[dict], cards: dict[str, dict]) -> dict | None:
    """跑一次 claude 產出總體重點分析；失敗不影響日報本體。"""
    if not cards:
        return None
    valid = {h["ticker"] for h in holdings if h["ticker"] in cards}
    try:
        raw = _run_claude(_build_overview_prompt(report_date, holdings, cards))
        if not raw:
            return None
        data = _parse_json(raw)
    except Exception as exc:  # noqa: BLE001 — 總覽失敗就退回沒有總覽的日報
        print(f"[ai-report] 總體重點分析失敗：{exc}")
        return None

    def _clean_points(items) -> list[dict]:
        out = []
        for it in items or []:
            if isinstance(it, str):
                out.append({"point": it.strip(), "tickers": [], "evidence": None})
                continue
            if not isinstance(it, dict):
                continue
            point = str(it.get("point") or "").strip()
            if not point:
                continue
            out.append({
                "point": point,
                # 只留真的在持倉裡的代號，擋掉幻覺
                "tickers": [t for t in (it.get("tickers") or []) if str(t).upper() in valid],
                "evidence": (str(it.get("evidence")).strip() if it.get("evidence") else None),
            })
        return out

    def _clean_actions(items) -> list[dict]:
        out = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            t = str(it.get("ticker") or "").upper()
            if t not in valid:
                continue
            out.append({
                "ticker": t,
                "action": str(it.get("action") or "—").strip(),
                "reason": str(it.get("reason") or "").strip(),
            })
        return out

    return {
        "market_view": (str(data.get("market_view")).strip() if data.get("market_view") else None),
        "portfolio_stance": (str(data.get("portfolio_stance")).strip() if data.get("portfolio_stance") else None),
        "key_points": _clean_points(data.get("key_points")),
        "priority_actions": _clean_actions(data.get("priority_actions")),
        "top_risks": [str(r).strip() for r in (data.get("top_risks") or []) if str(r).strip()],
    }


_ACTION_ICON = {
    # 含 per-stock 卡片會用到的動作詞，避免總覽沿用卡片用語時掉成無圖示
    "加碼": "🟢", "建倉": "🟢", "買入": "🟢", "分批買入": "🟢",
    "持有": "⚪", "觀望": "⚪",
    "減碼": "🟠", "停利": "🔵", "停損": "🔴", "賣出": "🔴",
}


def _overview_markdown(overview: dict) -> list[str]:
    L: list[str] = ["## 🧭 總體重點分析", ""]
    if overview.get("market_view"):
        L.append(f"**市場定性**：{overview['market_view']}")
    if overview.get("portfolio_stance"):
        L.append(f"**組合姿態**：{overview['portfolio_stance']}")
    if overview.get("market_view") or overview.get("portfolio_stance"):
        L.append("")

    if overview.get("key_points"):
        L.append("**跨持倉重點**")
        for kp in overview["key_points"]:
            tags = f"（{' · '.join(kp['tickers'])}）" if kp.get("tickers") else ""
            ev = f"　— 依據：{kp['evidence']}" if kp.get("evidence") else ""
            L.append(f"- {kp['point']}{tags}{ev}")
        L.append("")

    if overview.get("priority_actions"):
        L.append("**優先處理**")
        for a in overview["priority_actions"]:
            icon = _ACTION_ICON.get(a["action"], "•")
            L.append(f"- {icon} **{a['ticker']} {a['action']}**：{a['reason']}")
        L.append("")

    if overview.get("top_risks"):
        L.append("**全組合風險**")
        for r in overview["top_risks"]:
            L.append(f"- ⚠️ {r}")
        L.append("")

    L.append("---")
    L.append("")
    return L


def _build_markdown(
    report_date: str,
    holdings: list[dict],
    cards: dict[str, dict],
    overview: dict | None = None,
) -> str:
    now = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M 台北時間")
    L: list[str] = []
    L.append(f"# 🧑‍🍳 AI 持倉卡 — {report_date}")
    L.append(f"> 產生於 {now}｜共 {len(cards)} 檔持倉")
    L.append("> 逐檔卡片是 🎖️ War Room 盤後復盤的備料與證據層；買賣結論以復盤為準。")
    L.append("")

    # 總體重點分析（放最上方，先給全局再進逐檔）
    if overview:
        L.extend(_overview_markdown(overview))

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
        ret = f"總報酬 {h['return_pct']:+.1f}%" if h.get("return_pct") is not None else ""
        # 缺權重或缺報酬時不要留下「— ｜」這種空殼分隔
        meta = "｜".join(x for x in (w, ret) if x)
        head = f"### {h['ticker']}（{c.get('company_name', h['company_name'])}）"
        L.append(f"{head} — {meta}" if meta else head)
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
        # 分析正文：條列式，每條帶來源（新聞來源是 markdown 連結）
        if c.get("analysis"):
            L.append(c["analysis"].strip())
        elif c.get("analysis_bullets"):
            L.append(_bullets_to_markdown(c["analysis_bullets"]))
        L.append("")
        L.append("---")
        L.append("")

    L.append("*由 claude 依 stock-radar 即時資料產生，僅供參考，非投資建議。*")
    return "\n".join(L)


def _build_telegram_summary(
    report_date: str,
    holdings: list[dict],
    cards: dict[str, dict],
    overview: dict | None = None,
) -> str:
    lines = [f"🤖 AI 持倉決策日報｜{report_date}", ""]
    if overview:
        if overview.get("market_view"):
            lines.append(f"🧭 {overview['market_view']}")
        if overview.get("portfolio_stance"):
            lines.append(f"📐 {overview['portfolio_stance']}")
        for kp in (overview.get("key_points") or [])[:3]:
            tags = f"（{'·'.join(kp['tickers'])}）" if kp.get("tickers") else ""
            lines.append(f"• {kp['point']}{tags}")
        for a in (overview.get("priority_actions") or [])[:3]:
            lines.append(f"{_ACTION_ICON.get(a['action'], '•')} {a['ticker']} {a['action']}：{a['reason']}")
        lines.append("")
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
    with_overview: bool = False,
    as_of: date | None = None,
) -> dict:
    """批次跑所有持倉的 AI 決策卡，寫入快取。回傳 {date, total, ok, errors, path}。

    預設只產卡 + 快取（給盤後復盤當 context）；to_obsidian/to_telegram 預設 False。
    with_overview=True 會在逐檔卡片跑完後，多跑一次 claude 做跨持倉的總體重點分析。
    預設關閉 —— 那份總覽與 War Room 復盤回答同一個問題（今天要動哪幾檔），兩套答案
    只會互相打架；要補跑的話用 scripts/rebuild_ai_overview.py。

    as_of 用來重跑某一天的日報（卡片快取與日報檔都掛在那個日期）。資料一律取當下
    即時行情，所以只在那天之後還沒開過盤時才等價 —— 例如週末重跑週五那一份。
    """
    report_date = (as_of or date.today()).isoformat()
    holdings = _collect_holdings()
    if not holdings:
        raise RuntimeError("找不到任何持倉（portfolio 為空？）")

    tickers = [h["ticker"] for h in holdings]
    cards: dict[str, dict] = {}
    errors: list[str] = []

    # 額度用罄時中止整批：claude_guard 的共用 state 會讓後續呼叫直接短路（不再開
    # subprocess），這裡再把還沒開跑的 future 取消掉，避免重演「35 檔全打空砲」。
    quota_exc = None
    ex = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
    futures = {t: ex.submit(_card_for, t, refresh, as_of) for t in tickers}
    for t, fut in futures.items():
        if quota_exc is not None:
            fut.cancel()
            errors.append(t)
            continue
        try:
            card = fut.result()
        except _guard.QuotaExceeded as exc:
            quota_exc = exc
            errors.append(t)
            print(f"[ai-report] 額度用罄，中止本批：{exc}")
            continue
        if card:
            cards[t] = card
        else:
            errors.append(t)
    ex.shutdown(wait=True, cancel_futures=True)

    if quota_exc is not None:
        _defer_rerun(quota_exc)
        if not cards:
            # 一張卡都沒產出就不要落地 —— 以前這裡會把當天的報告覆蓋成空表格，
            # 前端於是顯示「持倉決策摘要 (0/35 檔)」。保留舊檔，等補跑。
            return {
                "date": report_date, "total": len(holdings), "ok": 0,
                "errors": errors, "deferred_until": quota_exc.resets_at,
                "detail": f"claude 額度用罄，已登記補跑：{quota_exc}",
            }

    overview_ok = with_overview and quota_exc is None

    overview = _generate_overview(report_date, holdings, cards) if overview_ok else None
    markdown = _build_markdown(report_date, holdings, cards, overview)
    generated_at = datetime.now(TAIPEI_TZ).isoformat()

    # 1) 存檔（前端讀）
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": report_date,
        "generated_at": generated_at,
        "total": len(holdings),
        "ok": len(cards),
        "errors": errors,
        "overview": overview,
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
            send_telegram(_build_telegram_summary(report_date, holdings, cards, overview))
        except Exception as exc:  # noqa: BLE001
            print(f"[ai-report] Telegram 推播失敗：{exc}")

    return {
        "date": report_date,
        "total": len(holdings),
        "ok": len(cards),
        "errors": errors,
        "path": str(REPORT_DIR / f"{report_date}.json"),
        "deferred_until": quota_exc.resets_at if quota_exc else None,
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
        raise HTTPException(status_code=404, detail="尚無 AI 持倉卡")
    return json.loads((REPORT_DIR / f"{dates[0]}.json").read_text(encoding="utf-8"))


@router.get("/{report_date}")
def get_report(report_date: str) -> dict:
    fp = REPORT_DIR / f"{report_date}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"無 {report_date} 的日報")
    return json.loads(fp.read_text(encoding="utf-8"))
