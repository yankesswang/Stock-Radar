"""個股頁各切面「有沒有資料」與一句話結論 —— GET /api/stocks/{ticker}/tabs

前端的分頁列靠這支把沒資料的切面變灰、有筆數的加角標；主頁的總覽列拿同一份
回應畫每個切面的結論（DCF 加權合理價、PE 中位數合理價、分析師目標價、同業推算價、
AI 立場、最新一季財報筆記）。

所以這支必須便宜：只看檔案在不在、讀既有的 JSON、查 DB。絕不觸發爬蟲、DCF 重算
或 AI 生成 —— 那些各自的端點會做，這裡只回報現況。沒檔案就是 available=False，
前端自己決定要不要引導使用者去產生。

每個切面固定這幾個欄位（沒有的省略）：
  available   True / False / None（None＝即時抓的資料，事前不知道有沒有，例如選擇權）
  value       這個切面算出的每股合理價或目標價
  upside_pct  (value − 現價) ÷ 現價 × 100；正值＝還有上檔＝低估
  note        一行補充（可信度、幾位分析師、中位 PE…）
  date        資料日
  count       有計數意義的切面（財報筆記幾季）
"""
import json
import re
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Stock, Valuation
from .compare import compare_with_peers
from .dcf import MAX_CACHE_AGE_DAYS, _file_age_days, _find_latest_json as _dcf_file
from .earnings_notes import _meta as _note_meta, _note_file, _quarter_dirs
from .financials import _rows as _fin_rows
from .forecast import _find_latest_json as _forecast_file
from .metrics_breakdown import _find_latest_csv as _metrics_file
from .pe import _find_latest as _pe_file
from .ratios import _find_latest_csv as _ratios_file
from .statistics import _find_latest_json as _stats_file

router = APIRouter()

AI_DIR: Path = Path(settings.DATA_DIR) / "ai_analysis"


def _load(path: Path | None) -> dict | None:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _date_in_name(path: Path | None) -> str | None:
    if not path:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return m.group(1) if m else None


def _upside(value, price) -> float | None:
    try:
        if value is None or not price:
            return None
        return round((float(value) - float(price)) / float(price) * 100, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _latest_ai_file(ticker: str) -> Path | None:
    files = sorted(AI_DIR.glob(f"{ticker}_*.json"), reverse=True) if AI_DIR.exists() else []
    return files[0] if files else None


@router.get("/{ticker}/tabs")
def stock_tabs(ticker: str, db: Session = Depends(get_db)) -> dict:
    t = ticker.upper()

    stock = db.query(Stock).filter(func.upper(Stock.ticker) == t).first()
    val = None
    if stock:
        val = (
            db.query(Valuation)
            .filter_by(stock_id=stock.id)
            .order_by(Valuation.snapshot_date.desc())
            .first()
        )
    price = getattr(val, "current_price", None)
    tabs: dict[str, dict] = {}

    # ── 估值 ────────────────────────────────────────────────────────────────
    dcf_path = _dcf_file(t)
    dcf = _load(dcf_path)
    if dcf:
        fv = dcf.get("fair_value") or {}
        conf = dcf.get("confidence") or {}
        app = dcf.get("applicability") or {}
        age = _file_age_days(dcf_path) if dcf_path else None
        note = []
        if conf.get("grade"):
            note.append(f"可信度 {conf['grade']}（{conf.get('score', '—')}/100）")
        if app.get("meaningful") is False:
            note.append("模型不適用")
        if age is not None and age > MAX_CACHE_AGE_DAYS:
            note.append(f"{age} 天前算的，打開會重算")
        tabs["dcf"] = {
            "available": True,
            "value": fv.get("weighted"),
            "upside_pct": _upside(fv.get("weighted"), dcf.get("current_price") or price),
            "meaningful": app.get("meaningful"),
            "grade": conf.get("grade"),
            "note": " · ".join(note) or None,
            "date": dcf.get("computed_date") or _date_in_name(dcf_path),
        }
    else:
        # 沒算過：只要統計與預估都在，DCF 頁打開就會現算；缺輸入就算不出來
        can = bool(_stats_file(t) and _forecast_file(t))
        tabs["dcf"] = {"available": can,
                       "note": "尚未計算，打開分頁會現算" if can else "缺統計或分析師預估資料，算不出 DCF"}

    pe_path = _pe_file(t)
    pe_fair = getattr(val, "pe_median_price", None)
    pe_median_5y = getattr(val, "pe_median_5y", None)
    # PE 歷史頁要的是 fullratio 檔；估值 DB 裡的中位數合理價可以獨立存在（總覽列照樣能畫）
    tabs["pe"] = {
        "available": pe_path is not None,
        "value": pe_fair,
        "upside_pct": _upside(pe_fair, price),
        "next_year_value": getattr(val, "next_year_fair_price", None),
        "next_year_upside_pct": _upside(getattr(val, "next_year_fair_price", None), price),
        # 有中位數卻沒合理價＝估值那邊判定失真（中位數遠高於 forward PE、EPS 有一次性損益、
        # 或外國公司換算不出美元 EPS），見 src.db_service.compute_valuation
        "note": ((f"5 年中位 PE {pe_median_5y}x"
                  + ("（歷史 PE 或 EPS 失真，不估合理價）" if pe_fair is None else ""))
                 if pe_median_5y else None) if pe_path
                else "尚未抓 fullratio 的 PE 歷史" + (f"；估值快照 5 年中位 PE {pe_median_5y}x" if pe_median_5y else ""),
        "date": _date_in_name(pe_path) or getattr(val, "snapshot_date", None),
    }

    fc = _load(_forecast_file(t))
    if fc:
        pt = fc.get("price_target") or {}
        ratings = fc.get("analyst_ratings") or {}
        n = 0
        for k in ("strong_buy", "buy", "hold", "sell", "strong_sell"):
            seq = ratings.get(k) or []
            n += (seq[-1] or 0) if seq else 0
        note = []
        if n:
            note.append(f"{n} 位分析師")
        if pt.get("low") is not None and pt.get("high") is not None:
            note.append(f"區間 ${pt['low']:,.0f}–{pt['high']:,.0f}")
        tabs["forecast"] = {
            "available": True,
            "value": pt.get("average"),
            "upside_pct": _upside(pt.get("average"), price) if pt.get("average_change_pct") is None else pt.get("average_change_pct"),
            "note": " · ".join(note) or None,
            "date": fc.get("date"),
        }
    else:
        tabs["forecast"] = {"available": False}

    try:
        peers = compare_with_peers(t, db) if stock else None
    except Exception:  # noqa: BLE001 —— 同業比較掛了不該讓整條分頁列掛掉
        peers = None
    if peers and peers.get("peer_count", 0) > 1:
        im = peers.get("implied") or {}
        cy = im.get("current_year") or {}
        pe_stat = next((s for s in peers.get("stats") or [] if s.get("key") == "pe"), None)
        note = [f"{peers['peer_count'] - 1} 檔同業"]
        if im.get("peer_median_pe"):
            note.append(f"同業中位 P/E {im['peer_median_pe']}x")
        if pe_stat and pe_stat.get("rank") is not None:
            note.append(f"P/E 第 {pe_stat['rank']} 便宜／{pe_stat.get('rank_total')}")
        tabs["peers"] = {
            "available": True,
            "value": cy.get("fair_price"),
            "upside_pct": _upside(cy.get("fair_price"), price),
            "count": peers["peer_count"] - 1,
            "note": " · ".join(note),
        }
    else:
        tabs["peers"] = {"available": False, "note": "這個產業只有這一檔在追蹤" if peers else None}

    # ── 基本面 ──────────────────────────────────────────────────────────────
    fin = _fin_rows(
        "SELECT COUNT(DISTINCT period_end) n, MIN(period_end) mn, MAX(period_end) mx "
        "FROM financial_facts WHERE ticker=?", (t,))
    fin_n = fin[0]["n"] if fin else 0
    tabs["financials"] = {
        "available": fin_n > 0,
        "note": f"SEC {fin[0]['mn'][:4]}–{fin[0]['mx'][:4]} 共 {fin_n} 期" if fin_n else None,
        "date": fin[0]["mx"] if fin_n else None,
    }
    ratios_path = _ratios_file(t)
    tabs["ratios"] = {"available": ratios_path is not None, "date": _date_in_name(ratios_path)}
    stats_path = _stats_file(t)
    tabs["statistics"] = {"available": stats_path is not None, "date": _date_in_name(stats_path)}
    metrics_path = _metrics_file(t)
    tabs["metrics"] = {"available": metrics_path is not None, "date": _date_in_name(metrics_path)}
    seg = _fin_rows("SELECT COUNT(DISTINCT period_end) n, MAX(period_end) mx "
                    "FROM segment_financials WHERE ticker=?", (t,))
    seg_n = seg[0]["n"] if seg else 0
    tabs["segments"] = {"available": seg_n > 0, "date": seg[0]["mx"] if seg_n else None}

    # ── 市場 ────────────────────────────────────────────────────────────────
    tabs["options"] = {"available": None}   # yfinance 即時抓，打開才知道
    tabs["news"] = {"available": None}

    # ── 觀點 ────────────────────────────────────────────────────────────────
    ai_path = _latest_ai_file(t)
    ai = _load(ai_path)
    if ai:
        head = " / ".join(x for x in (ai.get("rating"), ai.get("action")) if x)
        tabs["ai"] = {
            "available": True,
            "headline": head or None,
            "score": ai.get("score"),
            "target": ai.get("target"),
            "note": ai.get("summary"),
            "date": (ai.get("generated_at") or "")[:10] or _date_in_name(ai_path),
        }
    else:
        tabs["ai"] = {"available": False, "note": "尚未產生，打開分頁會生成（約 20–90 秒）"}

    qdirs = [d for d in _quarter_dirs(t) if _note_file(d)]
    if qdirs:
        latest = _note_meta(qdirs[0])
        tabs["notes"] = {
            "available": True,
            "count": len(qdirs),
            "headline": latest["quarter"].replace("_", " "),
            "note": latest.get("price_after_earnings") or latest.get("title"),
            "date": latest.get("report_date") or None,
        }
    else:
        tabs["notes"] = {"available": False, "count": 0}

    return {
        "ticker": t,
        "price": price,
        "as_of": getattr(val, "snapshot_date", None) or date.today().isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tabs": tabs,
    }
