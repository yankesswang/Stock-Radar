#!/usr/bin/env python3
"""把財報筆記的結論抽成結構化欄位，存進 earnings_registry.db 的 earnings_conclusions。

筆記本身是給人讀的長文，App 只能整篇渲染，掃不了「這季我到底決定做什麼」。
這支腳本走一遍所有筆記，把每季的可操作結論拆出來：

    30 秒結論 / 操作動作 / 動作分類 / 停損價 / 部位權重 / 財報後反應

筆記格式跨了好幾代（章節編號 1. 與 一、混用、舊筆記沒有 [!summary] callout、
只有三成寫了明確的「動作：」行），所以抽取一律「能確定才寫」：抽不到的欄位留空並
記進 gaps，總覽頁照 gaps 標示待補，不猜、不硬湊。

讀鏡像（~/.arthur-digest/earnings_notes/vault-mirror）而不是 ~/Documents：
macOS TCC 會擋 launchd 起的行程讀 vault，同 earnings_notes.py 的處理。

用法:
  extract_conclusions.py                 全部重抽（既有列以 ticker+quarter 覆蓋）
  extract_conclusions.py --ticker WDAY   只抽一檔
  extract_conclusions.py --dry-run       只印結果不寫庫
  extract_conclusions.py --gaps          印出各筆記缺哪些欄位（校正 parser 用）
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import sqlite3
from datetime import datetime

DB = pathlib.Path(__file__).parents[2] / "data" / "earnings_registry.db"
MIRROR = pathlib.Path(os.environ.get(
    "EARNINGS_NOTES_DIR", "~/.arthur-digest/earnings_notes/vault-mirror")).expanduser()
VAULT = pathlib.Path("~/Documents/arthurwang_DB/投資/財報筆記").expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS earnings_conclusions (
  ticker TEXT,
  quarter TEXT,
  report_date TEXT,
  industry TEXT,
  company TEXT,
  note_path TEXT,
  note_title TEXT,
  summary TEXT,
  stance TEXT,
  action_text TEXT,
  action_source TEXT,
  action_candidates TEXT,
  stop_loss REAL,
  stop_loss_note TEXT,
  weight_pct REAL,
  reaction_pct REAL,
  price_after_earnings REAL,
  conclusion_md TEXT,
  conclusion_hash TEXT,
  gaps TEXT,
  extracted_at TEXT,
  PRIMARY KEY (ticker, quarter)
);
"""

# 動作分類：先比對到的先贏。否定式（不進場／不加碼）必須排在肯定式前面，
# 否則「不進場」會被 add 的「進場」吃掉、「續抱不加碼」會被判成加碼
STANCE_RULES = [
    ("avoid",  ["不進場", "不買進", "不參與", "不建倉", "略過"]),
    # 「不加也不減」是 AAPL 那種滿倉觀望的標準講法，漏了會被下面的 add 吃掉。
    # 不用單獨的「不動」當關鍵字，那會誤中房建筆記裡的「不動產」
    ("hold",   ["續抱不加碼", "不加碼", "不加也不減", "不加不減", "維持不動",
                "續抱", "持有不動", "按兵不動", "維持部位"]),
    ("exit",   ["出清", "全數賣出", "清倉", "認賠出場"]),
    ("trim",   ["減碼", "減倉", "調降權重", "減到等權", "回到等權"]),
    ("add",    ["加碼", "買進", "進場", "試單", "分批進", "建倉"]),
    # 「不追高」「不必急」是筆記常用的婉轉說法，實質是觀望；放最後一組才不會
    # 吃掉「加碼到等權，但不追價」這種真的要動手的句子
    ("watch",  ["觀察", "觀望", "等訊號", "等待", "不追高", "不追", "不必急", "不急"]),
]
STANCE_LABELS = {
    "add": "加碼", "hold": "續抱", "trim": "減碼", "exit": "出清",
    "avoid": "不進場", "watch": "觀察", "": "未分類",
}


def ensure_schema(con: sqlite3.Connection) -> None:
    """建表，並補上舊庫缺的欄位。

    CREATE TABLE IF NOT EXISTS 對已存在的表不會加新欄位，加欄位時如果不補這一段，
    寫入會直接炸在 "table has no column named ..."。
    """
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(earnings_conclusions)")}
    for line in SCHEMA.splitlines():
        parts = line.strip().rstrip(",").split()
        if len(parts) == 2 and parts[1] in ("TEXT", "REAL") and parts[0] not in have:
            con.execute(f"ALTER TABLE earnings_conclusions ADD COLUMN {parts[0]} {parts[1]}")


def _root() -> pathlib.Path:
    return MIRROR if MIRROR.exists() else VAULT


def parse_frontmatter(text: str) -> dict:
    """YAML 前置資料；只取純量鍵，list 值（tags/sources）跳過。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.startswith((" ", "\t", "-")):
            continue                                  # 縮排行＝上一個 key 的 list 項目
        k, sep, v = line.partition(":")
        if sep and v.strip():
            fm[k.strip()] = v.strip()
    return fm


def split_sections(body: str, level: int = 2) -> list[tuple[str, str]]:
    """切成 (標題, 內容)；筆記章節編號有 `1.`／`一、`／無編號三種寫法。"""
    parts = re.split(rf"^#{{{level}}}\s+(.+)$", body, flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts), 2)]


def find_conclusion(sections: list[tuple[str, str]]) -> tuple[str, str]:
    """結論章節：標題含「結論」或「操作」的最後一節（有些筆記多節提到結論）。"""
    hits = [(h, b) for h, b in sections if "結論" in h or "操作" in h]
    return hits[-1] if hits else ("", "")


def extract_summary(body: str) -> str:
    """> [!summary] 30 秒結論 的 callout 內容（35/46 篇有，舊筆記沒寫）。"""
    m = re.search(r"^>\s*\[!summary\][^\n]*\n((?:>.*\n?)+)", body, flags=re.M)
    if not m:
        return ""
    return "\n".join(re.sub(r"^>\s?", "", ln) for ln in m.group(1).splitlines()).strip()


def _clean(s: str) -> str:
    return s.strip().lstrip("->*# ").strip().strip("*").strip()


# 「動作：」出現在行首、條列裡，或摘要句中間（WDAY 是「…核心倉。動作：續抱不加碼」）。
# 前面必須是句子邊界，否則「其他策略動作：」的尾綴會被當成操作動作（AMKR 踩過）
_ACTION_RE = re.compile(r"(?:^|[。；)】」]|\*\*)\s*[-*>]?\s*\**(?:主)?動作\**\s*[：:]\s*(.*)",
                        flags=re.M)


def extract_action(conclusion: str, summary: str, whole: str) -> tuple[str, str]:
    """(操作動作原句, 來源)。精度優先：抽不到就留空給 gaps，不用亂猜的句子填。

    來源決定總覽頁怎麼呈現：explicit 是筆記自己寫死的動作，section 是從
    「操作結論」小節取的第一段，兩者可以照字面信；空的就只有 stance 是推斷的。
    """
    for src in (conclusion, summary, whole):
        if not src:
            continue
        m = _ACTION_RE.search(src)
        if not m:
            continue
        rest = m.group(1).strip()
        if not rest:                                  # 「動作：」後換行接條列
            tail = [ln for ln in src[m.end():].splitlines() if ln.strip()]
            return (_clean(tail[0])[:160] if tail else ""), "explicit"
        return _clean(re.split(r"(?<=。)", rest)[0])[:160], "explicit"

    # 沒有明確動作行時，退而求其次：標題含「操作」的 H3 小節裡第一句帶動作字眼的話。
    # 不能直接拿第一行——那常是「部位狀態：試單／偏低配（等權 0.62 倍）」這種
    # 現況標籤，抽出來會被當成「這季決定加碼」，比留白還糟
    for title, body in split_sections(conclusion, level=3):
        if "操作" not in title:
            continue
        # 逐「句」而不是逐「行」：條列的第一行常常是一整段（動作寫在句首、後面接
        # 一長串理由），用整行長度篩會把正確答案篩掉（AAPL 的「維持 3 股不動，
        # 不加也不減。」就藏在一行 200 字的開頭）
        for ln in body.splitlines():
            for sent in re.split(r"(?<=[。；])", ln):
                c = _clean(sent)
                if 4 < len(c) < 160 and any(k in c for k in _STANCE_KWS) \
                        and not c.startswith(("部位狀態", "估值", "持倉現況", "現況")):
                    return c[:160], "section"
    return "", ""


def classify(action: str) -> str:
    """只從明確寫下的動作句分類。

    早期版本會退回去掃結論長文的關鍵字，結果錯得很難看：NVDA 寫的是「兩處曝險都
    續抱，不加倉」卻被判成加碼，META 同一節裡「媽媽帳戶加碼」與「主帳戶維持不進場」
    兩句並存、位置規則救不了。長文推斷一律不做，缺的走 action_candidates 補。
    """
    if not action:
        return ""
    for stance, kws in STANCE_RULES:
        if any(kw in action for kw in kws):
            return stance
    return ""


_STANCE_KWS = [k for _, kws in STANCE_RULES for k in kws]


def action_candidates(conclusion: str) -> list[str]:
    """結論段裡帶動作字眼的句子，給沒有明確動作行的筆記當補件線索（表格行排除）。"""
    prose = "\n".join(l for l in conclusion.splitlines() if not l.strip().startswith("|"))
    out = []
    for sent in re.split(r"(?<=[。\n])", prose):
        s = _clean(sent)
        if 6 < len(s) < 130 and any(k in s for k in _STANCE_KWS) and s not in out:
            out.append(s)
    return out[:4]


# 停損價：數字要在「停損」右邊 30 字內。這一段全是雜訊，必須逐條擋掉：
#   stop_2atr        底線後的 2 不是價位
#   1.5 到 2 倍 ATR   倍數不是價位
#   3% 試單約 $1,477  百分比與股數不是價位
# 擋不完的用價格合理性把關（見 extract_stop_loss）
_STOP_RE = re.compile(r"停損[^%\n]{0,30}?(\$?)\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
_BAD_TAIL = ("%", "％", "倍", "×", "x", "股", "到", "年", "月", "日", "檔", "次")


def extract_stop_loss(conclusion: str, whole: str,
                      price: float | None) -> tuple[float | None, str]:
    """(停損價, 出處原句)。停損必須落在現價下方 0.5～1.0 倍。

    技術面章節有一句樣板「停損要放在 1.5 到 2 倍 ATR 以外，也就是 $X 到 $Y」，講的是
    假設性的進場停損、常高於現價；價格區間把關就是用來把這種句子擋掉的。
    """
    best = None
    for text in (conclusion, whole):
        if not text:
            continue
        for m in _STOP_RE.finditer(text):
            if text[max(0, m.start(2) - 1):m.start(2)] == "_":
                continue                              # stop_2atr 的 2
            if text[m.end():m.end() + 1].strip()[:1] in _BAD_TAIL:
                continue
            try:
                v = float(m.group(2).replace(",", ""))
            except ValueError:
                continue
            if v <= 1:
                continue
            if price is not None and not (0.5 * price <= v < price):
                continue
            ctx = _clean(text[max(0, m.start() - 60):m.end() + 40].replace("\n", " "))
            if m.group(1) == "$":                     # 明確標價，直接採用
                return v, ctx
            best = best or (v, ctx)
        if best:
            return best
    return best or (None, "")


# 權重：只認「權重 X%」與「佔組合 X%」，但「現金佔組合 18%」講的是現金不是部位
_WEIGHT_RE = re.compile(r"(.{0,10})(?:權重|佔組合|占組合)[^0-9\n]{0,6}([0-9]+(?:\.[0-9]+)?)\s*%")


def extract_weight(conclusion: str) -> float | None:
    for m in _WEIGHT_RE.finditer(conclusion or ""):
        if any(w in m.group(1) for w in ("現金", "產業", "半導體", "軟體", "合計", "等權")):
            continue
        return float(m.group(2))
    return None


def _lead_float(s: str) -> float | None:
    m = re.match(r"\s*\$?([0-9][0-9,]*(?:\.[0-9]+)?)", s or "")
    return float(m.group(1).replace(",", "")) if m else None


def extract_reaction(fm: dict) -> float | None:
    """財報後股價反應。frontmatter 有 reaction_pct 就用，否則從 price 描述句撈。"""
    for k in ("reaction_pct", "reaction_next_day_pct", "gap_open_pct"):
        if k in fm:
            m = re.search(r"[-−+]?[0-9]+(?:\.[0-9]+)?", fm[k])
            if m:
                return float(m.group(0).replace("−", "-"))
    m = re.search(r"([+-−][0-9]+(?:\.[0-9]+)?)\s*%", fm.get("price_after_earnings", ""))
    return float(m.group(1).replace("−", "-")) if m else None


def extract(note: pathlib.Path) -> dict:
    text = note.read_text(encoding="utf-8", errors="replace")
    fm = parse_frontmatter(text)
    body = text.split("\n---", 1)[1] if text.startswith("---") else text
    sections = split_sections(body)
    _, conclusion = find_conclusion(sections)

    qdir = note.parent
    summary = extract_summary(body)
    action, action_source = extract_action(conclusion, summary, body)
    price = _lead_float(fm.get("price_after_earnings", ""))
    stop_loss, stop_loss_note = extract_stop_loss(conclusion, body, price)
    row = {
        "ticker": (fm.get("ticker") or qdir.parent.name).upper(),
        "quarter": qdir.name,
        "report_date": fm.get("report_date", ""),
        "industry": qdir.parent.parent.name,
        "company": fm.get("company", ""),
        "note_path": str(note.relative_to(_root())),
        "note_title": note.stem,
        "summary": summary,
        "action_text": action,
        "action_source": action_source,
        "stance": classify(action),
        "action_candidates": json.dumps(
            [] if action else action_candidates(conclusion), ensure_ascii=False),
        "stop_loss": stop_loss,
        "stop_loss_note": stop_loss_note,
        "weight_pct": extract_weight(conclusion),
        "reaction_pct": extract_reaction(fm),
        "price_after_earnings": price,
        "conclusion_md": conclusion.strip(),
    }
    row["conclusion_hash"] = hashlib.sha256(
        conclusion.strip().encode("utf-8")).hexdigest()[:16]
    row["gaps"] = _gaps(row)
    row["extracted_at"] = datetime.now().isoformat(timespec="seconds")
    return row


GAP_FIELDS = ("summary", "action_text", "stance", "stop_loss", "conclusion_md")


def _gaps(row: dict) -> str:
    return json.dumps([k for k in GAP_FIELDS if not row.get(k)], ensure_ascii=False)


def carry_over_llm(rows: list[dict]) -> int:
    """把上一輪 LLM 補的動作接回來，條件是結論段一個字都沒變。

    沒有這一步，每天的 cron 重跑一次抽取器就會把 27 篇 LLM 補的結果洗成空白，
    總覽頁隔天自己退化回半殘。結論段改過的筆記不接（雜湊不同），讓它重新補跑。
    """
    if not DB.exists():
        return 0
    con = sqlite3.connect(DB)
    ensure_schema(con)
    con.commit()
    con.row_factory = sqlite3.Row
    try:
        prev = {(r["ticker"], r["quarter"]): dict(r) for r in
                con.execute("SELECT * FROM earnings_conclusions")}
    except sqlite3.Error:
        return 0
    finally:
        con.close()

    n = 0
    for r in rows:
        old = prev.get((r["ticker"], r["quarter"]))
        if (not r["action_text"] and old and old.get("action_source") == "llm"
                and old.get("action_text")
                and old.get("conclusion_hash") in (None, "", r["conclusion_hash"])):
            r.update(action_text=old["action_text"], stance=old["stance"],
                     action_source="llm", action_candidates="[]")
            r["gaps"] = _gaps(r)
            n += 1
    return n


LLM_PROMPT = """你在讀一份財報筆記的「結論」章節。請只根據下面這段文字，回答這一季對這檔股票的操作決定。

只輸出一行 JSON，不要有其他文字、不要 markdown 圍欄：
{"action": "<照抄或濃縮筆記裡的操作決定，40 字內，繁體中文>", "stance": "<add|hold|trim|exit|avoid|watch>"}

stance 的意思：add=加碼或建倉、hold=續抱不動、trim=減碼、exit=出清、avoid=不進場（未持有且這季不買）、watch=只觀察等訊號。
筆記若把不同帳戶分開講（例如主帳戶不進場、媽媽帳戶加碼），action 要把兩邊都寫進去，stance 取主帳戶的。
文字裡若真的找不到操作決定，輸出 {"action": "", "stance": ""}。

---
%s
---"""


def llm_backfill(rows: list[dict], verbose: bool = True) -> int:
    """用 claude CLI 補上抽不到的操作動作。

    regex 對舊筆記無能為力（結論寫在長文裡、一節內多個帳戶各有決定），這條路徑
    讓 LLM 只做「讀結論段、回一行 JSON」這一件事，回寫時標 action_source=llm，
    總覽頁會跟筆記自己寫死的動作分開顯示。
    """
    import subprocess
    todo = [r for r in rows if not r["action_text"] and r["conclusion_md"]]
    filled = 0
    for i, r in enumerate(todo, 1):
        if verbose:
            print(f"  [{i}/{len(todo)}] {r['ticker']} {r['quarter']} …", end="", flush=True)
        try:
            out = subprocess.run(
                ["claude", "-p", LLM_PROMPT % r["conclusion_md"][:6000]],
                capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f" 失敗（{type(e).__name__}）")
            continue
        m = re.search(r"\{.*\}", out.stdout, flags=re.S)
        if not m:
            print(" 沒回 JSON")
            continue
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            print(" JSON 壞掉")
            continue
        action = (data.get("action") or "").strip()
        stance = (data.get("stance") or "").strip()
        if not action or stance not in STANCE_LABELS:
            print(" 空手而回")
            continue
        r.update(action_text=action[:160], stance=stance, action_source="llm",
                 action_candidates="[]")
        r["gaps"] = _gaps(r)
        filled += 1
        if verbose:
            print(f" {STANCE_LABELS[stance]}｜{action[:40]}")
    return filled


def notes_for(ticker: str | None) -> list[pathlib.Path]:
    root = _root()
    if not root.exists():
        return []
    pat = f"*/{ticker.upper()}/*/*財報筆記.md" if ticker else "*/*/*/*財報筆記.md"
    return sorted(root.glob(pat))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--gaps", action="store_true", help="印出每篇缺的欄位")
    ap.add_argument("--llm-backfill", action="store_true",
                    help="抽不到操作動作的筆記，交給 claude CLI 讀結論段補上")
    a = ap.parse_args()

    notes = notes_for(a.ticker)
    if not notes:
        print(f"no notes found under {_root()}")
        return
    rows = [extract(n) for n in notes]
    carried = carry_over_llm(rows)
    if carried:
        print(f"沿用上一輪 LLM 補的動作 {carried} 篇（結論段未變動）")
    if a.llm_backfill:
        print(f"LLM 補跑 {sum(1 for r in rows if not r['action_text'])} 篇：")
        print(f"補上 {llm_backfill(rows)} 篇\n")

    if not a.dry_run:
        con = sqlite3.connect(DB)
        ensure_schema(con)
        cols = list(rows[0].keys())
        con.executemany(
            f"INSERT OR REPLACE INTO earnings_conclusions ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [tuple(r[c] for c in cols) for r in rows])
        con.commit()
        con.close()

    filled = {k: sum(1 for r in rows if r.get(k)) for k in
              ("summary", "action_text", "stance", "stop_loss", "weight_pct", "conclusion_md")}
    for src in ("explicit", "section", "llm"):
        filled[f"  └ {src}"] = sum(1 for r in rows if r["action_source"] == src)
    print(f"{len(rows)} notes -> {'(dry-run)' if a.dry_run else DB}")
    for k, v in filled.items():
        print(f"  {k:<16} {v}/{len(rows)}")
    if a.gaps:
        print("\n缺欄位的筆記：")
        for r in rows:
            g = json.loads(r["gaps"])
            if g:
                print(f"  {r['ticker']:<6} {r['quarter']:<12} 缺 {', '.join(g)}")


if __name__ == "__main__":
    main()
