#!/usr/bin/env python3
"""
決策執行狀態機 —— 把「價格觸發」和「真的成交」徹底分開。

問題來源（2026-08-23 檢討）：
  8/21 War Room 寫「FSLY stop loss 已執行，倉位歸零」，隔天的 Portfolio 仍顯示持有 40 股。
  舊計分卡只能證明**價格觸發**，卻被寫成**已成交** —— 報告語氣比資料可信度更確定，
  這是整套系統最危險的一種錯誤（讀者會以為部位已經處理掉了）。

五態（只進不退，abandoned / expired 為終態）：

    recommended → triggered → acknowledged → executed → reconciled
                                  ↘ abandoned          ↘ expired

    recommended   決策已提出，價格條件還沒發生
    triggered     價格條件已發生（decision_ledger 用 high/low 判定）—— 僅此而已，**不代表成交**
    acknowledged  戰情官在後續日誌裡明確回應了這筆觸發（DECISIONS 的 `ack_prev` 欄位）
    executed      **對帳到持倉變化**（data/agent/positions/*.json 逐日差分）或人工確認
    reconciled    executed 後的下一份持倉快照仍然一致（確定不是抓錯／盤中暫態）
    abandoned     戰情官明說放棄（ack_prev = abandoned）
    expired       超過追蹤窗口仍未觸發

**只有 executed / reconciled 允許在報告裡寫「已執行」。**其餘一律「條件已觸發，待確認」。
scripts/report_guard.py 會拿本模組的狀態去掃描產出，違規就攔下並改寫。

CLI：
    uv run python scripts/exec_state.py                          # 印出目前所有未結決策的狀態
    uv run python scripts/exec_state.py --snapshot               # 只抓一份持倉快照
    uv run python scripts/exec_state.py --confirm FSLY --date 2026-08-20 --state executed --note "手動確認已市價賣出"
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = PROJECT_ROOT / "data" / "agent"
POSITIONS_DIR = DATA_DIR / "positions"
JOURNAL_DIR = DATA_DIR / "journal"
STATE_PATH = DATA_DIR / "exec_state.json"

STATE_RECOMMENDED = "recommended"
STATE_TRIGGERED = "triggered"
STATE_ACKNOWLEDGED = "acknowledged"
STATE_EXECUTED = "executed"
STATE_RECONCILED = "reconciled"
STATE_ABANDONED = "abandoned"
STATE_EXPIRED = "expired"

# 排序用（只進不退）
STATE_RANK = {
    STATE_RECOMMENDED: 0,
    STATE_TRIGGERED: 1,
    STATE_ACKNOWLEDGED: 2,
    STATE_EXECUTED: 3,
    STATE_RECONCILED: 4,
}
TERMINAL_STATES = {STATE_ABANDONED, STATE_EXPIRED, STATE_RECONCILED}
# 唯一可以在報告裡寫「已執行」的狀態
EXECUTED_STATES = {STATE_EXECUTED, STATE_RECONCILED}

STATE_ZH = {
    STATE_RECOMMENDED: "已建議（條件未發生）",
    STATE_TRIGGERED: "條件已觸發，待確認",
    STATE_ACKNOWLEDGED: "已回應，待對帳",
    STATE_EXECUTED: "已執行（持倉已變動）",
    STATE_RECONCILED: "已對帳（持倉變動已確認）",
    STATE_ABANDONED: "已放棄（戰情官明說不執行）",
    STATE_EXPIRED: "已過期（窗口內未觸發）",
}
STATE_ICON = {
    STATE_RECOMMENDED: "⏳",
    STATE_TRIGGERED: "⚠️",
    STATE_ACKNOWLEDGED: "📝",
    STATE_EXECUTED: "✅",
    STATE_RECONCILED: "🔒",
    STATE_ABANDONED: "🚫",
    STATE_EXPIRED: "🕛",
}

# action → 期待的持倉變化方向
INCREASE_ACTIONS = {"add", "open", "buy", "accumulate"}
DECREASE_ACTIONS = {"trim", "reduce"}
CLOSE_ACTIONS = {"exit", "stop_loss", "sell", "close", "sell_short"}
ACTIONABLE = INCREASE_ACTIONS | DECREASE_ACTIONS | CLOSE_ACTIONS

TRACK_WINDOW_DAYS = 14        # 超過這個天數還沒觸發就標 expired，不再佔用簡報版面
SHARE_EPS = 1e-6              # 零股比對容差


# ── 持倉快照（executed 的唯一客觀證據）────────────────────────────────────────

def snapshot_positions(as_of: date, portfolio_payload: dict) -> dict:
    """把當下持倉（同 ticker 跨帳戶合併）落地 data/agent/positions/{date}.json。

    多批持倉（例如 Mom 的 TSLA 在永豐與富邦各一筆）必須先合併，否則差分會被拆單噪音淹沒。
    """
    positions: dict[str, dict] = {}
    portfolios = (portfolio_payload or {}).get("portfolios", {}) or {}
    for pname, pdata in portfolios.items():
        for h in pdata.get("holdings", []) or []:
            t = str(h.get("ticker") or "").upper()
            if not t or t == "CASH":
                continue
            row = positions.setdefault(t, {"shares": 0.0, "market_value": 0.0, "portfolios": {}})
            shares = float(h.get("shares") or 0.0)
            row["shares"] += shares
            row["market_value"] += float(h.get("market_value") or 0.0)
            row["portfolios"][pname] = round(row["portfolios"].get(pname, 0.0) + shares, 6)
    for row in positions.values():
        row["shares"] = round(row["shares"], 6)
        row["market_value"] = round(row["market_value"], 2)

    record = {
        "date": as_of.isoformat(),
        "captured_at": datetime.now(TAIPEI_TZ).isoformat(),
        "positions": positions,
    }
    try:
        POSITIONS_DIR.mkdir(parents=True, exist_ok=True)
        (POSITIONS_DIR / f"{as_of.isoformat()}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[exec] 持倉快照寫入失敗（略過）：{e}")
    return record


def load_position_snapshots(limit_before: date | None = None) -> list[tuple[date, dict]]:
    out: list[tuple[date, dict]] = []
    if not POSITIONS_DIR.exists():
        return out
    for p in sorted(POSITIONS_DIR.glob("*.json")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if limit_before and d > limit_before:
            continue
        try:
            out.append((d, json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def diff_positions(prev: dict, curr: dict) -> dict[str, dict]:
    """{ticker: {prev_shares, curr_shares, delta, kind}}；kind ∈ opened/increased/reduced/closed。"""
    prev_pos = (prev or {}).get("positions", {}) or {}
    curr_pos = (curr or {}).get("positions", {}) or {}
    changes: dict[str, dict] = {}
    for t in set(prev_pos) | set(curr_pos):
        a = float((prev_pos.get(t) or {}).get("shares") or 0.0)
        b = float((curr_pos.get(t) or {}).get("shares") or 0.0)
        delta = b - a
        if abs(delta) <= SHARE_EPS:
            continue
        if a <= SHARE_EPS:
            kind = "opened"
        elif b <= SHARE_EPS:
            kind = "closed"
        elif delta > 0:
            kind = "increased"
        else:
            kind = "reduced"
        changes[t] = {
            "ticker": t, "prev_shares": round(a, 6), "curr_shares": round(b, 6),
            "delta": round(delta, 6), "kind": kind,
        }
    return changes


def collect_position_changes(since: date, until: date) -> dict[str, list[dict]]:
    """[since, until] 之間逐份快照的差分，依 ticker 聚合。"""
    snaps = [(d, s) for d, s in load_position_snapshots(limit_before=until)
             if d >= since - timedelta(days=1)]
    by_ticker: dict[str, list[dict]] = {}
    for (d_prev, s_prev), (d_curr, s_curr) in zip(snaps, snaps[1:]):
        for t, chg in diff_positions(s_prev, s_curr).items():
            by_ticker.setdefault(t, []).append({**chg, "from": d_prev.isoformat(), "as_of": d_curr.isoformat()})
    return by_ticker


# ── 狀態儲存 ──────────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("decisions"), dict):
                return data
    except Exception as e:
        print(f"[exec] 狀態檔讀取失敗，改用空狀態：{e}")
    return {"decisions": {}}


def save_state(state: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = datetime.now(TAIPEI_TZ).isoformat()
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[exec] 狀態檔寫入失敗（略過）：{e}")


def decision_key(proposed_date: date | str, ticker: str, action: str) -> str:
    d = proposed_date if isinstance(proposed_date, str) else proposed_date.isoformat()
    return f"{d}|{str(ticker).upper()}|{str(action).lower()}"


def _advance(entry: dict, new_state: str, evidence: str, at: str | None = None) -> bool:
    """只進不退地推進狀態；真的有推進回 True。"""
    cur = entry.get("state", STATE_RECOMMENDED)
    if cur in TERMINAL_STATES and new_state not in TERMINAL_STATES:
        return False
    if new_state in STATE_RANK and cur in STATE_RANK and STATE_RANK[new_state] <= STATE_RANK[cur]:
        return False
    entry["state"] = new_state
    entry.setdefault("history", []).append({
        "state": new_state,
        "at": at or datetime.now(TAIPEI_TZ).isoformat(),
        "evidence": evidence,
    })
    return True


def _matches(action: str, kind: str) -> bool:
    a = str(action or "").lower()
    if a in INCREASE_ACTIONS:
        return kind in ("opened", "increased")
    if a in DECREASE_ACTIONS:
        return kind in ("reduced", "closed")
    if a in CLOSE_ACTIONS:
        return kind in ("closed", "reduced")
    return False


# ── 主流程：把 journal + 觸發結果 + 持倉差分融合成狀態 ─────────────────────────

def sync_states(analysis_date: date, open_rows: list[dict],
                journals: list[tuple[date, dict]] | None = None) -> dict:
    """更新並回傳狀態表。

    open_rows  decision_ledger.compute_open_decisions() 的輸出（帶 status/hit_date）
    journals   近期 journal（用來讀 DECISIONS 的 `ack_prev` 欄位）
    """
    state = load_state()
    decisions = state["decisions"]

    # 1. 建檔 + 觸發（來自 decision_ledger 的價格判定）
    for row in open_rows or []:
        action = str(row.get("action") or "").lower()
        if action not in ACTIONABLE:
            continue                     # hold 不需要執行對帳
        key = decision_key(row["proposed_date"], row["ticker"], action)
        entry = decisions.setdefault(key, {
            "ticker": str(row["ticker"]).upper(),
            "action": action,
            "proposed_date": row["proposed_date"],
            "trigger_price": row.get("trigger_price"),
            "direction": row.get("direction"),
            "thesis": row.get("thesis"),
            "state": STATE_RECOMMENDED,
            "history": [{"state": STATE_RECOMMENDED,
                         "at": datetime.now(TAIPEI_TZ).isoformat(),
                         "evidence": f"{row['proposed_date']} journal 提出"}],
        })
        if row.get("status") == "triggered" and row.get("hit_date"):
            entry["trigger_date"] = row["hit_date"]
            _advance(entry, STATE_TRIGGERED,
                     f"{row['hit_date']} 價格觸發（{row.get('direction')} {row.get('trigger_price')}）")

    # 2. 戰情官的明確回應（DECISIONS 的 ack_prev）
    for jd, journal in (journals or []):
        for dec in (journal.get("decisions") or []):
            if not isinstance(dec, dict):
                continue
            ack = dec.get("ack_prev")
            if not isinstance(ack, dict):
                continue
            t = str(dec.get("ticker") or "").upper()
            verdict = str(ack.get("verdict") or "").lower()
            note = str(ack.get("note") or "")[:160]
            for key, entry in decisions.items():
                if entry["ticker"] != t or entry["state"] in TERMINAL_STATES:
                    continue
                if date.fromisoformat(entry["proposed_date"]) >= jd:
                    continue
                if verdict == "abandoned":
                    _advance(entry, STATE_ABANDONED, f"{jd.isoformat()} 戰情官明說放棄：{note}")
                elif verdict in ("executed", "acknowledged", "pending"):
                    _advance(entry, STATE_ACKNOWLEDGED, f"{jd.isoformat()} 戰情官已回應：{note}")
                entry["ack_note"] = note
                entry["ack_verdict"] = verdict

    # 3. 持倉對帳（唯一能推到 executed 的客觀證據）
    earliest = min((date.fromisoformat(e["proposed_date"]) for e in decisions.values()),
                   default=analysis_date)
    changes = collect_position_changes(earliest, analysis_date)
    for key, entry in decisions.items():
        if entry["state"] in TERMINAL_STATES:
            continue
        proposed = date.fromisoformat(entry["proposed_date"])
        matched = [c for c in changes.get(entry["ticker"], [])
                   if date.fromisoformat(c["as_of"]) > proposed and _matches(entry["action"], c["kind"])]
        if not matched:
            continue
        hit = matched[0]
        entry["exec_evidence"] = hit
        _advance(entry, STATE_EXECUTED,
                 f"{hit['as_of']} 持倉對帳：{hit['prev_shares']} → {hit['curr_shares']} 股（{hit['kind']}）")
        # 後續還有一份更新的快照且部位沒再變回去 → reconciled
        later = [d for d, _ in load_position_snapshots(limit_before=analysis_date)
                 if d > date.fromisoformat(hit["as_of"])]
        if later:
            _advance(entry, STATE_RECONCILED,
                     f"{max(later).isoformat()} 後續快照仍一致，對帳完成")

    # 4. 過期
    for entry in decisions.values():
        if entry["state"] != STATE_RECOMMENDED:
            continue
        if (analysis_date - date.fromisoformat(entry["proposed_date"])).days > TRACK_WINDOW_DAYS:
            _advance(entry, STATE_EXPIRED, f"超過 {TRACK_WINDOW_DAYS} 天未觸發")

    save_state(state)
    return state


def active_entries(state: dict, analysis_date: date, window_days: int = TRACK_WINDOW_DAYS) -> list[dict]:
    """近期仍值得在報告裡出現的決策（含剛完成對帳的，讓讀者看到閉環）。"""
    out = []
    for key, entry in (state.get("decisions") or {}).items():
        try:
            proposed = date.fromisoformat(entry["proposed_date"])
        except Exception:
            continue
        if (analysis_date - proposed).days > window_days:
            continue
        out.append({**entry, "key": key})
    out.sort(key=lambda e: (STATE_RANK.get(e["state"], 9), e["proposed_date"]), reverse=True)
    return out


def executed_tickers(state: dict) -> set[str]:
    """允許在報告裡被描述成「已執行」的 ticker。"""
    return {
        e["ticker"] for e in (state.get("decisions") or {}).values()
        if e.get("state") in EXECUTED_STATES
    }


def pending_tickers(state: dict) -> set[str]:
    """已觸發但**還沒對帳到成交**的 ticker —— 這些最容易被寫成「已執行」。"""
    return {
        e["ticker"] for e in (state.get("decisions") or {}).values()
        if e.get("state") in (STATE_TRIGGERED, STATE_ACKNOWLEDGED)
    }


# ── 渲染 ──────────────────────────────────────────────────────────────────────

def render_exec_section(entries: list[dict]) -> list[str]:
    """簡報用的執行狀態表（附硬規則，逼 agent 不准把 triggered 寫成 executed）。"""
    if not entries:
        return []
    # 對帳需要「兩份以上」持倉快照才能做差分。剛啟用時一份都沒有，
    # 這時所有決策都會停在 triggered——要講清楚那是「還沒有證據」而不是「確定沒成交」。
    n_snaps = len(load_position_snapshots())
    warmup: list[str] = []
    if n_snaps < 2:
        warmup = [
            f"> ⚠️ 目前只有 {n_snaps} 份持倉快照，**還無法做成交對帳**（差分至少需要兩份）。",
            "> 在累積足夠快照之前，所有決策最多只會停在 `triggered`——"
            "那代表「沒有證據」，不代表「確定沒成交」。要提前確認可用：",
            "> `uv run python scripts/exec_state.py --confirm TICKER --date YYYY-MM-DD --state executed`",
        ]
    lines = [
        "## 🔐 決策執行狀態（唯一可宣稱「已執行」的依據）",
        "> 狀態機：recommended → triggered → acknowledged → executed → reconciled。",
        "> **只有 executed / reconciled（有持倉變化對帳）才能寫「已執行」**；",
        "> triggered / acknowledged 一律寫「條件已觸發，待確認」。違反此規則的句子會被程式攔下改寫。",
        "> 你必須在 DECISIONS 的 `ack_prev` 欄位逐筆回應（verdict: executed / abandoned / pending）。",
        *warmup,
        "",
    ]
    for e in entries:
        st = e.get("state", STATE_RECOMMENDED)
        icon = STATE_ICON.get(st, "•")
        pd_ = date.fromisoformat(e["proposed_date"])
        tp = e.get("trigger_price")
        arrow = {"above": "漲破", "below": "跌破"}.get(e.get("direction") or "", "")
        cond = f"{arrow} {tp}" if tp is not None and arrow else "無價格條件"
        tail = ""
        if e.get("trigger_date"):
            td = date.fromisoformat(e["trigger_date"])
            tail += f"｜{td.month}/{td.day} 觸發"
        if e.get("exec_evidence"):
            ev = e["exec_evidence"]
            tail += f"｜對帳 {ev['prev_shares']}→{ev['curr_shares']} 股"
        if e.get("ack_note"):
            tail += f"｜前次回應：{e['ack_note'][:40]}"
        lines.append(
            f"- {icon} {e['ticker']}｜{e['action']}｜{cond}（{pd_.month}/{pd_.day} 提出）"
            f"｜狀態 **{st}**（{STATE_ZH.get(st, st)}）{tail}"
        )
    lines.append("")
    return lines


def manual_confirm(ticker: str, proposed_date: date, new_state: str, note: str = "") -> bool:
    """人工確認（讀不到券商對帳單時的逃生門）。"""
    state = load_state()
    hit = False
    for key, entry in state["decisions"].items():
        if entry["ticker"] != ticker.upper():
            continue
        if entry["proposed_date"] != proposed_date.isoformat():
            continue
        if _advance(entry, new_state, f"人工確認：{note or '（無備註）'}"):
            hit = True
    if hit:
        save_state(state)
    return hit


def main() -> None:
    ap = argparse.ArgumentParser(description="決策執行狀態機 — triggered ≠ executed")
    ap.add_argument("--date", help="分析日期 YYYY-MM-DD（預設今天）")
    ap.add_argument("--snapshot", action="store_true", help="只抓一份持倉快照就結束")
    ap.add_argument("--confirm", metavar="TICKER", help="人工確認某筆決策的狀態")
    ap.add_argument("--state", default=STATE_EXECUTED, help="人工確認要推進到的狀態")
    ap.add_argument("--note", default="", help="人工確認備註")
    args = ap.parse_args()
    d = date.fromisoformat(args.date) if args.date else date.today()

    if args.snapshot:
        from backend.app.database import SessionLocal
        from scripts.daily_analysis import load_portfolio
        db = SessionLocal()
        try:
            rec = snapshot_positions(d, load_portfolio(db))
        finally:
            db.close()
        print(f"[exec] 快照 {d}: {len(rec['positions'])} 檔")
        return

    if args.confirm:
        ok = manual_confirm(args.confirm, d, args.state, args.note)
        print("已更新" if ok else f"找不到 {args.confirm} 在 {d} 提出的決策")
        return

    state = load_state()
    entries = active_entries(state, d)
    print("\n".join(render_exec_section(entries)) or "（目前無可追蹤的執行狀態）")


if __name__ == "__main__":
    main()
