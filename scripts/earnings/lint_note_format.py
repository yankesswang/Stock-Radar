#!/usr/bin/env python3
"""財報筆記排版檢查：擠成一團的長段落抓出來，逼筆記維持條列式。

背景（2026-09-07）：FSLY／WDAY 2026Q2 兩篇 headless 生成的筆記把 30 秒結論寫成
一整段 400 字、內文 67 個段落有 41 個超過 120 字、條列只剩 19 行，跟 META 範本
（72 條列、19 段落）完全兩個樣子。SKILL 原本只寫「條列式」三個字，LLM 不當回事，
所以把規則量化成這支腳本能判的東西，寫完必跑，FAIL 就重排。

規則（對應 SKILL 格式鐵律 1a 到 1e）：
  S1  30 秒結論 callout 每一點獨立一行 `> - `，一行不超過 SUMMARY_MAX 字
  P1  非條列的純段落超過 PROSE_MAX 字 WARN、超過 PROSE_HARD 字 FAIL；
      WARN 等級的長段落全篇超過 PROSE_LONG_ALLOWED 段也 FAIL
      （圖說、表格前後提醒、小節開頭總結才准用段落，其餘說明都走條列）
  B1  條列一點超過 BULLET_MAX 字 WARN、超過 BULLET_HARD 字 FAIL（拆兩點或加子條列）
  E1  「第一，…第二，…」「(1)…(2)…」擠在同一行＝FAIL（引用區內 WARN）
  R1  全篇條列行數 ÷（條列＋純段落）低於 BULLET_RATIO_MIN 就 FAIL
  Q1  Q&A blockquote 一行超過 QUOTE_MAX 字 WARN
  L1  條列每一點開頭要先給重點（6 到 20 字的判斷句，接「：」或「，」再給數字），
      只是把長段落按句子切行不算條列。有重點引導的條列低於 LEAD_RATIO_MIN 就 FAIL
  I1  官方文件截圖：至少嵌一張檔名含「官方」的圖（EDGAR 財務表截圖或 IR 簡報頁），
      只有自製圖等於沒有原始財報，FAIL
  ── 以下只套用在 frontmatter 有 `format: analyst-v2` 的筆記（2026-09-19 定案的分析師報告格式，
     範本 ROG 2026Q2「新架構試寫」）──
  H1  H1 標題要是一句話總結：有「：」且冒號後 12 到 40 字，不是「XXX 財報筆記」
  S2  必備章節：1 本季成績單、2 基本面分析、3 論點檢視、4 估值、5 部位與操作、6 風險與論點追蹤
  X1  禁止獨立的深度分析／技術面／官方文件章節、附錄、`> [!note]-` 摺疊區塊
  C1  每個 `![[*.png]]` 前一行要有圖說 `圖 N｜…`
  W1  禁用詞：隱含、有機成長、bps（換算成 %）、吃掉、咬掉、撐不久、拖後腿、「一句話：」
  T1  英文詞彙：財務縮寫（YoY/EPS/FCF/P/E…）、指標名（RSI/MACD…）、硬體縮寫、公司名
      與代號以外的英文，同一個詞出現 TERM_FAIL_COUNT 次以上 FAIL（該翻成中文了）、
      2 次以上 WARN。部門名／產品名／KPI 縮寫（Security、NRR、RPO、WAF）都算
校準（2026-09-07，46 篇）：META／TER 範本 PASS（各帶幾個 WARN），FSLY／WDAY／NOW 這種
整段散文的 FAIL。門檻是為了抓「整篇擠成一團」，不是為了讓每篇零 WARN。

用法：
  python3 lint_note_format.py 筆記.md [...]      逐檔列出違規行，任一 FAIL 就 exit 1
  python3 lint_note_format.py --all              掃 vault 全部筆記，印總表
  python3 lint_note_format.py --all --mirror     掃 ~/.arthur-digest 鏡像（launchd 讀不到 ~/Documents）
  --json   機器可讀輸出（sync_staged.sh 用）
字數算法：CJK 一字算 1，ASCII 連續字元（數字、英文字、符號）一個 token 算 1，
所以 `$183.3M` 算 1 不算 7，中英混排的行不會被英數字灌水。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

SUMMARY_MAX = 90          # 30 秒結論單行上限（FAIL）
PROSE_MAX = 120           # 純段落超過就 WARN
PROSE_HARD = 200          # 純段落超過就 FAIL
PROSE_LONG_ALLOWED = 5    # 全篇 WARN 等級的長段落最多幾段，超過 FAIL
BULLET_MAX = 150          # 條列一點超過就 WARN
BULLET_HARD = 220         # 條列一點超過就 FAIL
QUOTE_MAX = 250           # Q&A 引用一行超過就 WARN（管理層回答本來就長，不 FAIL）
BULLET_RATIO_MIN = 0.60   # 條列 ÷（條列＋段落）低於就 FAIL
LEAD_MAX = 20             # 條列開頭重點句的最大長度
LEAD_RATIO_MIN = 0.70     # 有重點引導的條列佔比低於就 FAIL
TERM_FAIL_COUNT = 4       # 同一個非白名單英文詞出現幾次以上 FAIL
TERM_WARN_COUNT = 2

# 2a 財務縮寫、指標名、硬體縮寫、常見單位。這些是使用者拍板要留英文的（SKILL 鐵律 2a／2b），
# 其餘英文都該翻。比對不分大小寫。
TERM_ALLOW = set("""
yoy qoq eps fcf ocf pe p/e peg ps p/s p/fcf p/ocf ev ev/s ev/sales ev/ebitda ebitda capex
gaap non-gaap non gaap ttm ltm fy fy26 fy27 fy28 q1 q2 q3 q4 bps x k m b t
atr rsi macd kdj obv k/d/j ai gpu cpu hbm nand ddr euv soc asic cdn saas
dcf wacc roe roic roa cagr
sec edgar ir pdf html csv json stock-radar obsidian
ceo cfo coo cto
pp bp gw mw kw tw nm bn mn tb gb pb ghz mhz
""".split())
# 產品代號、平台名這種沒有中文譯名的字放這個檔（一行一個，# 開頭是註解）；
# 股票代號自動從 registry companies 與 backend stocks 表帶入，不用手填。
TERM_ALLOW_FILE = pathlib.Path(__file__).with_name("lint_terms_allow.txt")
_ALLOW_RE = re.compile(r"(fy|cy)?\d{2,4}|q\d{1,2}(?:'\d{2})?|[a-z]|fy\d{2}q\d|\d+[a-z]{1,3}")


def _load_extra_allow() -> set[str]:
    words: set[str] = set()
    if TERM_ALLOW_FILE.exists():
        for line in TERM_ALLOW_FILE.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                words.add(line.lower())
    import sqlite3
    here = pathlib.Path(__file__).resolve()
    for db, sql in ((here.parents[2] / "data/earnings_registry.db", "SELECT ticker FROM companies"),
                    (here.parents[2] / "backend/stock_valuation.db", "SELECT ticker FROM stocks")):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            words.update(str(r[0]).lower() for r in con.execute(sql) if r[0])
            con.close()
        except Exception:
            pass
    return words

VAULT = pathlib.Path.home() / "Documents/arthurwang_DB/投資/財報筆記"
MIRROR = pathlib.Path.home() / ".arthur-digest/earnings_notes/vault-mirror"

_CJK = re.compile(r"[　-〿㐀-䶿一-鿿豈-﫿＀-￯]")
_TOKEN = re.compile(r"[A-Za-z0-9$%.,+\-−–/()（）'\"’:：;；#@&*_=<>\[\]{}|\\~`^!?]+")
_ENUM = [
    re.compile(r"第一[，,、].*第二[，,、]"),
    re.compile(r"[（(]1[）)].*[（(]2[）)]"),
    re.compile(r"[（(]一[）)].*[（(]二[）)]"),
    re.compile(r"其一[，,].*其二[，,]"),
]


def width(s: str) -> int:
    """CJK 每字 1、英數 token 每個 1，用來衡量「讀起來多長」而不是 byte 數。"""
    s = re.sub(r"!\[\[[^\]]*\]\]", "圖", s)          # 圖片嵌入不算長度
    s = re.sub(r"https?://\S+", "網址", s)
    cjk = len(_CJK.findall(s))
    rest = _CJK.sub(" ", s)
    return cjk + len(_TOKEN.findall(rest))


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9&'’\-]*(?:/[A-Za-z0-9]+)*")
_LEAD_SEP = re.compile(r"[：:，,。；;]")


def _frontmatter_words(text: str) -> set[str]:
    """frontmatter 裡的英文（ticker、公司名、tags）在內文出現是合理的。"""
    words: set[str] = set()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        head = text[:end] if end != -1 else ""
        for line in head.splitlines():
            if line.strip().startswith(("sources", "- http")) or "http" in line:
                continue
            words.update(w.lower() for w in _WORD.findall(line))
    return words


def _count_terms(s: str, fm_words: set[str], counts: dict, first: dict, ln: int) -> None:
    s = re.sub(r"!\[\[[^\]]*\]\]|\[\[[^\]]*\]\]|https?://\S+|`[^`]*`", " ", s)
    for w in _WORD.findall(s):
        key = w.lower().rstrip("'’")
        if len(key) <= 1 or key in TERM_ALLOW or key in fm_words:
            continue
        if _ALLOW_RE.fullmatch(key) or key in _EXTRA_ALLOW:   # Q2'26、FY2028、單字母、代號與產品名
            continue
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, ln)


_EXTRA_ALLOW = _load_extra_allow()


def _has_lead(s: str) -> bool:
    """條列開頭是不是先給了重點：第一個標點前 2 到 LEAD_MAX 字，且後面還有內容。"""
    body = re.sub(r"^\s*([-*+]|\d+[.)]|[一二三四五六七八九十]+[、.])\s+", "", s)
    m = _LEAD_SEP.search(body)
    if not m:
        return width(body) <= LEAD_MAX          # 整點就是一句短結論也算
    lead, rest = body[:m.start()], body[m.end():].strip()
    return 2 <= width(lead) <= LEAD_MAX and bool(rest)


def strip_frontmatter(text: str) -> tuple[list[str], int]:
    """回傳 (內文行, 起始行號偏移)。"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            head = text[: end + 4]
            return text[end + 4:].splitlines(), head.count("\n")
    return text.splitlines(), 0


_V2_SECTIONS = [
    (r"^## 1\. 本季成績單", "## 1. 本季成績單：{結論}"),
    (r"^## 2\. 基本面分析", "## 2. 基本面分析"),
    (r"^## 3\. 論點檢視", "## 3. 論點檢視：{結論}"),
    (r"^## 4\. 估值", "## 4. 估值：{結論}"),
    (r"^## 5\. .*操作", "## 5. 部位與操作建議"),
    (r"^## 6\. .*論點追蹤", "## 6. 投資風險與論點追蹤"),
]
_V2_FORBIDDEN_H2 = re.compile(r"^## .*(深度分析|技術面|官方文件|附錄)")
_V2_BANNED = re.compile(r"隱含|有機成長|有機(?!會)|\d\s?bps|吃掉|咬掉|撐不久|拖後腿|一句話：")
_V2_CAPTION = re.compile(r"^圖 ?\d+ ?｜")


def _lint_v2(lines: list[str], offset: int, add) -> None:
    """analyst-v2 格式（2026-09-19）的結構與用詞檢查，見檔頭 H1／S2／X1／C1／W1。"""
    h1 = next(((i, l.strip()) for i, l in enumerate(lines) if l.startswith("# ")), None)
    if h1 is None:
        add("H1", 0, "沒有 H1 標題，要寫成 `# TICKER 2Q26：{一句話總結}`")
    else:
        i, s = h1
        head, sep, summary = s[2:].partition("：")
        if not sep or not (12 <= width(summary) <= 40) or summary.rstrip().endswith("財報筆記"):
            add("H1", i + 1 + offset, f"H1 要是一句話總結（冒號後 12 到 40 字），現在是：{s[:40]}")
    body = "\n".join(lines)
    for pat, want in _V2_SECTIONS:
        if not re.search(pat, body, flags=re.M):
            add("S2", 0, f"缺少章節 `{want}`")
    prev = ""
    for i, raw in enumerate(lines):
        s = raw.strip()
        ln = i + 1 + offset
        if _V2_FORBIDDEN_H2.match(s):
            add("X1", ln, f"不准獨立成章節，內容拆進對應小節：{s[:30]}")
        if s.startswith("> [!note]-") or s.startswith(">[!note]-"):
            add("X1", ln, "不准用摺疊區塊，圖表與內容直接放內文")
        if re.match(r"!\[\[[^\]]+\.(png|jpg|jpeg)\]\]", s, flags=re.I) and not _V2_CAPTION.match(prev):
            add("C1", ln, f"圖前一行要有圖說 `圖 N｜圖名：結論`：{s[:40]}")
        plain = re.sub(r"!?\[\[[^\]]*\]\]|https?://\S+", " ", s)
        m = _V2_BANNED.search(plain)
        if m:
            add("W1", ln, f"禁用詞「{m.group(0)}」，照 SKILL 口吻對照表改寫：{s[:40]}")
        if s:
            prev = s


def lint(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines, offset = strip_frontmatter(text)
    issues: list[dict] = []
    n_bullet = n_prose = 0
    n_lead_checked = n_lead_ok = 0
    in_code = False
    in_summary = False
    in_refs = False                      # 官方文件／資料來源：連結清單，不查重點與英文
    fm_words = _frontmatter_words(text)
    term_counts: dict[str, int] = {}
    term_first: dict[str, int] = {}

    def add(rule: str, ln: int, msg: str, sev: str = "FAIL"):
        issues.append({"rule": rule, "line": ln, "severity": sev, "msg": msg})

    for i, raw in enumerate(lines):
        ln = i + 1 + offset
        line = raw.rstrip()
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not s:
            in_summary = in_summary and bool(s)
            continue

        # ---- callout / blockquote ----
        if s.startswith(">"):
            body = s.lstrip(">").strip()
            if re.match(r"\[!summary\]", body):
                in_summary = True
                continue
            if in_summary:
                if not body.startswith(("- ", "* ")):
                    add("S1", ln, f"30 秒結論每一點要獨立成 `> - ` 一行，這行沒有：{body[:40]}…")
                w = width(body)
                if w > SUMMARY_MAX:
                    add("S1", ln, f"30 秒結論單行 {w} 字，上限 {SUMMARY_MAX}，拆成兩點或刪贅字")
                for pat in _ENUM:
                    if pat.search(body):
                        add("E1", ln, "30 秒結論把多點擠在同一行，改成每點一行")
                        break
                continue
            # Q&A 或其他引用
            w = width(body)
            if w > QUOTE_MAX:
                add("Q1", ln, f"引用區一行 {w} 字，上限 {QUOTE_MAX}，Q／回答／解讀各自一行並精簡", "WARN")
            for pat in _ENUM:
                if pat.search(body):
                    add("E1", ln, "引用區把多點擠在同一行，拆成條列或多行", "WARN")
                    break
            continue
        in_summary = False

        # ---- 不計入的行 ----
        if s.startswith("#"):
            in_refs = bool(re.search(r"資料來源|官方文件|參考", s))
            continue
        if s.startswith("![[") or s.startswith("<!--"):
            continue
        if re.match(r"^\[\[.*\]\]$", s):
            continue
        if not in_refs:
            _count_terms(s, fm_words, term_counts, term_first, ln)
        if s.startswith("|"):
            continue

        # ---- 條列 ----
        if re.match(r"^\s*([-*+]|\d+[.)]|[一二三四五六七八九十]+[、.])\s+", line):
            n_bullet += 1
            w = width(s)
            if w > BULLET_HARD:
                add("B1", ln, f"條列一點 {w} 字，硬上限 {BULLET_HARD}，拆成兩點或加子條列")
            elif w > BULLET_MAX:
                add("B1", ln, f"條列一點 {w} 字，上限 {BULLET_MAX}，考慮拆成兩點", "WARN")
            for pat in _ENUM:
                if pat.search(s):
                    add("E1", ln, "一個條列點裡塞了「第一／第二」多點，改成子條列")
                    break
            if not in_refs and "http" not in s and not re.match(r"^\s*[-*+]\s*\[\[", line):
                n_lead_checked += 1
                if _has_lead(s):
                    n_lead_ok += 1
                else:
                    add("L1", ln, f"條列沒有重點引導（開頭先給 6 到 {LEAD_MAX} 字的判斷句再接「：」）：{s[:30]}…", "WARN")
            continue

        # ---- 純段落 ----
        n_prose += 1
        w = width(s)
        if w > PROSE_HARD:
            add("P1", ln, f"純段落 {w} 字，硬上限 {PROSE_HARD}，改成條列（一點一件事）")
        elif w > PROSE_MAX:
            add("P1", ln, f"純段落 {w} 字，上限 {PROSE_MAX}，改成條列（一點一件事）", "WARN")
        for pat in _ENUM:
            if pat.search(s):
                add("E1", ln, "段落裡「第一／第二／第三」串在一起，改成條列")
                break

    body_text = "\n".join(lines)
    n_official = len(re.findall(r"!\[\[[^\]]*官方[^\]]*\]\]", body_text))
    if n_official == 0:
        # 「官方」命名規則 2026-09-07 才立，之前的筆記簡報頁用的是描述性檔名（META 的
        # 「費用占營收比.png」其實是官方投影片），對舊筆記只 WARN，不然存量 40 篇全紅
        m_date = re.search(r"^report_date:\s*(\d{4}-\d{2}-\d{2})", text, flags=re.M)
        legacy = bool(m_date and m_date.group(1) < "2026-09-01")
        add("I1", 0, "沒有嵌入任何官方文件截圖（檔名含「官方」的 ![[...]]）：至少要有損益表／分部門表或簡報頁，自製圖不算",
            "WARN" if legacy else "FAIL")
    long_prose = [x for x in issues if x["rule"] == "P1"]
    if len(long_prose) > PROSE_LONG_ALLOWED:
        add("P1", 0, f"超過 {PROSE_MAX} 字的純段落有 {len(long_prose)} 段，全篇最多容許 {PROSE_LONG_ALLOWED} 段，其餘改條列")
    total = n_bullet + n_prose
    ratio = (n_bullet / total) if total else 1.0
    if total >= 10 and ratio < BULLET_RATIO_MIN:
        add("R1", 0, f"條列佔比 {ratio:.0%}（{n_bullet} 條列／{n_prose} 段落），下限 {BULLET_RATIO_MIN:.0%}，說明文字要改寫成條列")

    lead_ratio = (n_lead_ok / n_lead_checked) if n_lead_checked else 1.0
    if n_lead_checked >= 10 and lead_ratio < LEAD_RATIO_MIN:
        add("L1", 0, f"有重點引導的條列只佔 {lead_ratio:.0%}（{n_lead_ok}/{n_lead_checked}），下限 {LEAD_RATIO_MIN:.0%}：每一點開頭先寫結論，不是把句子切行")
    for term, n in sorted(term_counts.items(), key=lambda kv: -kv[1]):
        if n >= TERM_FAIL_COUNT:
            add("T1", term_first[term], f"英文詞「{term}」出現 {n} 次，翻成中文（第一次出現括號附原文即可）")
        elif n >= TERM_WARN_COUNT:
            add("T1", term_first[term], f"英文詞「{term}」出現 {n} 次，考慮翻成中文", "WARN")

    if re.search(r"^format:\s*analyst-v2\s*$", text, flags=re.M):
        _lint_v2(lines, offset, add)

    fails = [x for x in issues if x["severity"] == "FAIL"]
    return {
        "path": str(path),
        "ticker": path.parent.parent.name if path.parent.parent != path.parent else "",
        "bullets": n_bullet,
        "prose": n_prose,
        "bullet_ratio": round(ratio, 3),
        "long_prose": sum(1 for x in issues if x["rule"] == "P1" and x["line"]),
        "lead_ratio": round(lead_ratio, 3),
        "en_terms": {k: v for k, v in sorted(term_counts.items(), key=lambda kv: -kv[1]) if v >= TERM_WARN_COUNT},
        "n_fail": len(fails),
        "n_warn": len(issues) - len(fails),
        "issues": issues,
        "status": "FAIL" if fails else "PASS",
    }


def all_notes(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(root.rglob("*財報筆記.md"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notes", nargs="*", help="筆記路徑")
    ap.add_argument("--all", action="store_true", help="掃 vault 全部筆記")
    ap.add_argument("--mirror", action="store_true", help="--all 改掃 ~/.arthur-digest 鏡像")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="只印總表不列每行")
    a = ap.parse_args()

    paths = [pathlib.Path(p).expanduser() for p in a.notes]
    if a.all:
        root = MIRROR if a.mirror else VAULT
        if not root.exists():
            print(f"找不到 {root}", file=sys.stderr)
            return 2
        paths += all_notes(root)
    if not paths:
        ap.print_help()
        return 2

    results = [lint(p) for p in paths if p.exists()]
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 1 if any(r["status"] == "FAIL" for r in results) else 0

    if len(results) > 1 or a.quiet:
        print(f"{'ticker':7} {'狀態':4} {'條列':>4} {'段落':>4} {'佔比':>5} {'長段落':>6} {'重點率':>6} {'英文≥4':>6} {'FAIL':>5} {'WARN':>5}")
        for r in results:
            n_en = sum(1 for v in r["en_terms"].values() if v >= TERM_FAIL_COUNT)
            print(f"{r['ticker']:7} {r['status']:4} {r['bullets']:4} {r['prose']:4} {r['bullet_ratio']:5.0%} "
                  f"{r['long_prose']:6} {r['lead_ratio']:6.0%} {n_en:6} {r['n_fail']:5} {r['n_warn']:5}")
        n_fail = sum(1 for r in results if r["status"] == "FAIL")
        print(f"\n{len(results)} 篇：PASS {len(results) - n_fail}、FAIL {n_fail}")
    if not a.quiet:
        for r in results:
            if len(results) > 1 and not r["issues"]:
                continue
            print(f"\n== {r['status']}  {pathlib.Path(r['path']).name}"
                  f"（條列 {r['bullets']}／段落 {r['prose']}，佔比 {r['bullet_ratio']:.0%}，重點引導 {r['lead_ratio']:.0%}）")
            if r["en_terms"]:
                top = "、".join(f"{k}×{v}" for k, v in list(r["en_terms"].items())[:15])
                print(f"  英文詞（出現 ≥{TERM_WARN_COUNT} 次）：{top}")
            for x in sorted(r["issues"], key=lambda x: (x["severity"] != "FAIL", x["line"])):
                loc = f"L{x['line']}" if x["line"] else "全篇"
                print(f"  {x['severity']:4} {x['rule']} {loc:>6}  {x['msg']}")
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
