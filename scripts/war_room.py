#!/usr/bin/env python3
"""
戰情中心（Phase 2）— 讓 claude -p 自主蒐集情報後給決策建議。

與 daily_analysis.py 的差異：
  - daily_analysis.py：Python 端組一個 ~39k chars 的巨型 prompt，把所有技術面/
    Mimi/歷史績效全塞進去，claude 只是「讀完整資料 → 寫報告」。
  - war_room.py（本檔）：Python 端只組一份 <8k chars 的「今日簡報」（大盤快照、
    持倉一行表、風險旗標、昨日計分卡、Mimi 近進場區），claude 拿到 agent/CLAUDE.md
    的 SOP 後，**自己用 curl / WebSearch 深挖值得關注的標的**，再輸出六區塊決策。

輸出管線（Telegram / LINE / Obsidian / journal / 昨日計分）全部復用 daily_analysis.py，
只在 Obsidian note 開頭標註「由戰情中心（war_room）產出」以區別舊管線。

用法：
    uv run python scripts/war_room.py                       # 今日，正常跑
    uv run python scripts/war_room.py --dry-run             # 只印簡報，不跑 claude
    uv run python scripts/war_room.py --date 2026-07-10
    uv run python scripts/war_room.py --model opus --max-turns 40
    uv run python scripts/war_room.py --no-telegram --no-obsidian
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

# 復用 Phase 1 的資料函式與輸出管線（同 package，直接 import）
from scripts.daily_analysis import (
    MACRO_LABELS,
    MACRO_SYMBOLS,
    SECTOR_ETFS,
    extract_decisions,
    extract_mimi_entry_zone,
    extract_mimi_position_size,
    extract_mimi_stop_loss,
    fetch_quotes,
    fetch_risk_flags,
    load_portfolio,
    parse_mimi_doc,
    score_yesterday,
    send_line,
    send_telegram,
    write_journal,
    _is_near_entry_zone,
)
from scripts.decision_ledger import compute_ledger, render_ledger_md, save_ledger_json, load_journals
from scripts.exec_state import (
    active_entries as exec_active_entries,
    load_state as load_exec_state,
    render_exec_section,
    snapshot_positions,
)
from scripts.portfolio_risk import compute_portfolio_risk, render_risk_md
from scripts.report_meta import (
    briefing_tense_instruction,
    build_freshness,
    estimate_trade_date,
    format_price,
    render_freshness_block,
    resolve_trade_date,
)
from scripts.report_guard import apply_guards, render_audit_md, render_audit_summary
from scripts.zh_terms import localize, zh_action
from scripts.war_room_report import assemble_report, build_morning_brief, build_telegram_summary
from scripts.daily_manifest import build_manifest, render_manifest_md, save_manifest
from backend.app.database import SessionLocal
# 財經 digest（每日 Substack 投資筆記）— 直接 import 讀檔，不打自己的 HTTP
from backend.app.routers.finance_digest import load_recent_items
# 我自己在 Obsidian 寫的投資筆記（觀點層）— 讀鏡像索引，讀不到就 degrade 成空
from src.analysis.vault_notes import (
    build_thesis_section,
    macro_notes,
    refresh as vault_refresh,
)

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
AGENT_DIR = PROJECT_ROOT / "agent"           # claude 的工作目錄（含 CLAUDE.md）
BACKEND_BASE = "http://127.0.0.1:6000"
OPP_DIR = PROJECT_ROOT / "data" / "agent" / "opportunities"
# launchd 服務寫不進 ~/Documents（TCC），完整復盤永遠先落地鏡像目錄；
# portal 市場日報分頁直接讀這裡（stock_digest router）。
MIRROR_DIR = Path.home() / ".arthur-digest" / "stock"


def _clean_mimi_field(text: str) -> str:
    """extract_mimi_* 有時會抓到 Mimi 文件裡的 markdown 表頭（如「目標 | 停損 | 關鍵催化劑」）。

    一行式簡報裡這種殘留很難看，這裡把明顯的表頭殘留清成「見策略文件」，
    避免把雜訊當進場區餵給 claude（claude 仍會自己 curl /api/mimi 拿乾淨資料）。
    """
    if not text or text == "—":
        return "—"
    # 含 markdown 分欄符號（表格殘留），或整段就是單一 Mimi 欄位名 → 判定為表頭殘留
    keywords = ("進場", "目標", "停損", "催化劑", "倉位")
    stripped = text.strip().strip("|").strip()
    if "|" in text:
        return "見策略文件"
    if stripped in keywords or (len(stripped) <= 6 and any(k == stripped for k in keywords)):
        return "見策略文件"
    return stripped


# ── 財經 digest（質化脈絡）──────────────────────────────────────────────────────

DIGEST_DAYS = 14              # 回溯天數
DIGEST_MAX_CHARS = 1200       # 整個 digest 區塊硬上限（簡報總長仍要 <8k）
DIGEST_NARRATIVE_DAYS = 5     # 大盤敘事只取最近幾天


def _one_line_tldr(tldr: str, limit: int = 100) -> str:
    """把 markdown 條列 TLDR 壓成一行短句（給簡報一行式用）。"""
    text = re.sub(r"[*#\-`>]+", " ", tldr or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def build_digest_section(held_tickers: set[str]) -> list[str]:
    """近 N 天財經 digest 的精簡脈絡：與持倉相關的文章 + 大盤敘事標題。

    防禦式：讀檔失敗 / 無資料 → 回一行「近 N 天無新 digest」，絕不 raise。
    整段長度硬控在 DIGEST_MAX_CHARS 以內。
    """
    try:
        items = load_recent_items(DIGEST_DAYS)
    except Exception:
        items = []

    lines: list[str] = ["## 📰 近期財經 digest（質化脈絡；不可取代技術面/估值判斷）"]
    if not items:
        lines.append(f"- （近 {DIGEST_DAYS} 天無新 digest）")
        lines.append("")
        return lines

    budget = DIGEST_MAX_CHARS - len(lines[0])

    def _emit(s: str) -> bool:
        nonlocal budget
        if len(s) + 1 > budget:
            return False
        lines.append(s)
        budget -= len(s) + 1
        return True

    related = [it for it in items if held_tickers.intersection(it["tickers"])]
    _emit(f"> 近 {DIGEST_DAYS} 天共 {len(items)} 篇；其中 {len(related)} 篇提及你的持倉。"
          f"想看全文：curl `/api/finance-digest/by-ticker?tickers=...&days=21`")

    if related:
        _emit("### 與持倉相關")
        for it in related:
            hits = ", ".join(sorted(held_tickers.intersection(it["tickers"])))
            row = (f"- [{it['date']}] {it['title']}（{it['sector']}）｜相關持倉: {hits}"
                   f"｜{_one_line_tldr(it['tldr'])}")
            if not _emit(row):
                break
    else:
        _emit("### 與持倉相關")
        _emit("- （近期 digest 未提及任何持倉標的——不代表持倉沒事，只是沒被寫到）")

    # 大盤敘事：近幾日的其他主題（與持倉相關的已列在上面，不重複）
    narrative_dates = set(sorted({it["date"] for it in items}, reverse=True)[:DIGEST_NARRATIVE_DAYS])
    related_ids = {(it["date"], it["title"]) for it in related}
    narrative = [
        it for it in items
        if it["date"] in narrative_dates and (it["date"], it["title"]) not in related_ids
    ]
    if narrative:
        _emit("### 大盤敘事（近幾日其他主題）")
        for it in narrative:
            tk = ",".join(it["tickers"][:4]) or "—"
            if not _emit(f"- [{it['date']}] {it['title']}（{it['sector']}｜{tk}）"):
                break
    lines.append("")
    return lines


# ── 財報日曆（唯一可信的財報日來源）────────────────────────────────────────────

EARNINGS_WINDOW_DAYS = 45     # 簡報只列這個窗口內的財報，再遠的對今天的決策沒意義
EARNINGS_MAX_ROWS = 20        # 硬上限，避免觀察名單一大就把簡報撐爆
EARNINGS_REPORTED_ROWS = 12   # 已公布的只是「別再當催化劑」的提醒，列少一點省簡報長度

_WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]
_SESSION_ZH = {"BMO": "盤前", "AMC": "盤後"}


def fetch_earnings_events() -> tuple[list[dict], str | None]:
    """財報日曆抓一次就好——簡報、組合風險（財報群聚）、三層報告都吃同一份。

    回 (events, error_name)；抓不到時 events 為空、error_name 帶例外型別名。
    """
    try:
        # 未快取時 yfinance 要抓兩百多檔，第一次會慢
        r = requests.get(f"{BACKEND_BASE}/api/earnings/calendar", timeout=120)
        r.raise_for_status()
        return r.json().get("events", []), None
    except Exception as e:
        return [], type(e).__name__


def build_earnings_section(tickers: set[str], events: list[dict],
                           error: str | None = None) -> list[str]:
    """持倉＋觀察名單的財報日，直接餵進簡報。

    財報日是 claude 最容易講錯的一格：它從新聞標題（「Q4 財報前…」）反推日期，
    或是 curl 回來的舊資料照抄，於是 8/23 的覆盤還在說「VIAV Q4 財報即將公布」——
    那份財報 8/5 就出了。把 backend 算好的日期直接寫進簡報，並在下面的任務指令
    釘死「只能引用這張表」，agent 就沒有自由發揮的空間。
    """
    if not tickers:
        return []
    if error:
        return [
            "## 📅 財報日曆",
            f"> ⚠️ 取不到 /api/earnings/calendar（{error}）。",
            "> 財報日請自行 curl 該端點確認；**查不到就不要寫任何財報日期或「財報將至」**。",
            "",
        ]

    def _in_window(e) -> bool:
        d = e.get("days_until")          # 0 = 今天財報，最重要的一列，別用 `or` 判斷
        return d is not None and 0 <= d <= EARNINGS_WINDOW_DAYS

    mine = [e for e in events if str(e.get("ticker", "")).upper() in tickers]
    upcoming = sorted(
        [e for e in mine if e.get("status") == "upcoming" and _in_window(e)],
        key=lambda e: e["date"],
    )[:EARNINGS_MAX_ROWS]
    reported = sorted(
        [e for e in mine if e.get("status") == "reported"],
        key=lambda e: e["date"], reverse=True,
    )[:EARNINGS_REPORTED_ROWS]

    lines = [f"## 📅 財報日曆（未來 {EARNINGS_WINDOW_DAYS} 天，美東日期）"]
    lines.append("> 來源：backend `/api/earnings/calendar`（Yahoo 官方排程轉美東時間，"
                 "已公布日以 EDGAR 8-K 為準）。**這是唯一可信的財報日**。")
    lines.append(f"> 沒出現在下表的 ticker＝{EARNINGS_WINDOW_DAYS} 天內沒有財報，"
                 "不准寫「財報將至／財報催化窗口逼近」。")
    lines.append("")

    if upcoming:
        for e in upcoming:
            d = date.fromisoformat(e["date"])
            when = "今天" if e["days_until"] == 0 else ("明天" if e["days_until"] == 1
                                                      else f"{e['days_until']} 天後")
            session = _SESSION_ZH.get(e.get("time_of_day") or "", "時段未定")
            eps = f"｜EPS est {e['eps_estimate']}" if e.get("eps_estimate") is not None else ""
            warn = "｜⚠️ 日期未確認" if e.get("date_source") == "calendar_estimate" else ""
            lines.append(f"- {e['ticker']}｜{e['date']}（{_WEEKDAY_ZH[d.weekday()]}）{session}"
                         f"｜{when}{eps}{warn}")
    else:
        lines.append(f"- （持倉與觀察名單在未來 {EARNINGS_WINDOW_DAYS} 天內都沒有財報）")

    if reported:
        bits = [f"{e['ticker']} {e['date'][5:]}" for e in reported]
        lines.append("")
        lines.append("> 這幾檔**財報已經公布**（EDGAR 8-K 確認；不是完整清單），"
                     "結果已經反映在價格裡，不准再寫成「即將到來的催化劑」： " + "｜".join(bits))

    lines.append("")
    return lines


# ── 機會掃描回顧（把 market_scan 的 top 機會接進每日復盤）─────────────────────────

SCAN_MAX_AGE_DAYS = 7        # 掃描檔超過這個天數就當過期，不進簡報
SCAN_MAX_ROWS = 6


def _load_latest_scan(analysis_date: date) -> tuple[date, dict] | None:
    """最近一份（≤analysis_date、未過期）的機會掃描 journal。"""
    try:
        candidates: list[date] = []
        for p in OPP_DIR.glob("*.json"):
            try:
                d = date.fromisoformat(p.stem)
            except ValueError:
                continue
            if d <= analysis_date and (analysis_date - d).days <= SCAN_MAX_AGE_DAYS:
                candidates.append(d)
        if not candidates:
            return None
        latest = max(candidates)
        data = json.loads((OPP_DIR / f"{latest.isoformat()}.json").read_text(encoding="utf-8"))
        return latest, data
    except Exception:
        return None


def build_scan_followup_section(analysis_date: date) -> list[str]:
    """機會獵手上次盡調過的 top 機會 → 現價 vs 進場區追蹤。

    這是「提醒新機會」的閉環：market_scan 找到的機會若後來跌進進場區，
    復盤這裡就要跳出來提醒，而不是掃完就石沉大海。
    """
    loaded = _load_latest_scan(analysis_date)
    if not loaded:
        return []
    scan_date, data = loaded
    opps = [
        o for o in data.get("opportunities", [])
        if isinstance(o, dict) and o.get("ticker")
        and o.get("action") in ("buy", "accumulate", "watch")
    ]
    opps.sort(key=lambda o: -(o.get("conviction") or 0))
    opps = opps[:SCAN_MAX_ROWS]
    if not opps:
        return []

    quotes = fetch_quotes([str(o["ticker"]).upper() for o in opps])
    lines = [
        f"## 🔭 機會掃描回顧（{scan_date.isoformat()} 機會獵手盡調結果）",
        "> 這些是先前定性盡調過的候選機會。**已進入進場區的，今天必須在 MIMI 區塊評估是否建倉**"
        "（成立→DECISIONS 給 open；論點已失效→明說失效原因），並在 SUMMARY 的 🆕 行提醒。",
        "",
    ]
    for o in opps:
        t = str(o["ticker"]).upper()
        price = quotes.get(t, {}).get("price")
        ez = o.get("entry_zone")
        status = "—"
        if price is not None and isinstance(ez, list) and len(ez) == 2 and all(
            isinstance(x, (int, float)) for x in ez
        ):
            lo, hi = float(ez[0]), float(ez[1])
            if lo <= price <= hi:
                status = "✅ 已進入進場區"
            elif price < lo:
                status = f"⚠️ 已低於下緣 {(lo - price) / lo * 100:.1f}%（檢查論點是否破壞）"
            else:
                status = f"距下緣 {(price - lo) / lo * 100:+.1f}%"
        ez_str = f"${ez[0]}-{ez[1]}" if isinstance(ez, list) and len(ez) == 2 else "—"
        p_str = f"${price:.2f}" if price is not None else "—"
        lines.append(
            f"- {t}｜{zh_action(o.get('action'))}｜信心 {o.get('conviction', '—')}｜"
            f"進場區 {ez_str}｜現價 {p_str}｜{status}｜{str(o.get('thesis') or '')[:60]}"
        )
    lines.append("")
    return lines


# ── 完整復盤鏡像落地（portal 市場日報分頁可讀，繞開 TCC）────────────────────────

def save_mirror_note(analysis_date: date, report_md: str) -> Path | None:
    """三層完整復盤寫進 ~/.arthur-digest/stock/{date}/。

    通知迴圈以 --no-obsidian 觸發時 vault note 會被跳過，過去等於整份深度分析被丟棄、
    只剩 Telegram 300 字摘要——鏡像落地不受旗標影響，永遠執行。
    報告本體（時效標頭／90 秒晨報／異常持倉／附錄）由 war_room_report.assemble_report 組好。
    """
    try:
        day_dir = MIRROR_DIR / analysis_date.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / "🎖️ War Room.md"
        path.write_text(report_md.rstrip() + "\n", encoding="utf-8")
        print(f"[mirror] 完整復盤已落地: {path}（{len(report_md):,} chars）")
        return path
    except Exception as e:
        print(f"[mirror] 鏡像落地失敗（略過）：{e}")
        return None


VAULT_DIR = Path("/Users/yankesswang/Documents/arthurwang_DB/投資/Stock Digest")


def save_vault_note(analysis_date: date, report_md: str, tickers: list[str]) -> Path | None:
    """把同一份三層報告寫進 Obsidian vault（best-effort）。

    不再走 daily_analysis.save_obsidian_note —— 那支是為舊的六區塊格式寫的，
    會用 `<<<MARKER>>>` 切段再重組；三層報告沒有 marker，丟進去只會得到一份空殼。
    檔名用 `🎖️ War Room.md`（與鏡像一致），不覆蓋 Stock Digest 自己的 `📊 Portfolio.md`。
    launchd context 下 ~/Documents 會被 TCC 擋，失敗不影響鏡像副本。
    """
    try:
        day_dir = VAULT_DIR / analysis_date.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / "🎖️ War Room.md"
        frontmatter = (
            "---\n"
            f"title: War Room {analysis_date.isoformat()}\n"
            f"date: {analysis_date.isoformat()}\n"
            "tags:\n  - 投資\n  - 盤後復盤\n  - claude-analysis\n"
            "stocks:\n" + "".join(f"  - {t}\n" for t in sorted(set(tickers)))
            + "---\n\n> 由戰情中心（war_room）產出\n\n"
        )
        path.write_text(frontmatter + report_md.rstrip() + "\n", encoding="utf-8")
        print(f"[obsidian] Saved: {path}")
        return path
    except OSError as e:
        print(f"[obsidian] vault 寫入失敗（TCC？略過，鏡像仍在）：{e}")
        return None
    except Exception as e:
        print(f"[obsidian] vault 寫入失敗（略過）：{e}")
        return None


# ── Backend 健康檢查（不在線只警告，agent 少了 curl 工具仍可靠 WebSearch）────────

def _backend_alive() -> bool:
    try:
        r = requests.get(f"{BACKEND_BASE}/api/health", timeout=4)
        return r.status_code == 200
    except Exception:
        return False


# ── 今日簡報組裝（目標 <8k chars）────────────────────────────────────────────────

def build_briefing(
    analysis_date: date,
    market_data: dict,
    sector_data: dict,
    portfolio_payload: dict,
    mimi_sections: dict,
    risk_flags_by_portfolio: dict,
    scorecard_md: str,
    backend_ok: bool,
    ledger_md: str = "",
    scan_lines: list[str] | None = None,
    freshness: dict | None = None,
    earnings_events: list[dict] | None = None,
    earnings_error: str | None = None,
    exec_lines: list[str] | None = None,
    risk_lines: list[str] | None = None,
    risk: dict | None = None,
) -> str:
    """組一份精簡的作戰簡報，讓 claude 當作深挖的起點（不是最終答案）。"""
    now_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M 台北時間")
    lines: list[str] = []

    def L(s: str = "") -> None:
        lines.append(s)

    L(f"# 🎖️ 今日作戰簡報｜{analysis_date.isoformat()}")
    L(f"> {now_str}")
    if not backend_ok:
        L("> ⚠️ backend :6000 目前離線——curl 工具可能不可用，請改用 WebSearch 補技術面/新聞。")
    L()

    # ── 時效標頭（最上面，因為它會改變整份報告的時態）─────────────────────────
    if freshness:
        L(render_freshness_block(freshness))
        L()
        for line in briefing_tense_instruction(freshness):
            L(line)

    # ── 大盤快照（一行式）─────────────────────────────────────────────────────
    L("## 📈 大盤快照")
    idx_bits: list[str] = []
    for sym in ["SPY", "QQQ", "IWM", "DIA", "^VIX"]:
        d = market_data.get(sym, {})
        label = MACRO_LABELS.get(sym, sym).split("（")[0]
        pct = f"{d['change_pct']:+.2f}%" if "change_pct" in d else "—"
        price = d.get("price", "—")
        idx_bits.append(f"{label} {price}（{pct}）")
    L("指數： " + "｜".join(idx_bits))

    macro_bits: list[str] = []
    for sym in ["TLT", "UUP", "GLD"]:
        d = market_data.get(sym, {})
        label = MACRO_LABELS.get(sym, sym).split("（")[0]
        pct = f"{d['change_pct']:+.2f}%" if "change_pct" in d else "—"
        macro_bits.append(f"{label} {pct}")
    L("宏觀： " + "｜".join(macro_bits))

    # 板塊：領漲 / 領跌各挑，一行式
    sector_sorted = sorted(
        SECTOR_ETFS.items(),
        key=lambda x: sector_data.get(x[0], {}).get("change_pct", 0),
        reverse=True,
    )
    sec_bits: list[str] = []
    for sym, name in sector_sorted:
        d = sector_data.get(sym, {})
        if "change_pct" not in d:
            continue
        icon = "🟢" if d["change_pct"] >= 0 else "🔴"
        sec_bits.append(f"{icon}{name} {d['change_pct']:+.1f}%")
    L("板塊（強→弱）： " + "｜".join(sec_bits))
    L()

    # ── 持倉精簡表（每檔一行）──────────────────────────────────────────────────
    portfolios = portfolio_payload.get("portfolios", {}) if isinstance(portfolio_payload, dict) else {}
    portfolio_names = portfolio_payload.get("portfolio_names", []) if isinstance(portfolio_payload, dict) else []

    L("## 💼 持倉精簡表")
    L("> 欄位：ticker｜組合內權重（Main＋Mom 合併權重）｜今日%｜累計%｜現價｜Mimi進場區/停損")
    L("> 價格一律標 **US$**；台股（.TW）另附原幣 NT$。同一 ticker 的多批持倉已先合併，")
    L("> 逐帳戶看會低估真實權重（例：Mom 的 TSLA 在兩個帳戶各一筆）。")
    L()
    combined = ((risk or {}).get("positions") or {})
    usd_twd = (risk or {}).get("usd_twd_rate")
    for pname in portfolio_names:
        pdata = portfolios.get(pname, {})
        summary = pdata.get("summary", {})
        total_val = float(summary.get("total_value") or 0.0)

        # 同 ticker 跨帳戶先合併，再算組合內權重
        # （get_csv_portfolio 的 holdings 是「一帳戶一列」且不含 weight_pct）
        rows: dict[str, dict] = {}
        for h in pdata.get("holdings", []):
            t = h.get("ticker")
            if t in ("CASH", None, ""):
                continue
            r = rows.setdefault(t, {"ticker": t, "market_value": 0.0, "cost_basis": 0.0,
                                    "current_price": h.get("current_price"),
                                    "day_change_pct": h.get("day_change_pct"), "lots": 0})
            r["market_value"] += float(h.get("market_value") or 0.0)
            r["cost_basis"] += float(h.get("cost_basis") or 0.0)
            r["lots"] += 1
        ordered = sorted(rows.values(), key=lambda r: -r["market_value"])

        net_ret = summary.get("net_return_pct", 0) or 0
        cash_pct = summary.get("cash_pct", 0) or 0
        L(f"### 【{pname}】累計 {net_ret:+.2f}%｜現金 {cash_pct:.1f}%｜總值 US${total_val:,.0f}")
        for r in ordered:
            t = r["ticker"]
            price = r["current_price"]
            day_pct = r["day_change_pct"]
            ret_pct = ((r["market_value"] - r["cost_basis"]) / r["cost_basis"] * 100
                       if r["cost_basis"] else None)
            weight = r["market_value"] / total_val * 100 if total_val else None
            comb = (combined.get(t) or {}).get("weight_pct")

            mimi_section = mimi_sections.get(t, "")
            if mimi_section:
                # Mimi 欄位常是整段敘述，簡報一行式只留前 34 字（agent 要細節會自己 curl /api/mimi）
                entry = _clean_mimi_field(extract_mimi_entry_zone(mimi_section))[:34]
                stop = _clean_mimi_field(extract_mimi_stop_loss(mimi_section))[:24]
                mimi_bit = f"Mimi進場 {entry}｜停損 {stop}"
            else:
                mimi_bit = "（不在 Mimi 表）"

            w_str = f"{weight:.1f}%" if weight is not None else "—"
            if comb is not None and abs((comb or 0) - (weight or 0)) > 0.05:
                w_str += f"（合併 {comb:.1f}%）"
            if r["lots"] > 1:
                w_str += f"　⚠️{r['lots']}批"
            over = "　🔴超過25%上限" if (weight or 0) > 25 else ""
            p_str = format_price(t, price, usd_twd)
            d_str = f"{day_pct:+.2f}%" if day_pct is not None else "—"
            r_str = f"{ret_pct:+.2f}%" if ret_pct is not None else "—"
            L(f"- {t}｜{pname}｜{w_str}{over}｜今日{d_str}｜累計{r_str}｜{p_str}｜{mimi_bit}")
        L()

    # ── 財報日曆（持倉＋Mimi 觀察名單）──────────────────────────────────────────
    all_held = {
        h.get("ticker", "")
        for pdata in portfolios.values()
        for h in pdata.get("holdings", [])
        if h.get("ticker") not in ("", "CASH", None)
    }
    for line in build_earnings_section({t for t in all_held if t} | set(mimi_sections),
                                       earnings_events or [], earnings_error):
        L(line)

    # ── 組合層風險（主題曝險／停損群聚／財報群聚）────────────────────────────────
    for line in (risk_lines or []):
        L(line)

    # ── 風險旗標 ───────────────────────────────────────────────────────────────
    any_flag = any(risk_flags_by_portfolio.get(p) for p in portfolio_names)
    if any_flag:
        L("## ⚠️ 風險旗標（backend 技術訊號）")
        for pname in portfolio_names:
            flags = risk_flags_by_portfolio.get(pname, [])
            if not flags:
                continue
            L(f"**{pname}**：")
            for f in flags[:8]:
                lvl = f.get("level", "info")
                emoji = {"high": "🔴", "medium": "🟡", "bear": "🔴", "bull": "🟢", "info": "ℹ️"}.get(lvl, "•")
                L(f"- {emoji} {f.get('message', '')}")
        L()

    # ── 昨日決策計分卡 ─────────────────────────────────────────────────────────
    if scorecard_md:
        L(scorecard_md.strip())
        L()
        L("> ☝️ 這是你上次的論點與觸發結果。今天的 DECISIONS 要延續或明確修正上面論點，不可失憶重來。")
        L()

    # ── 決策執行狀態（triggered ≠ executed 的唯一依據）──────────────────────────
    for line in (exec_lines or []):
        L(line)

    # ── 決策帳本（未結追蹤 + 決策績效）───────────────────────────────────────────
    if ledger_md:
        L(ledger_md.strip())
        L()

    # ── Mimi 近進場區（一行式）──────────────────────────────────────────────────
    watchlist = {t: s for t, s in mimi_sections.items() if t not in all_held}
    if watchlist:
        wl_quotes = fetch_quotes(list(watchlist.keys()))
        near_rows: list[tuple] = []
        for t, section in watchlist.items():
            q = wl_quotes.get(t, {})
            price = q.get("price")
            chg = q.get("change_pct")
            entry = extract_mimi_entry_zone(section)
            if _is_near_entry_zone(price, entry):
                near_rows.append((t, price, chg, _clean_mimi_field(entry),
                                  _clean_mimi_field(extract_mimi_position_size(section)),
                                  _clean_mimi_field(extract_mimi_stop_loss(section))))
        near_rows.sort(key=lambda x: abs(x[2] or 0), reverse=True)

        L("## 👁️ Mimi 觀察名單：接近進場區")
        if near_rows:
            for t, price, chg, entry, size, stop in near_rows:
                p_str = f"${price:.2f}" if price else "—"
                c_str = f"{chg:+.2f}%" if chg is not None else "—"
                L(f"- {t}：{p_str}（{c_str}）｜進場區 {entry}｜建議倉位 {size}｜停損 {stop}")
        else:
            L("- （目前無標的接近 Mimi 進場區）")
        L()

    # ── 機會掃描回顧（market_scan top 機會 vs 現價）─────────────────────────────
    for line in (scan_lines or []):
        L(line)

    # ── 近期財經 digest（質化脈絡）──────────────────────────────────────────────
    for line in build_digest_section({t for t in all_held if t}):
        L(line)

    # ── 我自己的筆記（觀點層）───────────────────────────────────────────────────
    # digest 是別人寫的；這一段是我寫的。當初買進的理由與停損位寫在筆記裡，
    # 戰情官給「續抱／減碼」之前應該先對照原始論點還成不成立。
    for line in build_thesis_section(
        {t for t in all_held if t},
        max_chars=1200,          # 這份簡報本來就吃緊，觀點層不該再吃掉一大塊
        header="## 🧠 我的觀點庫（我自己的 Obsidian 投資筆記；買進理由的原始出處）",
        empty_hint="- （近 90 天我的筆記沒寫到任何持倉——持倉的原始論點目前無據可查）",
    ):
        L(line)
    macro = macro_notes()
    if macro:
        L("**我近期的大盤／策略筆記**（我對 regime 的看法，拿來跟今天的數據對照）")
        for n in macro:
            L(f"- [{n['date']}] {n['title']}｜{n['thesis'][:100]}")
        L()

    # ── 任務指令 ───────────────────────────────────────────────────────────────
    L("---")
    L("## 🎯 你的任務")
    L("你是投資戰情官。以上只是**起點簡報**，不是最終答案。")
    L("請依 `agent/CLAUDE.md` 的資訊蒐集 SOP：")
    L("1. 從上表挑出 3-6 檔「值得深挖」的標的（大漲大跌／接近觸發價／有財報催化）。")
    L("2. 對深挖標的用 `curl -s http://127.0.0.1:6000/api/...` 查技術面、新聞、估值。")
    L("   **財報日一律以上面「📅 財報日曆」為準**：不在表上就是近期沒有財報，"
      "不准從新聞標題（「Q4 財報前…」）反推日期，也不准說「財報即將公布」。")
    L("3. 用 WebSearch 查當日重大市場新聞與各標的催化劑。")
    L("4. curl macro/market endpoints 確認大盤 regime。")
    L("5. 深挖標的若出現在上方「近期財經 digest」，先 curl")
    L("   `\"http://127.0.0.1:6000/api/finance-digest/by-ticker?tickers=X,Y&days=21\"` 讀 TLDR，")
    L("   把新聞/敘事脈絡納入 thesis（引用時註明來源日期）。digest 是質化補充，非技術面替代品。")
    L("6. **逐筆回應「🔐 決策執行狀態」與「🧾 未結決策追蹤」裡已觸發的項目**："
      "在該持倉的 DECISIONS 加 `ack_prev`（verdict = executed / abandoned / pending + note），")
    L("   不可裝沒看到。⚠️ **除非狀態表寫 executed / reconciled，否則一律寫「條件已觸發，待確認」**——")
    L("   價格觸發不等於成交，程式會掃描並改寫任何沒有對帳背書的「已執行」。")
    L("7. **檢查「🔭 機會掃描回顧」**：已進入進場區或跌破下緣的機會要重新驗證論點，")
    L("   仍成立的在 MIMI 區塊給建倉評估（DECISIONS 加 `action=open`），並寫進 SUMMARY 的 🆕 行。")
    L("8. **對照「🧠 我的觀點庫」**：我寫過的持倉，深挖後要明講原始論點**還成不成立**——")
    L("   當時的停損位還適用嗎、當時列的催化劑兌現了沒、當時的疑慮解除了沒。論點被推翻")
    L("   卻還在持倉，是要優先講的事；我沒寫過的持倉就說沒有原始論點可對照，不要編。")
    L("9. **組合層風險**（「🧮 組合層風險」區塊）要在 SHARED 收尾處明確回應一次："
      "主題曝險是否過度集中、停損群聚的總風險金額、指標股財報會牽動多少組合。")
    L("   逐檔都健康不代表組合健康——這是單股 25% 規則看不到的風險。")
    L("完成後**輸出六區塊**：`<<<SUMMARY>>> <<<SHARED>>> <<<MAIN>>> <<<MOM>>> <<<MIMI>>> <<<DECISIONS>>>`")
    L("（格式與 DECISIONS JSON schema 詳見 agent/CLAUDE.md 的「輸出契約」。每檔持倉必須逐一點評。）")
    L("⚠️ **正文寫中文術語**：`action` / `risk_level` / `market_regime` 的英文值只填在 JSON 裡，"
      "句子裡一律寫 減碼／加倉／建倉／出場／停損／維持／風險等級高／避險，不要寫 trim、"
      "stop_loss、risk_level=high、risk-off。")
    L("⚠️ 報告的第一層「90 秒晨報」由程式從你的 DECISIONS 自動組裝——"
      "**DECISIONS 的 action / trigger / stop / risk_level / ack_prev 必須與文字區塊一致**，"
      "填錯會直接反映在使用者看到的第一頁。")

    return "\n".join(lines)


# ── Claude runner（自主 agent 模式）──────────────────────────────────────────────

def run_war_room_claude(briefing: str, model: str, max_turns: int) -> tuple[str, str | None]:
    """呼叫 claude -p，讓它在 agent/ 目錄下自主蒐集情報。

    回傳 (result_text, session_id)。全程防禦式：逾時 / 非零退出 / JSON 解析失敗
    都回傳 fallback 訊息字串（不拋 traceback），session_id 取不到則為 None。
    """
    cmd = [
        "claude", "-p", briefing,
        # 空白分隔多個工具名放在同一個 --allowed-tools（見 claude --help）
        "--allowed-tools", "Bash(curl http://127.0.0.1:6000/*) WebSearch WebFetch",
        "--max-turns", str(max_turns),
        "--model", model,
        "--output-format", "json",
    ]
    print(f"[claude] 啟動戰情官（model={model}, max-turns={max_turns}, 簡報 {len(briefing):,} chars）...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,          # 30 分鐘上限
            cwd=str(AGENT_DIR),
        )
    except subprocess.TimeoutExpired:
        return "戰情官分析超時（>30 分鐘），本次略過。請稍後重試或降低 --max-turns。", None
    except FileNotFoundError:
        return "找不到 `claude` 命令，請確認 Claude Code CLI 已安裝並在 PATH 中。", None
    except Exception as e:
        return f"戰情官執行例外：{e}", None

    if result.returncode != 0:
        stderr = (result.stderr or "")[:500]
        print(f"[claude] 非零退出（{result.returncode}）：{stderr}")
        # 仍嘗試解析 stdout（有時 JSON 已產出但退出碼非零）

    stdout = (result.stdout or "").strip()
    if not stdout:
        return f"戰情官無輸出（exit {result.returncode}）：{(result.stderr or '')[:200]}", None

    # --output-format json：stdout 是單一 JSON 物件，取 result 與 session_id
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        print("[claude] stdout 非合法 JSON，退回原始文字（無 session_id）。")
        return stdout, None

    text = payload.get("result")
    session_id = payload.get("session_id")
    if not text:
        # 有 JSON 但沒 result 欄位 → 可能是錯誤物件
        err = payload.get("error") or payload.get("subtype") or "unknown"
        return f"戰情官回應無 result 欄位（{err}），本次略過。", session_id
    return text, session_id


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="戰情中心 — claude 自主蒐集情報後給決策")
    parser.add_argument("--date", help="分析日期 YYYY-MM-DD（預設今天）")
    parser.add_argument("--dry-run", action="store_true", help="只印簡報，不跑 claude")
    parser.add_argument("--no-telegram", action="store_true", help="跳過 Telegram / LINE 發送")
    parser.add_argument("--no-obsidian", action="store_true", help="跳過 Obsidian 存檔")
    parser.add_argument("--model", default="sonnet", help="claude 模型（預設 sonnet，可 opus）")
    parser.add_argument("--max-turns", type=int, default=30, help="claude 最大 agent 回合數（預設 30）")
    args = parser.parse_args()

    analysis_date = date.fromisoformat(args.date) if args.date else date.today()
    print(f"\n{'='*60}")
    print(f"  🎖️ War Room: {analysis_date.isoformat()}")
    print(f"{'='*60}\n")

    backend_ok = _backend_alive()
    if not backend_ok:
        print("[warn] backend :6000 離線——agent 將少了 curl 工具，改靠 WebSearch。仍繼續。")

    # 觀點層：把我 Obsidian 的投資筆記鏡像進來 + 重建索引。整條管線第一站，
    # 因為 market_scan 的 universe 擴充也吃這份索引。iCloud 佔位檔在背景行程讀不到
    # （OSError 11），撈不到的留在 pending，等你在終端機跑 sync_vault_notes.py 補。
    vn = vault_refresh()
    print(f"[0/6] 觀點層：筆記 {vn['notes']} 篇｜標的 {vn['tickers']} 檔"
          f"｜新鏡像 {vn['mirror'].get('copied', 0)}｜待補 {vn['pending']}")
    if vn["pending"]:
        print("      ⏳ 有筆記是 iCloud 佔位檔，背景行程讀不到；"
              "在終端機跑 `uv run python scripts/sync_vault_notes.py` 可補齊")

    db = SessionLocal()
    try:
        # Step 1：大盤 + 板塊
        print("[1/6] 抓大盤 + 板塊報價...")
        all_syms = MACRO_SYMBOLS + list(SECTOR_ETFS.keys())
        all_quotes = fetch_quotes(all_syms)
        market_data = {s: all_quotes[s] for s in MACRO_SYMBOLS if s in all_quotes}
        sector_data = {s: all_quotes[s] for s in SECTOR_ETFS if s in all_quotes}
        quotes_ok = (len(all_quotes), len(all_syms))

        # 交易日不用猜：直接取報價最後一根 K 棒的日期，再算產出延遲。
        # yfinance 整批限流時退回推估（會在報告標「推估」，不假裝一樣可信）。
        trade_date = resolve_trade_date(all_quotes)
        trade_date_source = "quotes"
        if trade_date is None:
            trade_date = estimate_trade_date()
            trade_date_source = "estimated"
            print("       ⚠️ 報價無資料日期，改用推估交易日（報告會標「推估」）")
        freshness = build_freshness(report_key=analysis_date, trade_date=trade_date,
                                    trade_date_source=trade_date_source)
        print(f"       交易日 {freshness.get('trade_date')}｜狀態 {freshness['status']}"
              f"（延遲 {freshness.get('lag_hours')} 小時）")
        if freshness.get("warning"):
            print(f"       ⚠️ {freshness['warning']}")

        # Step 2：持倉 + 風險旗標 + 持倉快照（executed 對帳的基準）
        print("[2/6] 載入持倉 + 風險旗標 + 持倉快照...")
        portfolio_payload = load_portfolio(db)
        portfolio_names = (
            portfolio_payload.get("portfolio_names", [])
            if isinstance(portfolio_payload, dict) else []
        )
        risk_flags = fetch_risk_flags(portfolio_names, db)
        snap = snapshot_positions(analysis_date, portfolio_payload)
        print(f"       持倉快照：{len(snap.get('positions', {}))} 檔（供執行對帳差分）")

        # Step 3：財報日曆（抓一次，簡報與組合風險共用）
        print("[3/6] 抓財報日曆...")
        earnings_events, earnings_error = fetch_earnings_events()
        if earnings_error:
            print(f"       ⚠️ 財報日曆取不到（{earnings_error}）")
        else:
            print(f"       財報事件：{len(earnings_events)} 筆")

        # Step 4：Mimi doc + 計分卡 + 決策帳本（含執行狀態同步）+ 掃描回顧
        print("[4/6] 解析 Mimi 策略文件 + 計分卡 + 決策帳本 + 執行狀態 + 掃描回顧...")
        mimi_sections = parse_mimi_doc()
        scorecard_md = score_yesterday(analysis_date)
        if scorecard_md:
            print("       昨日決策計分卡：已生成")
        ledger_data = compute_ledger(analysis_date)
        save_ledger_json(ledger_data)
        ledger_md = render_ledger_md(ledger_data)
        if ledger_md:
            print(f"       決策帳本：{len(ledger_md):,} chars")
        exec_state = (ledger_data or {}).get("exec_state") or load_exec_state()
        exec_entries = exec_active_entries(exec_state, analysis_date)
        exec_lines = render_exec_section(exec_entries)
        if exec_entries:
            print(f"       執行狀態：{len(exec_entries)} 筆可追蹤決策")
        scan_lines = build_scan_followup_section(analysis_date)
        if scan_lines:
            print("       機會掃描回顧：已生成")

        # Step 5：組合層風險
        print("[5/6] 計算組合層風險（主題曝險／停損群聚／財報群聚）...")
        risk = compute_portfolio_risk(
            portfolio_payload,
            journals=load_journals(analysis_date - timedelta(days=10), analysis_date),
            earnings_events=earnings_events,
            analysis_date=analysis_date,
        )
        risk_lines = render_risk_md(risk)
        risk_md = "\n".join(risk_lines)
        for w in (risk.get("warnings") or []):
            print(f"       {'🔴' if w['level'] == 'high' else '🟡'} {w['text'][:80]}")

        # Step 6：組簡報
        print("[6/6] 組裝今日作戰簡報...")
        briefing = build_briefing(
            analysis_date, market_data, sector_data,
            portfolio_payload, mimi_sections, risk_flags,
            scorecard_md, backend_ok,
            ledger_md=ledger_md, scan_lines=scan_lines,
            freshness=freshness,
            earnings_events=earnings_events, earnings_error=earnings_error,
            exec_lines=exec_lines, risk_lines=risk_lines, risk=risk,
        )
        print(f"       簡報：{len(briefing):,} chars")

        if args.dry_run:
            print("\n" + "=" * 25 + " 簡報預覽（dry-run）" + "=" * 25)
            print(briefing)
            print("=" * 70)
            print(f"\n總長度：{len(briefing):,} chars（目標 <16000；含時效標頭、執行狀態、組合風險、決策帳本與掃描回顧）")
            return

        # 呼叫戰情官（自主 agent）
        print("\n[claude] 交給戰情官自主蒐集情報...")
        analysis, session_id = run_war_room_claude(briefing, args.model, args.max_turns)
        print(f"[claude] 回應：{len(analysis):,} chars｜session_id={session_id or '（無）'}")

        date_str = analysis_date.isoformat()

        # 解析 DECISIONS → 寫 journal（附 session_id 供未來雙向續問）
        decisions_payload = extract_decisions(analysis)
        if decisions_payload:
            n = len(decisions_payload.get("decisions", []))
            print(f"[decisions] 解析出 {n} 筆決策，regime={decisions_payload.get('market_regime', '—')}")
            payload = dict(decisions_payload)
            payload["source"] = "war_room"
            if session_id:
                payload["session_id"] = session_id
            write_journal(analysis_date, payload)
        else:
            print("[decisions] 未解析出有效 DECISIONS JSON（略過寫 journal）")
            # 即使沒有 decisions，也把 session_id 落地（供續問）
            if session_id:
                write_journal(analysis_date, {
                    "decisions": [],
                    "market_regime": "—",
                    "source": "war_room",
                    "session_id": session_id,
                })

        # ── 執行狀態再同步一次：把今天 DECISIONS 的 ack_prev 併進狀態機 ──────────
        try:
            from scripts.exec_state import sync_states
            exec_state_final = sync_states(
                analysis_date,
                (ledger_data or {}).get("open_decisions") or [],
                load_journals(analysis_date - timedelta(days=30), analysis_date),
            )
        except Exception as e:
            print(f"[exec] 決策回應同步失敗（沿用先前狀態）：{e}")
            exec_state_final = exec_state

        # ── 產出驗證：沒有對帳背書的「已執行」、事後補報卻用即時語氣、佔位句 ──────
        held_tickers = {
            str(h.get("ticker") or "").upper()
            for pdata in portfolio_payload.get("portfolios", {}).values()
            for h in pdata.get("holdings", [])
            if h.get("ticker") not in ("", "CASH", None)
        }
        guarded, audit = apply_guards(
            analysis, exec_state=exec_state_final, freshness=freshness,
            known_tickers=held_tickers,
        )
        if audit["violations"]:
            print(f"[guard] 攔下 {len(audit['violations'])} 處："
                  + "、".join(f"{k}×{v}" for k, v in audit["counts"].items()))
        audit_md = render_audit_md(audit)
        audit_summary = render_audit_summary(audit)

        # ── 完整性 manifest（在報告落地前先算一版，讓第一層能顯示資料品質）──────
        manifest = build_manifest(analysis_date, freshness=freshness)

        # ── 三層報告組裝 + 鏡像落地（不受 --no-obsidian 影響；portal 讀這裡）─────
        report_md = assemble_report(
            analysis_date=analysis_date, analysis=guarded,
            decisions_payload=decisions_payload, freshness=freshness,
            exec_state=exec_state_final,
            exec_section_md="\n".join(render_exec_section(
                exec_active_entries(exec_state_final, analysis_date))),
            risk=risk, risk_md=risk_md, manifest=manifest,
            earnings_events=earnings_events,
            scorecard_md=scorecard_md, ledger_md=ledger_md,
            audit_md=audit_md, audit_summary=audit_summary, quotes_ok=quotes_ok,
            manifest_md=render_manifest_md(manifest),
        )
        # 術語中文化：DECISIONS 的英文 enum（trim / risk-off / acknowledged…）不該出現在
        # 人看的那一面。JSON、行內程式碼與網址由 localize 自己跳過，journal 不受影響。
        report_md = localize(report_md)
        save_mirror_note(analysis_date, report_md)

        # manifest 重算一次（War Room 剛落地，狀態要反映得出來）
        manifest = build_manifest(analysis_date, freshness=freshness)
        save_manifest(manifest)
        print(f"[manifest] 今日產出：{manifest['overall']}"
              + (f"｜缺 {'、'.join(manifest['missing'])}" if manifest.get("missing") else ""))

        # Telegram / LINE：時效標頭 + 程式組裝的「今天要做／等待條件」+ 模型摘要
        if not args.no_telegram:
            import re
            summary_m = re.search(r"<<<SUMMARY>>>(.*?)(?=<<<[A-Z_]+>>>|\Z)", guarded, re.DOTALL)
            summary_body = summary_m.group(1).strip() if summary_m else guarded[:400].strip()
            morning_lines = build_morning_brief(
                decisions_payload=decisions_payload, risk=risk, exec_state=exec_state_final,
                freshness=freshness, manifest=manifest, earnings_events=earnings_events,
                quotes_ok=quotes_ok,
            )
            telegram_text = build_telegram_summary(
                analysis_date=analysis_date, blocks_summary=summary_body,
                freshness=freshness, morning_lines=morning_lines, manifest=manifest,
            )
            telegram_text = localize(telegram_text)
            print("[telegram/line] 發送第一層晨報 + SUMMARY...")
            send_telegram(telegram_text)
            send_line(telegram_text, important_report=True)

        # Obsidian：直接寫三層報告（vault 版與鏡像版內容一致，避免兩套語氣）
        if not args.no_obsidian:
            save_vault_note(analysis_date, report_md, sorted(held_tickers))

        print("\n✅ War Room 完成！")

    finally:
        db.close()


if __name__ == "__main__":
    main()
