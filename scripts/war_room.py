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
from datetime import date, datetime
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
    save_obsidian_note,
    score_yesterday,
    send_line,
    send_telegram,
    write_journal,
    _is_near_entry_zone,
)
from backend.app.database import SessionLocal
# 財經 digest（每日 Substack 投資筆記）— 直接 import 讀檔，不打自己的 HTTP
from backend.app.routers.finance_digest import load_recent_items

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
AGENT_DIR = PROJECT_ROOT / "agent"           # claude 的工作目錄（含 CLAUDE.md）
BACKEND_BASE = "http://127.0.0.1:6000"


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
    L("> 欄位：ticker｜組合｜權重%｜今日%｜累計%｜現價 vs Mimi進場區/停損")
    L()
    for pname in portfolio_names:
        pdata = portfolios.get(pname, {})
        summary = pdata.get("summary", {})
        holdings = sorted(
            [h for h in pdata.get("holdings", []) if h.get("ticker") not in ("CASH", None, "")],
            key=lambda h: -(h.get("weight_pct") or 0),
        )
        net_ret = summary.get("net_return_pct", 0) or 0
        cash_pct = summary.get("cash_pct", 0) or 0
        L(f"### 【{pname}】累計 {net_ret:+.2f}%｜現金 {cash_pct:.1f}%")
        for h in holdings:
            t = h.get("ticker", "")
            weight = h.get("weight_pct")
            price = h.get("current_price")
            day_pct = h.get("day_change_pct")
            ret_pct = h.get("return_pct")

            mimi_section = mimi_sections.get(t, "")
            if mimi_section:
                entry = _clean_mimi_field(extract_mimi_entry_zone(mimi_section))
                stop = _clean_mimi_field(extract_mimi_stop_loss(mimi_section))
                mimi_bit = f"Mimi進場 {entry}｜停損 {stop}"
            else:
                mimi_bit = "（不在 Mimi 表）"

            w_str = f"{weight:.1f}%" if weight is not None else "—"
            p_str = f"${price:.2f}" if price else "—"
            d_str = f"{day_pct:+.2f}%" if day_pct is not None else "—"
            r_str = f"{ret_pct:+.2f}%" if ret_pct is not None else "—"
            L(f"- {t}｜{pname}｜{w_str}｜今日{d_str}｜累計{r_str}｜{p_str}｜{mimi_bit}")
        L()

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

    # ── Mimi 近進場區（一行式）──────────────────────────────────────────────────
    all_held = {
        h.get("ticker", "")
        for pdata in portfolios.values()
        for h in pdata.get("holdings", [])
        if h.get("ticker") not in ("", "CASH", None)
    }
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

    # ── 近期財經 digest（質化脈絡）──────────────────────────────────────────────
    for line in build_digest_section({t for t in all_held if t}):
        L(line)

    # ── 任務指令 ───────────────────────────────────────────────────────────────
    L("---")
    L("## 🎯 你的任務")
    L("你是投資戰情官。以上只是**起點簡報**，不是最終答案。")
    L("請依 `agent/CLAUDE.md` 的資訊蒐集 SOP：")
    L("1. 從上表挑出 3-6 檔「值得深挖」的標的（大漲大跌／接近觸發價／有財報催化）。")
    L("2. 對深挖標的用 `curl -s http://127.0.0.1:6000/api/...` 查技術面、新聞、財報日、估值。")
    L("3. 用 WebSearch 查當日重大市場新聞與各標的催化劑。")
    L("4. curl macro/market endpoints 確認大盤 regime。")
    L("5. 深挖標的若出現在上方「近期財經 digest」，先 curl")
    L("   `\"http://127.0.0.1:6000/api/finance-digest/by-ticker?tickers=X,Y&days=21\"` 讀 TLDR，")
    L("   把新聞/敘事脈絡納入 thesis（引用時註明來源日期）。digest 是質化補充，非技術面替代品。")
    L("完成後**輸出六區塊**：`<<<SUMMARY>>> <<<SHARED>>> <<<MAIN>>> <<<MOM>>> <<<MIMI>>> <<<DECISIONS>>>`")
    L("（格式與 DECISIONS JSON schema 詳見 agent/CLAUDE.md 的「輸出契約」。每檔持倉必須逐一點評。）")

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

    db = SessionLocal()
    try:
        # Step 1：大盤 + 板塊
        print("[1/4] 抓大盤 + 板塊報價...")
        all_syms = MACRO_SYMBOLS + list(SECTOR_ETFS.keys())
        all_quotes = fetch_quotes(all_syms)
        market_data = {s: all_quotes[s] for s in MACRO_SYMBOLS if s in all_quotes}
        sector_data = {s: all_quotes[s] for s in SECTOR_ETFS if s in all_quotes}

        # Step 2：持倉 + 風險旗標
        print("[2/4] 載入持倉 + 風險旗標...")
        portfolio_payload = load_portfolio(db)
        portfolio_names = (
            portfolio_payload.get("portfolio_names", [])
            if isinstance(portfolio_payload, dict) else []
        )
        risk_flags = fetch_risk_flags(portfolio_names, db)

        # Step 3：Mimi doc + 昨日計分卡
        print("[3/4] 解析 Mimi 策略文件 + 昨日決策計分...")
        mimi_sections = parse_mimi_doc()
        scorecard_md = score_yesterday(analysis_date)
        if scorecard_md:
            print("       昨日決策計分卡：已生成")

        # Step 4：組簡報
        print("[4/4] 組裝今日作戰簡報...")
        briefing = build_briefing(
            analysis_date, market_data, sector_data,
            portfolio_payload, mimi_sections, risk_flags,
            scorecard_md, backend_ok,
        )
        print(f"       簡報：{len(briefing):,} chars")

        if args.dry_run:
            print("\n" + "=" * 25 + " 簡報預覽（dry-run）" + "=" * 25)
            print(briefing)
            print("=" * 70)
            print(f"\n總長度：{len(briefing):,} chars（目標 <8000）")
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

        # Telegram / LINE：只發 SUMMARY
        if not args.no_telegram:
            import re
            summary_m = re.search(r"<<<SUMMARY>>>(.*?)(?=<<<[A-Z_]+>>>|\Z)", analysis, re.DOTALL)
            summary_body = summary_m.group(1).strip() if summary_m else analysis[:400].strip()
            telegram_text = f"🎖️ 戰情中心｜{date_str}\n\n{summary_body}"
            print("[telegram/line] 發送 SUMMARY...")
            send_telegram(telegram_text)
            send_line(telegram_text, important_report=True)

        # Obsidian：復用 save_obsidian_note，開頭標註戰情中心產出
        if not args.no_obsidian:
            # save_obsidian_note 靠 prompt_text 抽 tickers（#### TICKER（），簡報是一行式抽不到，
            # 這裡直接把持倉 ticker 拼成 daily 格式的假 prompt 供 frontmatter 用。
            fake_prompt = "\n".join(
                f"#### {h.get('ticker')}（{h.get('company_name', h.get('ticker'))}）"
                for pdata in portfolio_payload.get("portfolios", {}).values()
                for h in pdata.get("holdings", [])
                if h.get("ticker") not in ("", "CASH", None)
            )
            note_path = save_obsidian_note(
                analysis_date, fake_prompt, analysis,
                portfolio_md_raw="",
                portfolio_names=portfolio_names,
                scorecard_md=scorecard_md,
            )
            # 在 note 開頭插一行戰情中心標註（與舊管線區別）
            try:
                content = note_path.read_text(encoding="utf-8")
                marker = "> 由戰情中心（war_room）產出\n"
                if marker not in content:
                    # 插在 frontmatter 之後、H1 之前（找第二個 '---\n' 結尾）
                    parts = content.split("---\n", 2)
                    if len(parts) == 3:
                        content = f"---\n{parts[1]}---\n\n{marker}\n{parts[2].lstrip()}"
                    else:
                        content = marker + content
                    note_path.write_text(content, encoding="utf-8")
            except Exception as e:
                print(f"[obsidian] 標註戰情中心失敗（略過）：{e}")
            print(f"\n✅ Obsidian note: {note_path}")

        print("\n✅ War Room 完成！")

    finally:
        db.close()


if __name__ == "__main__":
    main()
