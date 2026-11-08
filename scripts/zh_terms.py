#!/usr/bin/env python3
"""術語中文化 —— 報告落地前把英文交易黑話換成中文。

為什麼要有這一層：
  DECISIONS 的 schema 是英文 enum（`action=trim` / `risk_level=high` / 狀態機 `acknowledged`），
  程式與模型都直接把 enum 值印進人看的報告，於是首頁會出現「明日：trim」「（risk=high）」
  「狀態 **acknowledged**」「與宏觀 risk-off 脫鉤」這種半英半中的句子。
  enum 本身要留著（journal / 狀態機 / 前端都靠它），**但使用者看到的那一面不該有英文黑話**。

分兩層處理：
  1. 結構化欄位 —— 呼叫端用 `zh_action()` / `zh_risk()` / `zh_regime()` 直接印中文（首選）。
  2. 模型自由書寫的段落 —— 用 `localize()` 掃一遍替換（收尾網，模型不照 prompt 寫時的保險）。

`localize()` 的安全邊界（避免把資料改壞）：
  - 反引號行內程式碼、``` 圍籬區塊、URL、JSON 鍵值對一律不動
    （`action=open`、`data/agent/journal/...`、`"action": "trim"` 都要保持原樣）。
  - 只比對**小寫**單字，所以不會誤傷股票代號（NOW / C / ADD）與評級（Buy / Sell / Outperform）。
  - 單字後面若接著另一個英文小寫單字就跳過（`open interest`、`add to position` 不是動作指令）。
"""
from __future__ import annotations

import re
from pathlib import Path

# ── 結構化欄位對照 ────────────────────────────────────────────────────────────

ACTION_ZH = {
    "add": "加倉", "open": "建倉", "buy": "買進", "accumulate": "分批買進",
    "trim": "減碼", "reduce": "減碼", "exit": "出場", "stop_loss": "停損",
    "sell": "賣出", "sell_short": "放空", "hold": "維持",
    "watch": "觀察", "avoid": "迴避",
}

RISK_ZH = {"low": "低", "medium": "中", "high": "高"}

REGIME_ZH = {
    "risk_on": "風險偏好", "risk-on": "風險偏好", "riskon": "風險偏好",
    "risk_off": "避險", "risk-off": "避險", "riskoff": "避險",
    "mixed": "多空分歧", "neutral": "中性",
    "bullish": "偏多", "bearish": "偏空",
}

SETUP_ZH = {
    "oversold_bounce": "超賣反彈",
    "undervalued_catalyst": "低估＋催化劑",
    "momentum_breakout": "動能突破",
    "mimi_entry": "Mimi 進場區",
    "breakdown_short": "破位放空",
    "rs_leader": "強勢回檔",
}


def zh_action(action: str | None, fallback: str = "—") -> str:
    key = str(action or "").strip().lower()
    return ACTION_ZH.get(key, key or fallback)


def zh_risk(level: str | None, fallback: str = "—") -> str:
    key = str(level or "").strip().lower()
    return RISK_ZH.get(key, key or fallback)


def zh_regime(regime: str | None, fallback: str = "—") -> str:
    key = str(regime or "").strip().lower().replace(" ", "_")
    return REGIME_ZH.get(key, str(regime or "").strip() or fallback)


def zh_setup(setup: str | None, fallback: str = "訊號") -> str:
    key = str(setup or "").strip().lower()
    return SETUP_ZH.get(key, key or fallback)


# ── 自由書寫段落的收尾網 ──────────────────────────────────────────────────────

# 不進替換的區段：``` 圍籬 / 行內程式碼 / URL / JSON 鍵值對。
_PROTECTED_RE = re.compile(
    r"```.*?```"                      # 圍籬區塊
    r"|`[^`\n]*`"                     # 行內程式碼
    r"|https?://\S+"                  # 網址
    r"|\"[A-Za-z_][A-Za-z0-9_]*\"\s*:\s*\"[^\"\n]*\"",  # "action": "trim"
    re.DOTALL,
)


def _word(token: str) -> str:
    """英文小寫單字：前後不得黏著英文，後面也不得接另一個英文小寫單字。

    前面允許數字（模型會寫「RSI未達75trim門檻」），後面不允許——`75trim` 要換，
    `trim20` 這種可能是代號或編號的形式則不動。斜線連寫（`trim/exit`）交給下面的
    連鎖規則處理，這裡一律避開，免得把 `backend/app/...` 這種路徑改壞。
    """
    return (r"(?<![A-Za-z_\-/.])" + token
            + r"(?![A-Za-z0-9_\-/]|\s+[a-z]{2,})")


# 順序有意義：片語先換，單字後換。
_RULES: list[tuple[re.Pattern, object]] = []


def _add_rule(pattern: str, repl, flags: int = 0) -> None:
    _RULES.append((re.compile(pattern, flags), repl))


# 1) 片語 / 複合詞（先換掉，免得被單字規則拆散）
_add_rule(r"(?<![A-Za-z_])risk[\s_-]?on(?![A-Za-z_])", "風險偏好")
_add_rule(r"(?<![A-Za-z_])risk[\s_-]?off(?![A-Za-z_])", "避險情緒")
_add_rule(r"(?<![A-Za-z_])risk[\s_]?level(?![A-Za-z_])", "風險等級")
_add_rule(r"(?<![A-Za-z_])market[\s_]?regime(?![A-Za-z_])", "市場定性")
_add_rule(r"(?<![A-Za-z_])stop[\s_-]?loss(?:es)?(?![A-Za-z_])", "停損")
_add_rule(r"(?<![A-Za-z_])take[\s_-]?profit(?![A-Za-z_])", "停利")
_add_rule(r"(?<![A-Za-z_])sell[\s_-]?short(?![A-Za-z_])", "放空")
_add_rule(r"(?<![A-Za-z_])insider[\s_-]selling(?![A-Za-z_])", "內部人賣股", re.IGNORECASE)
_add_rule(r"(?<![A-Za-z_])short squeeze(?![A-Za-z_])", "軋空", re.IGNORECASE)
_add_rule(r"(?<![A-Za-z_])entry zone(?![A-Za-z_])", "進場區", re.IGNORECASE)
_add_rule(r"(?<![A-Za-z_])(?:price\s)?target(?![A-Za-z_])\s*價?", "目標價", re.IGNORECASE)
_add_rule(r"(?<![A-Za-z_])dead cross(?![A-Za-z_])", "死亡交叉", re.IGNORECASE)
_add_rule(r"(?<![A-Za-z_])golden cross(?![A-Za-z_])", "黃金交叉", re.IGNORECASE)

# 2) 風險／信心等級：只在有語境時才把 high/medium/low 換成 高/中/低
_add_rule(r"(?<![A-Za-z_])risk\s*[=:＝：]\s*(high|medium|low)(?![A-Za-z_])",
          lambda m: "風險：" + RISK_ZH[m.group(1).lower()])
_add_rule(r"(風險等級|風險|信心|conviction)([^A-Za-z\n]{0,6})(high|medium|low)(?![A-Za-z_])",
          lambda m: ("信心" if m.group(1) == "conviction" else m.group(1))
                    + m.group(2) + RISK_ZH[m.group(3).lower()])

# 3) 動作 enum（小寫才換，股票代號 NOW / C / ADD 不受影響）
for _en, _zh in [
    ("accumulate", "分批買進"), ("stop_loss", "停損"),
    ("trim", "減碼"), ("reduce", "減碼"), ("exit", "出場"), ("open", "建倉"),
    ("hold", "維持"), ("add", "加倉"), ("buy", "買進"), ("sell", "賣出"),
    ("watch", "觀察"), ("avoid", "迴避"),
]:
    _add_rule(_word(_en), _zh)

# 4) 執行狀態機
for _en, _zh in [
    ("recommended", "已建議"), ("triggered", "條件已觸發"), ("acknowledged", "已回應"),
    ("executed", "已執行"), ("reconciled", "已對帳"), ("abandoned", "已放棄"),
    ("expired", "已過期"), ("pending", "待確認"),
]:
    _add_rule(_word(_en), _zh)

# 5) 其餘常見黑話
for _en, _zh in [
    ("conviction", "信心"), ("thesis", "論點"), ("catalyst", "催化劑"),
    ("catalysts", "催化劑"), ("setup", "型態"), ("guidance", "財測指引"),
    ("guide", "財測指引"), ("buyback", "庫藏股"), ("drawdown", "回撤"),
    ("breakout", "突破"), ("breakdown", "破位"), ("pullback", "回檔"),
    ("momentum", "動能"), ("oversold", "超賣"), ("overbought", "超買"),
    ("bullish", "偏多"), ("bearish", "偏空"), ("neutral", "中性"),
    ("mixed", "多空分歧"), ("overweight", "加重"), ("underweight", "減重"),
    ("upside", "上檔空間"), ("downside", "下檔風險"), ("watchlist", "觀察名單"),
    ("regime", "市場定性"), ("backend", "後端"), ("insider", "內部人"),
    ("rebalance", "再平衡"), ("valuation", "估值"), ("scan", "掃描"),
]:
    _add_rule(_word(_en), _zh)


# 6) 斜線連寫（`trim/exit/停損`、`hold/watch`、`已觸發/acknowledged`）
#    單字規則刻意避開斜線——否則 `backend/app/routers` 這種路徑會被改壞。這裡只在
#    「整串都是黑話或中文」時才動手，且至少要有一個英文黑話才值得改。
_CHAIN_WORDS: dict[str, str] = {}
for _en, _zh in [
    ("stop_loss", "停損"), ("accumulate", "分批買進"), ("trim", "減碼"), ("reduce", "減碼"),
    ("exit", "出場"), ("open", "建倉"), ("hold", "維持"), ("add", "加倉"), ("buy", "買進"),
    ("sell", "賣出"), ("watch", "觀察"), ("avoid", "迴避"),
    ("recommended", "已建議"), ("triggered", "條件已觸發"), ("acknowledged", "已回應"),
    ("executed", "已執行"), ("reconciled", "已對帳"), ("abandoned", "已放棄"),
    ("expired", "已過期"), ("pending", "待確認"),
]:
    _CHAIN_WORDS[_en] = _zh

_CHAIN_PART = r"(?:" + "|".join(sorted(_CHAIN_WORDS, key=len, reverse=True)) + r"|[一-鿿]{1,6})"
_CHAIN_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])(" + _CHAIN_PART + r"(?:\s*/\s*" + _CHAIN_PART + r")+)(?![A-Za-z_])"
)


def _chain_sub(m: re.Match) -> str:
    parts = [p.strip() for p in m.group(1).split("/")]
    if not any(p in _CHAIN_WORDS for p in parts):
        return m.group(0)          # 純中文連寫，不用動
    return "/".join(_CHAIN_WORDS.get(p, p) for p in parts)


_RULES.append((_CHAIN_RE, _chain_sub))


def _apply(chunk: str) -> str:
    for pattern, repl in _RULES:
        chunk = pattern.sub(repl, chunk)
    return chunk


def localize(text: str) -> str:
    """把自由書寫段落裡的英文黑話換成中文；程式碼／URL／JSON 原樣保留。"""
    if not text:
        return text
    out: list[str] = []
    pos = 0
    for m in _PROTECTED_RE.finditer(text):
        out.append(_apply(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_apply(text[pos:]))
    return "".join(out)


def _main() -> None:
    """CLI：管道模式抽查，或就地改寫既有報告。

        uv run python scripts/zh_terms.py < 報告.md            # 印到 stdout
        uv run python scripts/zh_terms.py ~/.arthur-digest/stock/*/*.md        # 只列差異
        uv run python scripts/zh_terms.py --write ~/.arthur-digest/stock/*/*.md  # 就地改寫
    """
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="報告術語中文化")
    ap.add_argument("paths", nargs="*", help="要處理的 markdown；省略則讀 stdin")
    ap.add_argument("--write", action="store_true", help="就地改寫（預設只列差異）")
    args = ap.parse_args()

    if not args.paths:
        sys.stdout.write(localize(sys.stdin.read()))
        return

    changed = 0
    for raw in args.paths:
        path = Path(raw)
        try:
            before = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"⚠️  {path}：{e}")
            continue
        after = localize(before)
        if after == before:
            continue
        changed += 1
        diff = sum(1 for a, b in zip(before.split("\n"), after.split("\n")) if a != b)
        print(f"{'改寫' if args.write else '待改'} {path}（{diff} 行）")
        if args.write:
            path.write_text(after, encoding="utf-8")
    print(f"—— 共 {changed} 份{'已改寫' if args.write else '需要改寫（加 --write 才會動檔案）'}")


if __name__ == "__main__":
    _main()
