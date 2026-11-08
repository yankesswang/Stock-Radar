#!/usr/bin/env python3
"""
產出後驗證（generation guard）—— 模型寫完之後、落地之前的最後一道關。

報告最大的問題不是內容不夠，而是**語氣比資料可信度更確定**。prompt 講一百次也擋不住，
所以這裡用純程式在落地前掃一遍，違規就地改寫並留下稽核紀錄：

  1. 執行宣稱：`已執行 / 已停損 / 倉位歸零 / 已出場`——除非 exec_state 對帳到持倉變化，
     一律改寫成「條件已觸發，待確認」。（8/21 War Room 說 FSLY 已停損倉位歸零，隔天還持有 40 股。）
  2. 即時語氣：BACKFILL / STALE 的報告不准寫「今日執行」「明日開盤」，改成「該交易日／次一交易日」。
  3. 佔位句：「原文未提供」「請參考原文連結」「待補」——沒資料就整段不要寫，不要用佔位句充版面。
  4. 無來源數字（選用）：Options / 資金流這種段落若沒有對應的原始欄位，整段標 unavailable。

用法（程式）：
    text, audit = apply_guards(text, exec_state=state, freshness=meta, tickers=held)
用法（CLI，檢查既有報告）：
    uv run python scripts/report_guard.py ~/.arthur-digest/stock/2026-08-21/"🎖️ War Room.md"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.exec_state import EXECUTED_STATES, STATE_ZH  # noqa: E402

# ── 1. 執行宣稱 ────────────────────────────────────────────────────────────────

# (正則, 改寫成什麼)。順序有意義：長詞先換，避免「已執行完畢」被拆成兩次替換。
EXEC_CLAIM_RULES: list[tuple[str, str]] = [
    (r"已執行完畢", "條件已觸發，待確認"),
    (r"執行完畢", "待確認執行"),
    (r"(?:倉位|部位)(?:已)?(?:歸零|清零|為零|出清)", "倉位變動待確認"),
    (r"已(?:全數)?清倉", "清倉條件已觸發，待確認"),
    (r"已出場", "出場條件已觸發，待確認"),
    (r"已停損", "停損條件已觸發，待確認"),
    (r"已(?:執行)?停利", "停利條件已觸發，待確認"),
    (r"已減碼", "減碼條件已觸發，待確認"),
    (r"已加倉", "加倉條件已觸發，待確認"),
    (r"已建倉", "建倉條件已觸發，待確認"),
    (r"已(?:市價)?賣出", "賣出條件已觸發，待確認"),
    (r"已(?:市價)?買進", "買進條件已觸發，待確認"),
    (r"已執行", "條件已觸發，待確認"),
    (r"(?<![A-Za-z])executed(?![A-Za-z])", "條件已觸發（待確認）"),
]
_EXEC_ANY = re.compile("|".join(f"(?:{p})" for p, _ in EXEC_CLAIM_RULES))

_TICKER_TOKEN = re.compile(r"(?<![A-Za-z0-9\.])([A-Z]{1,5}(?:\.TW)?|\d{4,6}\.TW)(?![A-Za-z0-9])")

# 第三方主體做的事不是我們的成交。「Third Point Q2 已賣出 AVGO」被改寫成
# 「賣出條件已觸發，待確認」不只沒意義，還會把句子改壞——這裡列出常見的第三方主體，
# 出現在宣稱前 16 字內就跳過該處。（寧可漏標外部主體，也不可漏標自己的假執行。）
THIRD_PARTY_MARKERS = [
    "Third Point", "Berkshire", "Point72", "Citadel", "Bridgewater", "Buffett",
    "Wells Fargo", "Goldman", "Morgan", "Barchart", "Reuters", "Bloomberg",
    "機構", "基金", "大戶", "內部人", "insider", "Insider", "分析師", "外資",
    "投信", "董事", "執行長", "CEO", "創辦人", "公司", "管理層", "同業", "對手",
]
_THIRD_PARTY_RE = re.compile("|".join(re.escape(m) for m in THIRD_PARTY_MARKERS))
_LOOKBEHIND_CHARS = 16

# 沒指名 ticker 的宣稱：只有帶「自身部位」語境時才改寫，否則僅列入稽核不動文字。
_SELF_CONTEXT_RE = re.compile(
    r"部位|倉位|持倉|我方|本組合|確認執行|待執行|補執行|明日|次一交易日|停損|止損|"
    r"觸發|重進|加倉|減碼|建倉|出場|清倉|stop_?\s?loss|\btrim\b|\bexit\b",
    re.IGNORECASE,
)

_CLEANUP_RULES = [
    (r"待確認確認", "待確認"),
    (r"待確認，待確認", "待確認"),
    (r"條件已觸發，待確認，待確認", "條件已觸發，待確認"),
]


def _rewrite_claims(line: str) -> str:
    """逐個 match 改寫，跳過第三方主體。"""
    out = line
    for pattern, repl in EXEC_CLAIM_RULES:
        rx = re.compile(pattern)
        pieces: list[str] = []
        pos = 0
        for m in rx.finditer(out):
            before = out[max(0, m.start() - _LOOKBEHIND_CHARS):m.start()]
            pieces.append(out[pos:m.start()])
            if _THIRD_PARTY_RE.search(before):
                pieces.append(m.group(0))            # 第三方的動作，原樣保留
            else:
                pieces.append(m.expand(repl) if "\\" in repl else repl)
            pos = m.end()
        pieces.append(out[pos:])
        out = "".join(pieces)
    for pattern, repl in _CLEANUP_RULES:
        out = re.sub(pattern, repl, out)
    return out


def _tickers_in(line: str, known: set[str]) -> set[str]:
    return {m.group(1).upper() for m in _TICKER_TOKEN.finditer(line)} & known


def check_execution_claims(text: str, exec_state: dict | None,
                           known_tickers: set[str] | None = None) -> tuple[str, list[dict]]:
    """把沒有持倉對帳背書的「已執行」句子改寫掉。

    判定以**行**為單位：一行裡出現執行宣稱時，看同一行提到的 ticker 是否處於
    executed / reconciled；全部有背書就放行。沒指名 ticker 的宣稱只有在帶自身部位
    語境時才改寫（避免把「某某機構已賣出 X」這種第三方敘述改壞）。
    """
    if not text:
        return text, []
    decisions = ((exec_state or {}).get("decisions") or {})
    state_by_ticker: dict[str, str] = {}
    for entry in decisions.values():
        t = str(entry.get("ticker") or "").upper()
        st = entry.get("state")
        if t and (t not in state_by_ticker or st in EXECUTED_STATES):
            state_by_ticker[t] = st
    known = set(known_tickers or set()) | set(state_by_ticker)

    violations: list[dict] = []
    out_lines: list[str] = []
    for i, line in enumerate(text.split("\n"), start=1):
        if not _EXEC_ANY.search(line):
            out_lines.append(line)
            continue
        mentioned = _tickers_in(line, known)
        backed = {t for t in mentioned if state_by_ticker.get(t) in EXECUTED_STATES}
        if mentioned and backed == mentioned:
            out_lines.append(line)          # 每一檔都有對帳背書 → 放行
            continue
        if not mentioned and not _SELF_CONTEXT_RE.search(line):
            out_lines.append(line)          # 疑似第三方敘述 → 不動文字
            violations.append({
                "kind": "unverified_claim", "line": i, "tickers": [],
                "states": {}, "before": line.strip()[:180],
                "after": "（未改寫：無法判定主體，請自行確認）",
            })
            continue
        fixed = _rewrite_claims(line)
        if fixed == line:
            out_lines.append(line)
            continue
        unbacked = sorted(mentioned - backed) or ["（未指名 ticker）"]
        violations.append({
            "kind": "execution_claim",
            "line": i,
            "tickers": unbacked,
            "states": {t: STATE_ZH.get(state_by_ticker.get(t), "無此決策紀錄") for t in unbacked
                       if t != "（未指名 ticker）"},
            "before": line.strip()[:180],
            "after": fixed.strip()[:180],
        })
        out_lines.append(fixed)
    return "\n".join(out_lines), violations


# ── 2. 即時語氣 ───────────────────────────────────────────────────────────────

def _realtime_rules(trade_date: str | None) -> list[tuple[str, str]]:
    # 交易日不明時不要寫「該交易日 當日應執行」這種疊字，直接改成條件句
    td = trade_date
    on_day = f"{td} 當日應" if td else "條件成立時"
    return [
        (r"今日執行", on_day + "執行"),
        (r"今天執行", on_day + "執行"),
        (r"立即執行", "條件成立時執行"),
        (r"盤中執行", "盤中條件成立時執行"),
        (r"現在(買進|買入|加倉)", r"條件成立時\1"),
        (r"現在(賣出|減碼|出場)", r"條件成立時\1"),
        (r"今日(買進|賣出|加倉|減碼|停損|trim|add)", on_day + r"\1"),
        (r"明日開盤", "次一交易日開盤"),
        (r"明天開盤", "次一交易日開盤"),
        (r"明日操作", "次一交易日操作"),
        (r"明天操作", "次一交易日操作"),
        (r"^(\s*)明日：", r"\1次一交易日："),
    ]


def check_realtime_language(text: str, freshness: dict | None) -> tuple[str, list[dict]]:
    """事後補報／過期資料的報告不准使用即時買賣動詞。"""
    if not text or not freshness:
        return text, []
    if freshness.get("status") in (None, "LIVE"):
        return text, []
    rules = _realtime_rules(freshness.get("trade_date"))
    violations: list[dict] = []
    out_lines: list[str] = []
    for i, line in enumerate(text.split("\n"), start=1):
        fixed = line
        for pattern, repl in rules:
            fixed = re.sub(pattern, repl, fixed, flags=re.MULTILINE)
        if fixed != line:
            violations.append({
                "kind": "realtime_language",
                "line": i,
                "before": line.strip()[:180],
                "after": fixed.strip()[:180],
            })
        out_lines.append(fixed)
    return "\n".join(out_lines), violations


# ── 3. 佔位句 ─────────────────────────────────────────────────────────────────

PLACEHOLDER_PATTERNS = [
    r"（?原文未提供）?",
    r"請參考原文(?:連結)?",
    r"詳見原文",
    r"待補(?:充)?",
    r"\bTBD\b",
    r"資料待確認中",
    r"（無資料）",
]
_PLACEHOLDER_RE = re.compile("|".join(f"(?:{p})" for p in PLACEHOLDER_PATTERNS))


def check_placeholders(text: str, *, drop_lines: bool = False) -> tuple[str, list[dict]]:
    """佔位句掃描。drop_lines=True 時整行刪除（digest 那種表格列適用）。"""
    if not text:
        return text, []
    violations: list[dict] = []
    out_lines: list[str] = []
    for i, line in enumerate(text.split("\n"), start=1):
        if _PLACEHOLDER_RE.search(line):
            violations.append({"kind": "placeholder", "line": i, "before": line.strip()[:180],
                               "after": "（整行移除）" if drop_lines else line.strip()[:180]})
            if drop_lines:
                continue
        out_lines.append(line)
    return "\n".join(out_lines), violations


# ── 4. 無來源段落 ─────────────────────────────────────────────────────────────

def check_unsourced_sections(text: str, *, required: dict[str, bool]) -> list[dict]:
    """`required` = {段落標題關鍵字: 是否有真實資料}。沒資料卻有段落 → 回報。"""
    violations: list[dict] = []
    for keyword, has_data in (required or {}).items():
        if has_data:
            continue
        for i, line in enumerate(text.split("\n"), start=1):
            if keyword in line and line.lstrip().startswith(("#", "**", "▌", "###")):
                violations.append({
                    "kind": "unsourced_section", "line": i, "keyword": keyword,
                    "before": line.strip()[:180],
                    "after": f"無 {keyword} 原始資料，該段不應產生",
                })
                break
    return violations


# ── 總入口 ────────────────────────────────────────────────────────────────────

def apply_guards(text: str, *, exec_state: dict | None = None,
                 freshness: dict | None = None,
                 known_tickers: set[str] | None = None,
                 drop_placeholder_lines: bool = False) -> tuple[str, dict]:
    """依序套用所有守則，回傳 (改寫後文字, 稽核報告)。"""
    all_violations: list[dict] = []

    text, v = check_execution_claims(text, exec_state, known_tickers)
    all_violations += v
    text, v = check_realtime_language(text, freshness)
    all_violations += v
    text, v = check_placeholders(text, drop_lines=drop_placeholder_lines)
    all_violations += v

    audit = {
        "violations": all_violations,
        "counts": {},
        "clean": not all_violations,
    }
    for item in all_violations:
        audit["counts"][item["kind"]] = audit["counts"].get(item["kind"], 0) + 1
    return text, audit


_KIND_ZH = {
    "execution_claim": "未經對帳的「已執行」宣稱",
    "realtime_language": "事後補報卻用即時語氣",
    "placeholder": "佔位句（沒資料就不該寫）",
    "unsourced_section": "無原始資料的段落",
    "unverified_claim": "無法驗證的執行敘述（未改寫）",
}


def render_audit_summary(audit: dict) -> str:
    """一行式（放報告最上方，不佔版面；明細留給附錄）。"""
    v = (audit or {}).get("violations") or []
    if not v:
        return "> ✅ 產出驗證：未偵測到未經對帳的執行宣稱、即時語氣誤用或佔位句。"
    bits = "、".join(f"{_KIND_ZH.get(k, k)} {n} 處"
                    for k, n in (audit.get("counts") or {}).items())
    return (f"> ⚠️ **產出驗證：{bits}，已由程式改寫**"
            "（原句與改寫對照見附錄「產出驗證明細」）。")


def render_audit_md(audit: dict) -> str:
    """把稽核結果寫進報告，讓讀者看得到「哪些句子被程式改過」。"""
    v = (audit or {}).get("violations") or []
    if not v:
        return "> ✅ 產出驗證：未偵測到未經對帳的執行宣稱、即時語氣誤用或佔位句。"
    lines = [
        "> ⚠️ **產出驗證：以下敘述與可驗證資料不符，已由程式改寫**"
        f"（共 {len(v)} 處）。原句保留在下方供稽核。",
        "",
        "| # | 類型 | 標的 | 原句 → 改寫 |",
        "|---|------|------|-------------|",
    ]
    for i, item in enumerate(v[:20], start=1):
        tickers = ", ".join(item.get("tickers") or []) or "—"
        before = item["before"].replace("|", "／")
        after = item["after"].replace("|", "／")
        lines.append(f"| {i} | {_KIND_ZH.get(item['kind'], item['kind'])} | {tickers} | `{before}` → `{after}` |")
    if len(v) > 20:
        lines.append(f"| … | 另有 {len(v) - 20} 處 | | |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="報告產出驗證 — 執行宣稱／時態／佔位句")
    ap.add_argument("path", help="要檢查的 markdown 報告")
    args = ap.parse_args()
    from scripts.exec_state import load_state

    raw = Path(args.path).read_text(encoding="utf-8")
    fixed, audit = apply_guards(raw, exec_state=load_state())
    print(render_audit_md(audit))
    print()
    print(f"（{len(audit['violations'])} 處；未寫回檔案，僅檢查）")


if __name__ == "__main__":
    main()
