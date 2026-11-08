#!/usr/bin/env python3
"""
組合層級風險 —— 從「單股風險」升級成「這個組合真正的風險長什麼樣」。

問題來源（2026-08-23 檢討）：
  「單一持倉不超過 25%」對現況幫助有限。真正的風險是 NVDA / SMH / TSM / MU / ARM /
  ADI / TXN / VRT 同時暴露在 AI 半導體這一個因子上 —— 逐檔看每檔都很健康，
  合起來卻是一注壓在同一個賭盤。另外：
    - 同一 ticker 多批持倉（Mom 的 TSLA 在永豐與富邦各一筆）沒有合併檢視
    - 台股與美股混在一起，`$1.00` 究竟是 USD 還是 TWD 有歧義
    - 停損價位散落在各檔，沒人算過「全部同時觸發會賠多少」
    - 財報日只看單檔，沒算過「NVDA 那天會同時影響組合的幾 %」

本模組把這些算成結構化資料（compute_portfolio_risk）＋ markdown（render_risk_md），
給 war_room 簡報、三層報告的第一層、以及 /api/war-room 前端共用。

CLI：
    uv run python scripts/portfolio_risk.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

SINGLE_NAME_LIMIT_PCT = 25.0      # 既有硬規則：單一持倉權重上限
THEME_WARN_PCT = 35.0             # 單一主題曝險警戒線
THEME_ALERT_PCT = 50.0            # 單一主題曝險紅線
EARNINGS_CLUSTER_DAYS = 14        # 財報群聚只看未來兩週


def _stock_list() -> dict[str, set[str]]:
    try:
        from src.config import STOCK_LIST
        return {k: {str(t).upper() for t in v} for k, v in STOCK_LIST.items()}
    except Exception:
        return {}


def _build_theme_map() -> list[tuple[str, set[str]]]:
    """主題 → ticker 集合，**依序**比對（一檔只歸一個主題，先到先得）。

    AI／半導體鏈刻意涵蓋設備、記憶體、EMS、電源與網通 —— 它們在 AI 資本支出敘事反轉時
    是一起跌的，分開列會嚴重低估真實集中度。
    """
    sl = _stock_list()
    ai_semi = (
        sl.get("半導體", set()) | sl.get("ai半導體", set()) | sl.get("記憶體", set())
        | {
            # ETF / 槓桿
            "SMH", "SOXX", "SOXL", "NVDL",
            # 電源、散熱、網通、光通訊（AI 資料中心供應鏈）
            "VRT", "ANET", "VIAV", "LITE", "FN", "CLS", "TTMI", "COHR",
            # 設備 / 材料 / 代工
            "ICHR", "ACMR", "AMAT", "ASML", "LRCX", "KLAC", "ENTG", "ASX", "UCTT",
            # EMS（AI 伺服器組裝）
            "JBL", "SANM", "SMCI", "FLEX",
            # 記憶體 / 邏輯
            "MU", "TSM", "NVDA", "AVGO", "AMD", "ARM", "QCOM", "TXN", "ADI", "INTC",
            "MRVL", "NXPI", "SWKS", "QRVO", "LSCC", "ALAB", "WOLF",
        }
    )
    big_tech = sl.get("大科技", set()) | {"GOOGL", "QQQ", "MAGS"}
    software = sl.get("SaaS", set()) | sl.get("軟體", set()) | sl.get("資安", set()) | {"IBM", "NOW", "WDAY", "CRM"}
    robotics = sl.get("機器人", set()) | {"OUST", "AEVA", "SYM", "RR", "UMAC", "PDYN", "RDW", "BKSY"}
    healthcare = sl.get("傳統", set()) | {"LLY", "UNH", "ISRG", "TMDX"}
    financial = sl.get("銀行", set()) | sl.get("支付", set()) | {"SOFI", "NU"}
    energy = sl.get("原油", set()) | {"OKLO", "FSLR", "BE", "FCEL", "ENPH", "SEDG"}
    broad_etf = {"VT", "SPY", "VOO", "IWM", "DIA", "GLD", "TLT", "VIXY", "UVXY"}
    return [
        ("AI／半導體鏈", ai_semi),
        ("大型科技平台", big_tech),
        ("企業軟體／SaaS", software),
        ("機器人／無人機／太空", robotics),
        ("能源／電力", energy),
        ("醫療／防禦", healthcare),
        ("金融", financial),
        ("大盤／避險 ETF", broad_etf),
    ]


_THEME_MAP = None


def theme_of(ticker: str, industry: str | None = None) -> str:
    global _THEME_MAP
    if _THEME_MAP is None:
        _THEME_MAP = _build_theme_map()
    t = str(ticker or "").upper()
    if t.endswith(".TW"):
        return "台股（原幣 TWD）"
    for name, members in _THEME_MAP:
        if t in members:
            return name
    if industry:
        return f"其他｜{industry}"
    return "其他"


# ── 合併持倉 ──────────────────────────────────────────────────────────────────

def merge_positions(portfolio_payload: dict) -> dict:
    """同 ticker 跨組合、跨帳戶合併 —— 這才是真正的曝險單位。"""
    portfolios = (portfolio_payload or {}).get("portfolios", {}) or {}
    merged: dict[str, dict] = {}
    total_value = 0.0
    cash_value = 0.0
    usd_twd_rate = None

    for pname, pdata in portfolios.items():
        summary = pdata.get("summary", {}) or {}
        usd_twd_rate = usd_twd_rate or summary.get("usd_twd_rate")
        total_value += float(summary.get("total_value") or 0.0)
        for h in pdata.get("holdings", []) or []:
            t = str(h.get("ticker") or "").upper()
            mv = float(h.get("market_value") or h.get("cost_basis") or 0.0)
            if t == "CASH":
                cash_value += mv
                continue
            if not t:
                continue
            row = merged.setdefault(t, {
                "ticker": t,
                "company_name": h.get("company_name") or t,
                "industry": h.get("industry"),
                "shares": 0.0,
                "market_value": 0.0,
                "cost_basis": 0.0,
                "lots": [],
                "portfolios": {},
                "current_price": h.get("current_price"),
                "day_change_pct": h.get("day_change_pct"),
                "max_leverage": 1.0,
            })
            shares = float(h.get("shares") or 0.0)
            lev = float(h.get("leverage") or 1.0)
            row["shares"] += shares
            row["market_value"] += mv
            row["cost_basis"] += float(h.get("cost_basis") or 0.0)
            row["max_leverage"] = max(row["max_leverage"], lev)
            row["portfolios"][pname] = round(row["portfolios"].get(pname, 0.0) + mv, 2)
            row["lots"].append({
                "portfolio": pname, "account": h.get("account"),
                "shares": round(shares, 4), "avg_cost": h.get("avg_cost"),
                "market_value": round(mv, 2), "return_pct": h.get("return_pct"),
            })

    for row in merged.values():
        row["shares"] = round(row["shares"], 4)
        row["market_value"] = round(row["market_value"], 2)
        row["cost_basis"] = round(row["cost_basis"], 2)
        row["weight_pct"] = round(row["market_value"] / total_value * 100, 2) if total_value else None
        row["theme"] = theme_of(row["ticker"], row.get("industry"))
        row["multi_lot"] = len(row["lots"]) > 1
        row["return_pct"] = (
            round((row["market_value"] - row["cost_basis"]) / row["cost_basis"] * 100, 2)
            if row["cost_basis"] else None
        )
    return {
        "positions": merged,
        "total_value": round(total_value, 2),
        "cash_value": round(cash_value, 2),
        "cash_pct": round(cash_value / total_value * 100, 2) if total_value else 0.0,
        "usd_twd_rate": usd_twd_rate,
    }


# ── 各項風險 ──────────────────────────────────────────────────────────────────

def _exposure_by(merged: dict, key: str) -> list[dict]:
    total = merged["total_value"] or 0.0
    buckets: dict[str, dict] = {}
    for row in merged["positions"].values():
        k = row.get(key) or "其他"
        b = buckets.setdefault(k, {"name": k, "value": 0.0, "tickers": []})
        b["value"] += row["market_value"]
        b["tickers"].append(row["ticker"])
    out = []
    for b in buckets.values():
        out.append({
            "name": b["name"],
            "value": round(b["value"], 2),
            "weight_pct": round(b["value"] / total * 100, 2) if total else None,
            "tickers": sorted(b["tickers"]),
            "count": len(b["tickers"]),
        })
    out.sort(key=lambda x: -(x["weight_pct"] or 0))
    return out


def _latest_stops(journals: list[tuple[date, dict]]) -> dict[str, dict]:
    """每檔最新一筆帶 `stop` 數字的決策。"""
    stops: dict[str, dict] = {}
    for jd, journal in journals or []:
        for dec in (journal.get("decisions") or []):
            if not isinstance(dec, dict):
                continue
            t = str(dec.get("ticker") or "").upper()
            stop = dec.get("stop")
            if not t or not isinstance(stop, (int, float)) or stop <= 0:
                continue
            stops[t] = {"stop": float(stop), "date": jd.isoformat(),
                        "action": dec.get("action"), "thesis": str(dec.get("thesis") or "")[:80]}
    return stops


def compute_stop_cluster(merged: dict, journals: list[tuple[date, dict]]) -> dict:
    """全部停損同時觸發的預估損失（相對現值）。"""
    stops = _latest_stops(journals)
    rows = []
    total_loss = 0.0
    covered_value = 0.0
    for t, row in merged["positions"].items():
        s = stops.get(t)
        price = row.get("current_price")
        if not s or not price or not row.get("shares"):
            continue
        loss = (price - s["stop"]) * row["shares"]     # 現價跌到停損價的損失（已破線者為負→視為已超額）
        covered_value += row["market_value"]
        total_loss += loss
        rows.append({
            "ticker": t, "shares": row["shares"], "price": round(price, 2),
            "stop": round(s["stop"], 2), "stop_date": s["date"],
            "downside_usd": round(loss, 2),
            "downside_pct": round((s["stop"] - price) / price * 100, 2),
            "already_below": s["stop"] > price,
        })
    rows.sort(key=lambda r: -(r["downside_usd"] or 0))
    total = merged["total_value"] or 0.0
    uncovered = [t for t, row in merged["positions"].items() if t not in stops]
    return {
        "rows": rows,
        "total_downside_usd": round(total_loss, 2),
        "total_downside_pct_of_portfolio": round(total_loss / total * 100, 2) if total else None,
        "covered_value": round(covered_value, 2),
        "covered_pct": round(covered_value / total * 100, 2) if total else None,
        "uncovered_tickers": sorted(uncovered),
    }


def compute_earnings_cluster(merged: dict, earnings_events: list[dict],
                             analysis_date: date) -> list[dict]:
    """未來兩週每個財報日「會同時影響組合幾 %」。"""
    total = merged["total_value"] or 0.0
    by_date: dict[str, dict] = {}
    for e in earnings_events or []:
        t = str(e.get("ticker") or "").upper()
        if t not in merged["positions"]:
            continue
        if e.get("status") == "reported":
            continue
        d = e.get("days_until")
        if d is None or not (0 <= d <= EARNINGS_CLUSTER_DAYS):
            continue
        slot = by_date.setdefault(e["date"], {
            "date": e["date"], "days_until": d, "tickers": [], "value": 0.0,
        })
        slot["tickers"].append(t)
        slot["value"] += merged["positions"][t]["market_value"]
    out = []
    for slot in by_date.values():
        out.append({
            **slot,
            "tickers": sorted(slot["tickers"]),
            "value": round(slot["value"], 2),
            "weight_pct": round(slot["value"] / total * 100, 2) if total else None,
        })
    out.sort(key=lambda x: x["date"])
    return out


BELLWETHERS = {"NVDA": "AI／半導體鏈", "TSM": "AI／半導體鏈", "AVGO": "AI／半導體鏈",
               "AMD": "AI／半導體鏈", "MU": "AI／半導體鏈", "ASML": "AI／半導體鏈",
               "MSFT": "大型科技平台", "GOOG": "大型科技平台", "META": "大型科技平台"}


def compute_bellwether_impact(merged: dict, earnings_events: list[dict],
                              theme_exposure: list[dict]) -> list[dict]:
    """指標股財報的**外溢**影響：NVDA 財報不只影響 NVDA，整條 AI 鏈都會跟著動。"""
    theme_by_name = {t["name"]: t for t in theme_exposure}
    out = []
    for e in earnings_events or []:
        t = str(e.get("ticker") or "").upper()
        if t not in BELLWETHERS or e.get("status") == "reported":
            continue
        d = e.get("days_until")
        if d is None or not (0 <= d <= EARNINGS_CLUSTER_DAYS):
            continue
        theme = BELLWETHERS[t]
        exp = theme_by_name.get(theme)
        if not exp:
            continue
        held = t in merged["positions"]
        out.append({
            "ticker": t, "date": e["date"], "days_until": d, "held": held,
            "theme": theme, "theme_weight_pct": exp["weight_pct"],
            "theme_tickers": exp["tickers"], "theme_value": exp["value"],
        })
    out.sort(key=lambda x: x["days_until"])
    return out


def compute_portfolio_risk(portfolio_payload: dict, *,
                           journals: list[tuple[date, dict]] | None = None,
                           earnings_events: list[dict] | None = None,
                           analysis_date: date | None = None) -> dict:
    """一次算完組合層風險。任何子項失敗都 degrade 成空，不 raise。"""
    analysis_date = analysis_date or date.today()
    merged = merge_positions(portfolio_payload)
    positions = merged["positions"]
    total = merged["total_value"] or 0.0

    theme_exposure = _exposure_by(merged, "theme")
    industry_exposure = _exposure_by(merged, "industry")

    ranked = sorted(positions.values(), key=lambda r: -(r["weight_pct"] or 0))
    concentration = {
        "top1_pct": ranked[0]["weight_pct"] if ranked else None,
        "top1_ticker": ranked[0]["ticker"] if ranked else None,
        "top3_pct": round(sum((r["weight_pct"] or 0) for r in ranked[:3]), 2),
        "top5_pct": round(sum((r["weight_pct"] or 0) for r in ranked[:5]), 2),
        "position_count": len(ranked),
        "over_limit": [
            {"ticker": r["ticker"], "weight_pct": r["weight_pct"]}
            for r in ranked if (r["weight_pct"] or 0) > SINGLE_NAME_LIMIT_PCT
        ],
    }

    warnings: list[dict] = []
    for th in theme_exposure:
        w = th["weight_pct"] or 0
        if w >= THEME_ALERT_PCT:
            warnings.append({"level": "high", "text":
                             f"{th['name']} 曝險 {w:.1f}%（{th['count']} 檔）已超過 {THEME_ALERT_PCT:.0f}% 紅線"
                             f"——這是單一因子風險，不是分散投資：{', '.join(th['tickers'])}"})
        elif w >= THEME_WARN_PCT:
            warnings.append({"level": "medium", "text":
                             f"{th['name']} 曝險 {w:.1f}%（{th['count']} 檔）已過 {THEME_WARN_PCT:.0f}% 警戒線："
                             f"{', '.join(th['tickers'])}"})
    for ov in concentration["over_limit"]:
        warnings.append({"level": "high", "text":
                         f"{ov['ticker']} 單一持倉 {ov['weight_pct']:.1f}% > {SINGLE_NAME_LIMIT_PCT:.0f}% 上限，必須 trim"})

    multi_lot = [
        {"ticker": r["ticker"], "lots": r["lots"], "shares": r["shares"],
         "market_value": r["market_value"], "weight_pct": r["weight_pct"]}
        for r in positions.values() if r["multi_lot"]
    ]
    leveraged = [
        {"ticker": r["ticker"], "leverage": r["max_leverage"], "market_value": r["market_value"]}
        for r in positions.values() if (r["max_leverage"] or 1) > 1
    ]
    twd_positions = [r for r in positions.values() if r["ticker"].endswith(".TW")]
    twd_value = round(sum(r["market_value"] for r in twd_positions), 2)
    # 匯率抓不到時 portfolio API 會讓台股維持原幣 TWD，於是台股市值被當成美金、
    # 權重放大三十幾倍（實測：0050.TW 從 6.9% 變 34.6%，全組合停損風險從 7% 變 85%）。
    # 這種情況下所有權重與曝險都不可信，必須整份標記，不能只是默默算下去。
    fx_unconverted = bool(twd_positions) and not merged["usd_twd_rate"]
    if fx_unconverted:
        # 插在最前面：它讓下面每一個百分比都失效，排在後面等於沒講
        warnings.insert(0, {"level": "high", "text":
                         "**USD/TWD 匯率取不到，台股部位仍是原幣 TWD** —— "
                         f"本節所有權重、曝險與停損金額都被高估（受影響：{', '.join(sorted(r['ticker'] for r in twd_positions))}）。"
                         "在匯率恢復前，不得依這些數字做倉位決策。"})

    # 匯率沒折算時，下面所有百分比與金額都是台股被放大三十幾倍的產物。
    # 直接刪掉會讓報告看起來一切正常，所以保留數字但逐條標記不可信。
    if fx_unconverted:
        for w in warnings[1:]:
            w["text"] += "　（⚠️ 受匯率未折算影響，此數字不可信）"

    stop_cluster = compute_stop_cluster(merged, journals or [])
    if fx_unconverted:
        stop_cluster["unreliable"] = True
    earnings_cluster = compute_earnings_cluster(merged, earnings_events or [], analysis_date)
    bellwether = compute_bellwether_impact(merged, earnings_events or [], theme_exposure)

    # 逐檔合併後的精簡表（給三層報告的 hold 合併表與前端用；不含 lots 明細以免臃腫）
    position_summary = {
        t: {
            "ticker": t, "weight_pct": r["weight_pct"], "market_value": r["market_value"],
            "shares": r["shares"], "current_price": r["current_price"],
            "day_change_pct": r["day_change_pct"], "return_pct": r["return_pct"],
            "theme": r["theme"], "industry": r.get("industry"), "multi_lot": r["multi_lot"],
            "portfolios": sorted(r["portfolios"].keys()),
        }
        for t, r in positions.items()
    }

    return {
        "date": analysis_date.isoformat(),
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(),
        "positions": position_summary,
        "currency": ("⚠️ 混幣未折算：台股為原幣 TWD、美股為 USD（匯率取不到）"
                     if fx_unconverted else "USD（台股已按匯率折算；原幣為 TWD）"),
        "fx_unconverted": fx_unconverted,
        "usd_twd_rate": merged["usd_twd_rate"],
        "total_value_usd": total,
        "cash_pct": merged["cash_pct"],
        "twd_exposure": {"value_usd": twd_value,
                         "weight_pct": round(twd_value / total * 100, 2) if total else None,
                         "tickers": sorted(r["ticker"] for r in twd_positions)},
        "concentration": concentration,
        "theme_exposure": theme_exposure,
        "industry_exposure": industry_exposure,
        "multi_lot_positions": multi_lot,
        "leveraged_positions": leveraged,
        "stop_cluster": stop_cluster,
        "earnings_cluster": earnings_cluster,
        "bellwether_earnings": bellwether,
        "warnings": warnings,
    }


# ── 渲染 ──────────────────────────────────────────────────────────────────────

def render_risk_md(data: dict | None, *, compact: bool = True) -> list[str]:
    if not data:
        return []
    rate = data.get("usd_twd_rate")
    lines = [
        "## 🧮 組合層風險（Main＋Mom 合併；單股 25% 規則之外真正該看的）",
        f"> 幣別：{data.get('currency')}"
        + (f"｜USD/TWD {rate}" if rate else "")
        + f"｜組合總值 US${data.get('total_value_usd', 0):,.0f}｜現金 {data.get('cash_pct', 0):.1f}%",
        "",
    ]

    themes = [t for t in (data.get("theme_exposure") or []) if (t.get("weight_pct") or 0) >= 1]
    if themes:
        lines.append("**主題曝險（合併後）**")
        for t in themes[:8]:
            bar = "█" * max(1, int((t["weight_pct"] or 0) / 4))
            lines.append(f"- {t['name']}：{t['weight_pct']:.1f}%（{t['count']} 檔）{bar}"
                         + (f"　{', '.join(t['tickers'][:10])}" if not compact else ""))
        lines.append("")

    conc = data.get("concentration") or {}
    if conc:
        lines.append(f"**集中度**：Top1 {conc.get('top1_ticker')} {conc.get('top1_pct')}%"
                     f"｜Top3 {conc.get('top3_pct')}%｜Top5 {conc.get('top5_pct')}%"
                     f"｜共 {conc.get('position_count')} 檔")

    ml = data.get("multi_lot_positions") or []
    if ml:
        for r in ml:
            lots = "＋".join(f"{l['portfolio']}/{l['account']} {l['shares']}股" for l in r["lots"])
            lines.append(f"**多批持倉合併**：{r['ticker']} 共 {r['shares']}股"
                         f"（{lots}）＝ US${r['market_value']:,.0f}／{r['weight_pct']}%"
                         "　← 逐帳戶看會低估這檔的真實權重")

    lev = data.get("leveraged_positions") or []
    if lev:
        lines.append("**槓桿部位**：" + "｜".join(
            f"{r['ticker']} {r['leverage']}x（US${r['market_value']:,.0f}）" for r in lev))

    twd = data.get("twd_exposure") or {}
    if twd.get("tickers") and data.get("fx_unconverted"):
        lines.append(f"**台股曝險**：{twd['weight_pct']}% ⚠️ **數字不可信**"
                     "（匯率取不到，台股仍是原幣 TWD，權重被高估約 30 倍）"
                     f"｜{', '.join(twd['tickers'])}")
    elif twd.get("tickers"):
        lines.append(f"**台股曝險**：{twd['weight_pct']}%（US${twd['value_usd']:,.0f}）"
                     f"｜{', '.join(twd['tickers'])}"
                     "　← 這幾檔報表上的 $ 是**折算後 USD**，原幣為 TWD，別跟美股價位混著讀")
    lines.append("")

    sc = data.get("stop_cluster") or {}
    if sc.get("rows"):
        lines.append("**停損同時觸發的預估損失**")
        unrel = "　（⚠️ 匯率未折算，金額不可信）" if sc.get("unreliable") else ""
        lines.append(f"- 全部停損一起打到：約 **US${sc['total_downside_usd']:,.0f}"
                     f"（佔組合 {sc['total_downside_pct_of_portfolio']:.1f}%）**"
                     f"；已設停損的部位覆蓋 {sc['covered_pct']:.0f}% 組合市值{unrel}")
        worst = sorted(sc["rows"], key=lambda r: -(r["downside_usd"] or 0))[:5]
        lines.append("- 最大單筆風險：" + "｜".join(
            f"{r['ticker']} −US${r['downside_usd']:,.0f}（{r['downside_pct']:+.1f}%）" for r in worst))
        below = [r["ticker"] for r in sc["rows"] if r.get("already_below")]
        if below:
            lines.append(f"- 🔴 **現價已在停損價之下**：{', '.join(below)} —— 依規則早該處理")
        if sc.get("uncovered_tickers"):
            lines.append(f"- ⚠️ 尚無停損價的持倉：{', '.join(sc['uncovered_tickers'][:14])}")
        lines.append("")

    ec = data.get("earnings_cluster") or []
    if ec:
        lines.append(f"**財報群聚（未來 {EARNINGS_CLUSTER_DAYS} 天，對組合的同時衝擊）**")
        for slot in ec[:6]:
            lines.append(f"- {slot['date']}（{slot['days_until']} 天後）："
                         f"{', '.join(slot['tickers'])} → 同時影響組合 **{slot['weight_pct']:.1f}%**")
        lines.append("")

    bw = data.get("bellwether_earnings") or []
    if bw:
        for b in bw[:4]:
            held = "（持倉）" if b["held"] else "（未持有，但外溢）"
            lines.append(f"- 🌊 {b['ticker']} {b['date']} 財報{held}：同主題「{b['theme']}」"
                         f"佔組合 **{b['theme_weight_pct']:.1f}%**"
                         f"（{', '.join(b['theme_tickers'][:10])}）——單檔財報＝整條線的風險事件")
        lines.append("")

    warns = data.get("warnings") or []
    if warns:
        lines.append("**⚠️ 組合層警告**")
        for w in warns:
            icon = "🔴" if w["level"] == "high" else "🟡"
            lines.append(f"- {icon} {w['text']}")
        lines.append("")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description="組合層風險 — 主題曝險／停損群聚／財報群聚")
    ap.add_argument("--date", help="分析日期 YYYY-MM-DD")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    args = ap.parse_args()
    d = date.fromisoformat(args.date) if args.date else date.today()

    from backend.app.database import SessionLocal
    from scripts.daily_analysis import load_portfolio
    from scripts.decision_ledger import load_journals
    from datetime import timedelta

    db = SessionLocal()
    try:
        payload = load_portfolio(db)
    finally:
        db.close()

    events = []
    try:
        import requests
        r = requests.get("http://127.0.0.1:6000/api/earnings/calendar", timeout=60)
        events = r.json().get("events", [])
    except Exception:
        pass

    data = compute_portfolio_risk(
        payload, journals=load_journals(d - timedelta(days=10), d),
        earnings_events=events, analysis_date=d,
    )
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("\n".join(render_risk_md(data, compact=False)))


if __name__ == "__main__":
    main()
