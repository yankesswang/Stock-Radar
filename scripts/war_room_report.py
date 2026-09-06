#!/usr/bin/env python3
"""
War Room 三層報告組裝 —— 把 853 行的長文變成「90 秒看完，要細節再往下翻」。

問題來源（2026-08-23 檢討）：
  - 報告太長，而且把 `<<<DECISIONS>>>` 的完整 JSON 塞在人看的報告正文裡。
  - 30 幾檔持倉逐檔寫一段，其中 26 檔結論都是 hold，重複敘述淹沒了真正要處理的 3 檔。
  - Portal 最上層還想再叫一次 LLM 總結 —— 那會變成第四種語氣。

改法：**第一層由程式從 DECISIONS + 執行狀態 + 組合風險組裝**（不再多叫一次 LLM），
模型只負責它擅長的那層（逐檔判讀與大盤敘事），JSON 只寫 journal 不進正文。

    第一層  90 秒晨報   今天要做 / 等待條件 / 最大風險 / 其餘維持
    第二層  異常持倉     只展開 action≠hold、貼停損、財報將至的標的
    第三層  附錄        全部持倉、大盤、Mimi、計分卡、決策帳本、組合風險、完整性
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

MARKER_RE = re.compile(r"^<<<([A-Z_]+)>>>\s*$", re.MULTILINE)
TICKER_SECTION_RE = re.compile(r"^[▍▌]\s*([A-Z0-9][A-Z0-9\.\-]{0,9})\s*$", re.MULTILINE)
OUTRO_RE = re.compile(r"^\**\s*(?:明日操作計劃|整體評估)", re.MULTILINE)

ACTION_ZH = {
    "add": "加倉", "open": "建倉", "buy": "買進", "accumulate": "分批買進",
    "trim": "減碼", "reduce": "減碼", "exit": "出場", "stop_loss": "停損",
    "sell": "賣出", "sell_short": "放空", "hold": "維持",
}
ACTIONABLE = {"add", "open", "buy", "accumulate", "trim", "reduce",
              "exit", "stop_loss", "sell", "sell_short"}

NEAR_STOP_PCT = 4.0        # 距停損多少 % 以內算「今天要處理」
EARNINGS_SOON_DAYS = 3     # 幾天內財報算「今天要處理」
ATTENTION_EARNINGS_DAYS = 7   # 第二層展開的財報窗口
MAX_WAITING_ROWS = 8          # 90 秒晨報「等待條件」最多列幾條（其餘見附錄）


# ── 區塊切分 ──────────────────────────────────────────────────────────────────

def split_blocks(text: str) -> dict[str, str]:
    """把 `<<<MARKER>>>` 分段的模型輸出切成 {marker: body}。沒有 marker 時回 {'RAW': text}。"""
    if not text:
        return {}
    marks = list(MARKER_RE.finditer(text))
    if not marks:
        return {"RAW": text.strip()}
    blocks: dict[str, str] = {}
    preamble = text[:marks[0].start()].strip()
    if preamble:
        blocks["_PREAMBLE"] = preamble
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        blocks[m.group(1)] = text[m.end():end].strip()
    return blocks


def strip_decisions_json(body: str) -> str:
    """把 DECISIONS 區塊裡的 ```json ...``` 拿掉（JSON 只進 journal，不進人看的報告）。"""
    if not body:
        return ""
    without = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", "", body, flags=re.DOTALL).strip()
    return without


def split_ticker_sections(body: str) -> tuple[str, dict[str, str]]:
    """把 `▍ TICKER` 逐檔段落切開，回 (前言, {TICKER: 段落全文})。"""
    if not body:
        return "", {}
    marks = list(TICKER_SECTION_RE.finditer(body))
    if not marks:
        return body.strip(), {}
    intro = body[:marks[0].start()].strip()
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        out[m.group(1).upper()] = body[m.start():end].rstrip()
    return intro, out


def split_outro(sections: dict[str, str]) -> str:
    """把黏在最後一檔尾巴的「明日操作計劃／整體評估」切出來（就地修掉最後一檔）。

    模型把整個組合的操作計劃寫在最後一檔之後，切段時會被算進最後那檔。第二層只展開
    異常持倉，最後一檔剛好是普通 hold 時，整個組合的操作計劃就會掉進附錄——那是
    第二層最該看的東西。
    """
    if not sections:
        return ""
    last = next(reversed(sections))
    body = sections[last]
    m = OUTRO_RE.search(body)
    if not m:
        return ""
    sections[last] = body[:m.start()].rstrip()
    return body[m.start():].strip()


# ── 第一層：90 秒晨報（純程式組裝）─────────────────────────────────────────────

def _sanitize(text: str, freshness: dict | None, exec_state: dict | None) -> str:
    """DECISIONS 裡的 trigger / thesis 也會被直接印到第一層，一樣要過守則。

    模型常在 trigger 欄寫「今日執行 trim 20-25%」——正文被改寫了，第一層卻照抄原句，
    等於守則只做了一半。
    """
    if not text:
        return text
    try:
        from scripts.report_guard import check_execution_claims, check_realtime_language
        out, _ = check_realtime_language(text, freshness)
        out, _ = check_execution_claims(out, exec_state)
        return out
    except Exception:
        return text


def _exec_label(ticker: str, exec_state: dict | None) -> str:
    """該 ticker 目前最前進的執行狀態，轉成人話。"""
    from scripts.exec_state import EXECUTED_STATES, STATE_RANK, STATE_ZH
    best, best_rank = None, -1
    for entry in ((exec_state or {}).get("decisions") or {}).values():
        if str(entry.get("ticker") or "").upper() != ticker:
            continue
        rank = STATE_RANK.get(entry.get("state"), -1)
        if rank > best_rank:
            best, best_rank = entry, rank
    if not best:
        return ""
    st = best.get("state")
    if st in EXECUTED_STATES:
        return f"（前次{ACTION_ZH.get(best.get('action'), best.get('action'))}已對帳執行）"
    return f"（前次{ACTION_ZH.get(best.get('action'), best.get('action'))}：{STATE_ZH.get(st, st)}）"


def build_morning_brief(*, decisions_payload: dict | None, risk: dict | None,
                        exec_state: dict | None, freshness: dict | None,
                        manifest: dict | None, earnings_events: list[dict] | None,
                        quotes_ok: tuple[int, int] | None = None) -> list[str]:
    """第一層：今天要做什麼、在等什麼、最怕什麼。全部從結構化資料算出來。"""
    decisions = [d for d in ((decisions_payload or {}).get("decisions") or [])
                 if isinstance(d, dict) and d.get("ticker")]
    by_ticker = {str(d["ticker"]).upper(): d for d in decisions}
    regime = (decisions_payload or {}).get("market_regime") or "—"

    lines: list[str] = ["## ⏱️ 90 秒晨報", ""]

    # 市場狀態 + 資料品質（一眼看出這份報告能不能信）
    lines.append(f"**市場狀態**：{regime}")
    quality_bits: list[str] = []
    if quotes_ok:
        got, total = quotes_ok
        quality_bits.append(f"{got}/{total} 檔有最新報價")
    if freshness:
        quality_bits.append(f"{freshness.get('status')}／{freshness.get('status_zh')}"
                            + (f"（收盤後 {freshness['lag_hours']} 小時）"
                               if freshness.get("lag_hours") is not None else ""))
    if manifest:
        miss = manifest.get("missing") or []
        deg = manifest.get("degraded") or []
        quality_bits.append("產出完整" if not miss and not deg
                            else f"缺 {len(miss)} 份／降級 {len(deg)} 份")
    if (risk or {}).get("total_value_usd"):
        pa = ((manifest or {}).get("portfolio") or {}).get("as_of")
        quality_bits.append(f"持倉快照 {pa or '未知'}")
    lines.append("**資料品質**：" + "｜".join(quality_bits))
    lines.append("")

    # ── 今天需要處理 ──────────────────────────────────────────────────────────
    todo: dict[str, list[str]] = {}
    order: list[str] = []

    def _add(ticker: str, note: str) -> None:
        t = ticker.upper()
        if t not in todo:
            todo[t] = []
            order.append(t)
        todo[t].append(note)

    for d in decisions:
        action = str(d.get("action") or "").lower()
        if action not in ACTIONABLE:
            continue
        t = str(d["ticker"]).upper()
        bits = [f"建議**{ACTION_ZH.get(action, action)}**"]
        if d.get("target_weight_pct") is not None:
            bits.append(f"目標倉位 {d['target_weight_pct']}%")
        if d.get("trigger"):
            bits.append(f"觸發：{_sanitize(str(d['trigger']), freshness, exec_state)[:70]}")
        if d.get("stop") is not None:
            bits.append(f"停損 ${d['stop']}")
        label = _exec_label(t, exec_state)
        _add(t, "；".join(bits) + (f"　{label}" if label else "　← 尚未確認執行"))

    stop_rows = ((risk or {}).get("stop_cluster") or {}).get("rows") or []
    for r in stop_rows:
        if r.get("already_below"):
            _add(r["ticker"], f"🔴 現價 ${r['price']} **已在停損 ${r['stop']} 之下**，"
                              f"最大風險 US${abs(r['downside_usd']):,.0f}")
        elif r.get("downside_pct") is not None and abs(r["downside_pct"]) <= NEAR_STOP_PCT:
            # 現價一起印：以前只有「已跌破」那行才寫現價，portal 今日焦點表的現價欄
            # 近停損的那些全是空的，讀者得自己去查
            px = f"現價 ${r['price']}，" if r.get("price") is not None else ""
            _add(r["ticker"], f"{px}距停損 ${r['stop']} 僅 {abs(r['downside_pct']):.1f}%，"
                              f"最大風險 US${abs(r['downside_usd']):,.0f}")

    for e in (earnings_events or []):
        t = str(e.get("ticker") or "").upper()
        d_until = e.get("days_until")
        if e.get("status") == "reported" or d_until is None:
            continue
        if d_until > EARNINGS_SOON_DAYS:
            continue
        if t not in by_ticker:
            continue
        when = "今天" if d_until == 0 else ("明天" if d_until == 1 else f"{d_until} 天後")
        _add(t, f"📅 {e['date']}（{when}）財報 —— 財報前不加倉，二元風險優先")

    lines.append("### 今天需要處理")
    if order:
        for i, t in enumerate(order, start=1):
            notes = todo[t]
            lines.append(f"{i}. **{t}**：{notes[0]}")
            for extra in notes[1:]:
                lines.append(f"   - {extra}")
    else:
        lines.append("- （無：所有持倉皆為維持，且無貼停損／財報事件）")
    lines.append("")

    # ── 等待條件 ─────────────────────────────────────────────────────────────
    waiting: list[str] = []
    from scripts.exec_state import STATE_ACKNOWLEDGED, STATE_TRIGGERED, STATE_ZH
    for entry in ((exec_state or {}).get("decisions") or {}).values():
        if entry.get("state") not in (STATE_TRIGGERED, STATE_ACKNOWLEDGED):
            continue
        t = entry["ticker"]
        waiting.append(f"- ⚠️ **{t}**：{ACTION_ZH.get(entry.get('action'), entry.get('action'))} "
                       f"條件已於 {entry.get('trigger_date') or '近期'} 觸發，"
                       f"**{STATE_ZH.get(entry['state'])}** —— 需人工確認是否成交")

    # 掛著的條件單很多，但只有「快到了」的才值得放進 90 秒晨報；
    # 剩下的每天照抄一次只會把真正接近的那幾條淹掉（完整清單在附錄的決策帳本）。
    positions = ((risk or {}).get("positions") or {})
    candidates: list[tuple[float, str]] = []
    for d in decisions:
        action = str(d.get("action") or "").lower()
        t = str(d["ticker"]).upper()
        if action in ACTIONABLE or t in todo:
            continue
        tp = d.get("trigger_price")
        if tp is None and not d.get("trigger"):
            continue
        price = (positions.get(t) or {}).get("current_price")
        dist = (abs(float(tp) - price) / price * 100
                if isinstance(tp, (int, float)) and price else 999.0)
        cond = d.get("trigger") or (
            f"{'漲破' if d.get('direction') == 'above' else '跌破'} ${tp}")
        near = f"（距 {dist:.1f}%）" if dist < 900 else ""
        candidates.append((dist, f"- {t}{near}：{_sanitize(str(cond), freshness, exec_state)[:70]}"))
    candidates.sort(key=lambda x: x[0])
    waiting += [line for _, line in candidates[:MAX_WAITING_ROWS]]
    overflow = len(candidates) - MAX_WAITING_ROWS
    if overflow > 0:
        waiting.append(f"- （另有 {overflow} 條距觸發價較遠的條件單，見附錄決策帳本）")

    lines.append("### 等待條件")
    lines += waiting or ["- （無掛著的條件單）"]
    lines.append("")

    # ── 最大風險 ─────────────────────────────────────────────────────────────
    lines.append("### 最大風險")
    risk_lines: list[str] = []
    for w in ((risk or {}).get("warnings") or []):
        risk_lines.append(f"- {'🔴' if w['level'] == 'high' else '🟡'} {w['text']}")
    sc = (risk or {}).get("stop_cluster") or {}
    if sc.get("total_downside_usd") is not None:
        unrel = "　（⚠️ 匯率未折算，金額不可信）" if sc.get("unreliable") else ""
        risk_lines.append(f"- 全部停損同時觸發：約 **US${sc['total_downside_usd']:,.0f}"
                          f"（組合 {sc.get('total_downside_pct_of_portfolio')}%）**{unrel}")
    for b in ((risk or {}).get("bellwether_earnings") or [])[:2]:
        risk_lines.append(f"- 🌊 {b['ticker']} {b['date']} 財報會同時牽動「{b['theme']}」"
                          f"共 {b['theme_weight_pct']:.1f}% 的組合")
    for d in decisions:
        if str(d.get("risk_level") or "").lower() == "high":
            risk_lines.append(f"- 🔴 {str(d['ticker']).upper()}（risk=high）："
                              f"{_sanitize(str(d.get('thesis') or ''), freshness, exec_state)[:80]}")
    lines += risk_lines or ["- （無組合層警告）"]
    lines.append("")

    # ── 其餘維持 ─────────────────────────────────────────────────────────────
    high_risk = {t for t, d in by_ticker.items()
                 if str(d.get("risk_level") or "").lower() == "high"}
    rest = [t for t, d in by_ticker.items()
            if str(d.get("action") or "").lower() == "hold"
            and t not in todo and t not in high_risk]
    if rest:
        lines.append(f"### 其餘 {len(rest)} 檔：維持，無新事件")
        lines.append("　".join(sorted(rest)))
        lines.append("")
    return lines


# ── 第二層：異常持倉 ──────────────────────────────────────────────────────────

def attention_tickers(*, decisions_payload: dict | None, risk: dict | None,
                      exec_state: dict | None, earnings_events: list[dict] | None) -> set[str]:
    """需要展開細節的標的 = action≠hold ∪ risk=high ∪ 貼停損 ∪ 財報將至 ∪ 有未結執行。"""
    out: set[str] = set()
    for d in ((decisions_payload or {}).get("decisions") or []):
        if not isinstance(d, dict) or not d.get("ticker"):
            continue
        t = str(d["ticker"]).upper()
        if str(d.get("action") or "").lower() in ACTIONABLE:
            out.add(t)
        if str(d.get("risk_level") or "").lower() == "high":
            out.add(t)
    for r in (((risk or {}).get("stop_cluster") or {}).get("rows") or []):
        if r.get("already_below") or (r.get("downside_pct") is not None
                                      and abs(r["downside_pct"]) <= NEAR_STOP_PCT):
            out.add(r["ticker"])
    from scripts.exec_state import STATE_ACKNOWLEDGED, STATE_TRIGGERED
    for entry in ((exec_state or {}).get("decisions") or {}).values():
        if entry.get("state") in (STATE_TRIGGERED, STATE_ACKNOWLEDGED):
            out.add(entry["ticker"])
    for e in (earnings_events or []):
        d_until = e.get("days_until")
        if e.get("status") != "reported" and d_until is not None and 0 <= d_until <= ATTENTION_EARNINGS_DAYS:
            out.add(str(e.get("ticker") or "").upper())
    return {t for t in out if t}


def render_hold_table(decisions_payload: dict | None, skip: set[str],
                      risk: dict | None) -> list[str]:
    """普通 hold 合併成一張表 —— 不逐檔寫重複敘述。"""
    rows = []
    positions = ((risk or {}).get("positions") or {}) if isinstance(risk, dict) else {}
    for d in ((decisions_payload or {}).get("decisions") or []):
        if not isinstance(d, dict) or not d.get("ticker"):
            continue
        t = str(d["ticker"]).upper()
        if t in skip or str(d.get("action") or "").lower() != "hold":
            continue
        pos = positions.get(t) or {}
        rows.append({
            "ticker": t,
            "weight": pos.get("weight_pct"),
            "risk": d.get("risk_level") or "—",
            "trigger": str(d.get("trigger") or "—")[:44],
            "stop": d.get("stop"),
            "thesis": str(d.get("thesis") or "—")[:60],
        })
    if not rows:
        return []
    rows.sort(key=lambda r: -(r["weight"] or 0))
    out = [
        "### 其餘持倉（維持，合併檢視）",
        "",
        "| Ticker | 權重 | 風險 | 觸發條件 | 停損 | 論點 |",
        "|--------|------|------|---------|------|------|",
    ]
    for r in rows:
        w = f"{r['weight']:.1f}%" if r["weight"] is not None else "—"
        stop = f"${r['stop']}" if r["stop"] is not None else "—"
        out.append(f"| {r['ticker']} | {w} | {r['risk']} | {r['trigger'].replace('|','／')} "
                   f"| {stop} | {r['thesis'].replace('|','／')} |")
    out.append("")
    return out


# ── 組裝 ──────────────────────────────────────────────────────────────────────

def assemble_report(*, analysis_date: date, analysis: str,
                    decisions_payload: dict | None,
                    freshness: dict | None,
                    exec_state: dict | None,
                    exec_section_md: str = "",
                    risk: dict | None,
                    risk_md: str = "",
                    manifest: dict | None,
                    earnings_events: list[dict] | None,
                    scorecard_md: str = "", ledger_md: str = "",
                    audit_md: str = "", audit_summary: str = "",
                    quotes_ok: tuple[int, int] | None = None,
                    manifest_md: str = "") -> str:
    """三層完整報告（給鏡像目錄／Obsidian／portal 讀）。"""
    from scripts.report_meta import render_freshness_block

    blocks = split_blocks(analysis)
    attention = attention_tickers(decisions_payload=decisions_payload, risk=risk,
                                  exec_state=exec_state, earnings_events=earnings_events)

    parts: list[str] = [
        f"# 🎖️ 盤後復盤（戰情中心）— {analysis_date.isoformat()}",
        "",
        render_freshness_block(freshness or {}),
        "",
    ]
    if audit_summary or audit_md:
        parts += [audit_summary or audit_md, ""]

    # ── 第一層 ───────────────────────────────────────────────────────────────
    parts += build_morning_brief(
        decisions_payload=decisions_payload, risk=risk, exec_state=exec_state,
        freshness=freshness, manifest=manifest, earnings_events=earnings_events,
        quotes_ok=quotes_ok,
    )
    if blocks.get("SUMMARY"):
        parts += ["> **戰情官一句話**：" + " ".join(blocks["SUMMARY"].split("\n")[:2]).strip(), ""]
    parts += ["---", ""]

    # ── 第二層：異常持倉 ──────────────────────────────────────────────────────
    parts += ["## 🔎 異常持倉（只展開需要判斷的）", ""]
    if attention:
        parts.append("> 納入條件：action≠hold、風險 high、距停損 "
                     f"≤{NEAR_STOP_PCT:.0f}%、{ATTENTION_EARNINGS_DAYS} 天內財報、"
                     "或有未結的執行狀態。")
        parts.append("")
    shown: set[str] = set()
    outro_shown: set[str] = set()
    for key in [k for k in blocks if k not in
                ("SUMMARY", "SHARED", "MIMI", "DECISIONS", "_PREAMBLE", "RAW")]:
        intro, sections = split_ticker_sections(blocks[key])
        outro = split_outro(sections)
        picked = [t for t in sections if t in attention]
        if not picked:
            continue
        parts.append(f"### 【{key.title()}】")
        if intro:
            parts += [intro, ""]
        for t in picked:
            parts += [sections[t], ""]
            shown.add(t)
        if outro:  # 該組合的明日操作計劃／整體評估跟著異常持倉走，不掉進附錄
            parts += [outro, ""]
            outro_shown.add(key)
    missed = sorted(attention - shown)
    if missed:
        parts.append(f"> ⚠️ 下列標的達到展開條件但模型沒有逐檔段落：{', '.join(missed)}"
                     "　（請檢查 SOP「每檔持倉都要逐一點評」是否被違反）")
        parts.append("")
    parts += render_hold_table(decisions_payload, attention, risk)
    parts += ["---", ""]

    # ── 第三層：附錄 ─────────────────────────────────────────────────────────
    parts += ["# 📚 附錄（完整內容）", ""]
    if blocks.get("SHARED"):
        parts += ["## 🌏 大盤形勢判讀", "", blocks["SHARED"], ""]
    if risk_md:
        parts += [risk_md, ""]
    if exec_section_md:
        parts += [exec_section_md, ""]
    for key in [k for k in blocks if k not in
                ("SUMMARY", "SHARED", "MIMI", "DECISIONS", "_PREAMBLE", "RAW")]:
        intro, sections = split_ticker_sections(blocks[key])
        outro = split_outro(sections)
        parts += [f"## 💼 【{key.title()}】其餘持倉逐檔分析", ""]
        if intro:
            parts += [intro, ""]
        already = [t for t in sections if t in shown]
        if already:
            parts += [f"> {'、'.join(already)} 已在上方「異常持倉」展開，此處不重複。", ""]
        rest_sections = [body for t, body in sections.items() if t not in shown]
        if rest_sections:
            parts += rest_sections + [""]
        elif not sections:
            parts += [blocks[key], ""]
        if outro and key not in outro_shown:
            parts += [outro, ""]
    if blocks.get("MIMI"):
        parts += ["## 👁️ Mimi 觀察名單", "", blocks["MIMI"], ""]
    if blocks.get("DECISIONS"):
        rest = strip_decisions_json(blocks["DECISIONS"])
        if rest:
            parts += ["## 🧾 決策說明", "", rest, ""]
    parts += ["> 機器可讀的 DECISIONS JSON 已寫入 "
              f"`data/agent/journal/{analysis_date.isoformat()}.json`，不再放進正文。", ""]
    if scorecard_md:
        parts += [scorecard_md.strip(), ""]
    if ledger_md:
        parts += [ledger_md.strip(), ""]
    if manifest_md:
        parts += [manifest_md, ""]
    if audit_summary and audit_md and audit_md != audit_summary:
        parts += ["## 🔍 產出驗證明細", "", audit_md, ""]
    if blocks.get("RAW"):
        parts += ["## 原始輸出（未依契約分段）", "", blocks["RAW"], ""]

    return "\n".join(parts).rstrip() + "\n"


def build_telegram_summary(*, analysis_date: date, blocks_summary: str,
                           freshness: dict | None, morning_lines: list[str],
                           manifest: dict | None) -> str:
    """Telegram 只發第一層 + 模型摘要，並在最前面標時效。"""
    from scripts.report_meta import freshness_one_liner

    head = [f"🎖️ 戰情中心｜{analysis_date.isoformat()}"]
    warn = freshness_one_liner(freshness or {})
    if warn:
        head.append(warn)
    if manifest and manifest.get("overall") != "success":
        miss = "、".join((manifest.get("missing") or [])[:4])
        head.append(f"📋 產出{'部分完成' if manifest['overall'] == 'partial' else '失敗'}"
                    + (f"（缺 {miss}）" if miss else ""))

    # 第一層裡「今天需要處理 / 等待條件」兩段就是 Telegram 最該看的
    body: list[str] = []
    capture = False
    for line in morning_lines:
        if line.startswith("### 今天需要處理") or line.startswith("### 等待條件"):
            capture = True
            body.append(line.replace("### ", "▌"))
            continue
        if line.startswith("### "):
            capture = False
            continue
        if capture and line.strip():
            body.append(line)
    text = "\n".join(head + [""] + body[:24] + ["", "—— 戰情官摘要 ——", blocks_summary.strip()[:700]])
    return text
