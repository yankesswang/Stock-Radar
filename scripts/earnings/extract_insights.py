#!/usr/bin/env python3
"""把財報筆記裡的關鍵數據與論點抽成結構化資料表。

跟 extract_conclusions.py 分工：那支抽「這季決定做什麼」（一列一篇），
這支抽「憑什麼這樣決定」（一列一個指標），三張表：

  earnings_theses       論點追蹤表：指標｜現值｜多方需要看到｜空方確認訊號
                        42/46 篇有。唯一前瞻、可驗證的結構——下一季同一檔筆記
                        進來時，同名指標會自動接成「上季門檻 vs 這季現值」
  earnings_metrics      成績單表：指標｜本季值｜QoQ｜YoY｜解讀
                        含分部門與地區別的表，用 section 欄位區分脈絡
  earnings_deep_metrics 深度分析的自算指標（增量營益率／反推 DCF／EPS 三情境…）
                        寫在散文裡，只有這張表需要 LLM 讀

表格是 markdown，抽取全程確定性，不花額度；只有 --llm-deep 那條路徑會呼叫 claude。

用法:
  extract_insights.py                    抽表格（論點＋成績單），全部筆記
  extract_insights.py --ticker WDAY      只抽一檔
  extract_insights.py --llm-deep         另外用 claude 讀深度分析段
  extract_insights.py --dry-run --show   只印不寫，附抽出來的內容
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from extract_conclusions import (  # noqa: E402  共用筆記走訪與章節切分
    DB, notes_for, parse_frontmatter, split_sections,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS earnings_theses (
  ticker TEXT, quarter TEXT, report_date TEXT, idx INTEGER,
  metric TEXT, metric_key TEXT, current_value TEXT,
  bull_condition TEXT, bear_condition TEXT,
  PRIMARY KEY (ticker, quarter, idx)
);
CREATE TABLE IF NOT EXISTS earnings_metrics (
  ticker TEXT, quarter TEXT, report_date TEXT, idx INTEGER,
  section TEXT, section_key TEXT, metric TEXT, metric_key TEXT,
  value TEXT, qoq TEXT, yoy TEXT, comment TEXT,
  PRIMARY KEY (ticker, quarter, idx)
);
CREATE TABLE IF NOT EXISTS earnings_deep_metrics (
  ticker TEXT, quarter TEXT, idx INTEGER,
  kind TEXT, label TEXT, value TEXT, detail TEXT,
  source TEXT, section_hash TEXT,
  PRIMARY KEY (ticker, quarter, idx)
);
"""

# 技術面那張「指標｜現值｜狀態｜意義」每篇都有，但數字全部來自 price_technicals.py，
# App 的價格圖已經在算同一組，抽進來只是重複，明確排除
TECH_HEADER = {"狀態", "意義"}


def _cell(s: str) -> str:
    """表格儲存格的清理。不能用 extract_conclusions._clean——它會把開頭的 `-`
    當條列符號剝掉，於是 QoQ 的 -7.4% 變成 +7.4%，正負號整個翻掉。"""
    return s.strip().strip("*").replace("**", "").strip()


def parse_tables(body: str):
    """走訪 markdown 表格，回傳 (章節標題, 表頭, 資料列)。

    章節跟著表格一起帶出來：同樣是「指標｜本季｜QoQ｜YoY」的表，出現在
    「公司整體營運」是總營收，出現在「分部門」是各部門，混在一起就沒意義了。
    """
    for title, section in split_sections(body, level=2):
        rows, header = [], None
        for line in section.splitlines() + [""]:
            s = line.strip()
            if s.startswith("|") and s.endswith("|"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                if all(set(c) <= set("-: ") for c in cells):   # |---|---| 分隔列
                    continue
                if header is None:
                    header = cells
                else:
                    rows.append(cells)
                continue
            if header is not None:
                if rows:
                    yield title, header, rows
                rows, header = [], None


def norm_key(metric: str) -> str:
    """指標比對用的正規化鍵：去掉季度標記、括號補充、空白與全半形差異。

    跨季接得起來全靠它——「Non-GAAP 營益率」與「Non-GAAP營益率（本季）」要視為同一條。
    """
    s = re.sub(r"[（(].*?[)）]", "", metric)
    s = re.sub(r"\s+|[｜|，,、。:：]", "", s)
    return s.lower().replace("－", "-")


# 章節標題的編號有 `2. `、`二、`、無編號三種寫法，還會帶「：成績單」之類的後綴。
# 不正規化的話，同一個「公司整體營運」在前端會裂成三組
def section_key(title: str) -> str:
    t = re.sub(r"^[0-9一二三四五六七八九十]+[\.、]\s*", "", title.strip())
    return re.split(r"[：:（(]", t)[0].strip()


def _col(header: list[str], *names: str) -> int | None:
    for i, h in enumerate(header):
        if any(n in h for n in names):
            return i
    return None


def extract_theses(body: str) -> list[dict]:
    """論點追蹤表：欄位名有好幾代寫法（多方需要／多方需要看到／空方確認訊號），
    一律用「含多方」「含空方」定位，不寫死表頭。"""
    out = []
    for _, header, rows in parse_tables(body):
        bull, bear = _col(header, "多方"), _col(header, "空方")
        if bull is None or bear is None:
            continue
        metric_i = _col(header, "指標", "項目") or 0
        value_i = _col(header, "現值", "本季") or 1
        for cells in rows:
            if max(metric_i, value_i, bull, bear) >= len(cells):
                continue
            metric = _cell(cells[metric_i])
            if not metric:
                continue
            out.append({
                "metric": metric, "metric_key": norm_key(metric),
                "current_value": _cell(cells[value_i]),
                "bull_condition": _cell(cells[bull]),
                "bear_condition": _cell(cells[bear]),
            })
    # 有些筆記把論點表寫了兩次（RRX 2026Q2 摘要一份、結論一份，字句略有出入），
    # 同一條論點在總覽上出現兩列只是雜訊；留內容較完整的那一份
    best: dict[str, dict] = {}
    for r in out:
        cur = best.get(r["metric_key"])
        if cur is None or len(str(r)) > len(str(cur)):
            best[r["metric_key"]] = r
    return [r for r in out if best.get(r["metric_key"]) is r]


def extract_metrics(body: str) -> list[dict]:
    """成績單表：認 QoQ／YoY 欄。表頭欄位數與順序每篇都不同，全部靠欄名定位。"""
    out = []
    for title, header, rows in parse_tables(body):
        if _col(header, "多方") is not None:                  # 論點表，另一支處理
            continue
        qoq, yoy = _col(header, "QoQ"), _col(header, "YoY")
        if qoq is None and yoy is None:
            continue
        if TECH_HEADER <= set(header):
            continue
        metric_i = _col(header, "指標", "項目", "科目", "部門", "地區") or 0
        comment_i = _col(header, "解讀", "說明", "白話")
        # 本季值：第一個不是指標／QoQ／YoY／解讀的欄（欄名常是 Q2'26、Q3 FY26 之類）
        skip = {metric_i, qoq, yoy, comment_i} - {None}
        value_i = next((i for i in range(len(header)) if i not in skip), None)
        if value_i is None:
            continue
        for cells in rows:
            if len(cells) <= max(i for i in (metric_i, value_i, qoq, yoy) if i is not None):
                continue
            metric = _cell(cells[metric_i])
            if not metric or set(metric) <= set("-— "):
                continue
            out.append({
                "section": title, "section_key": section_key(title), "metric": metric, "metric_key": norm_key(metric),
                "value": _cell(cells[value_i]),
                "qoq": _cell(cells[qoq]) if qoq is not None else "",
                "yoy": _cell(cells[yoy]) if yoy is not None else "",
                "comment": _cell(cells[comment_i]) if comment_i is not None else "",
            })
    return out


# ── 深度分析（散文，需要 LLM）────────────────────────────────────────────────
DEEP_PROMPT = """下面是一份財報筆記的「深度分析」章節，裡面是作者自己動手算的指標。
把算出來的數字抽成 JSON 陣列，只輸出 JSON、不要 markdown 圍欄、不要其他文字：

[{"kind": "<類別>", "label": "<這個數字叫什麼，20 字內>", "value": "<數字本身，含單位>", "detail": "<一句話說明這個數字代表什麼，50 字內>"}]

kind 只能用這幾個：
  incremental_margin  增量營益率
  depreciation        折舊推演／capex 對未來營益率的壓力
  fcf                 FCF 橋接或情境
  implied_dcf         反推 DCF（現價隱含的穩態 FCF、隱含成長率）
  eps_scenario        EPS 三情境（熊／基準／牛）
  other               其他自算指標

只抽作者算出來的結果，不要抽公司財報原本就公布的數字。找不到就輸出 []。

---
%s
---"""


def deep_section(body: str) -> tuple[str, str]:
    """(深度分析章節全文, 內容雜湊)。雜湊用來判斷這一季抽過之後有沒有改動。"""
    sections = [b for t, b in split_sections(body, level=2) if "深度" in t]
    limit = 9000
    if not sections:
        # analyst-v2（2026-09-19）不再有「深度分析」章節，七件套拆進「2. 基本面分析」
        # 與「4. 估值」，兩節一起丟給 LLM；公司公布的數字靠 DEEP_PROMPT 的指示排除
        sections = [b for t, b in split_sections(body, level=2) if "基本面" in t or "估值" in t]
        limit = 20000
    if not sections:
        return "", ""
    text = "\n".join(sections)[:limit]
    return text, hashlib.sha256(text.encode()).hexdigest()[:16]


def extract_deep(text: str, section_hash: str, ticker: str, quarter: str,
                 attempts: int = 2, verbose: bool = True) -> list[dict]:
    """呼叫 claude 把深度分析段抽成結構化指標。

    46 篇連續打的時候尾端會整批「沒回 JSON」（同一篇單獨重跑就正常），是暫時性的，
    所以失敗要重試一次；沒有重試的那一版一口氣掉了 9 篇。
    """
    import subprocess
    for attempt in range(1, attempts + 1):
        if verbose:
            suffix = "" if attempt == 1 else f"（重試 {attempt - 1}）"
            print(f"    {ticker} {quarter} 深度分析 {suffix}…", end="", flush=True)
        try:
            out = subprocess.run(["claude", "-p", DEEP_PROMPT % text],
                                 capture_output=True, text=True, timeout=240)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f" 失敗（{type(e).__name__}）")
            continue
        m = re.search(r"\[.*\]", out.stdout, flags=re.S)
        if not m:
            print(" 沒回 JSON")
            continue
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            print(" JSON 壞掉")
            continue
        rows = [d for d in data if isinstance(d, dict) and d.get("label")]
        if verbose:
            print(f" {len(rows)} 項")
        return [{"kind": (d.get("kind") or "other")[:32], "label": d["label"][:60],
                 "value": str(d.get("value", ""))[:60], "detail": (d.get("detail") or "")[:200],
                 "section_hash": section_hash}
                for d in rows]
    return []


def ensure_schema(con: sqlite3.Connection) -> None:
    """建表，並補上舊庫缺的欄位。

    CREATE TABLE IF NOT EXISTS 對既有的表不會加新欄位——加欄位後直接寫入會炸在
    "table has no column named ..."（section_key 上線時踩過）。
    """
    con.executescript(SCHEMA)
    for block in SCHEMA.split("CREATE TABLE IF NOT EXISTS ")[1:]:
        table = block.split("(", 1)[0].strip()
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for raw in block.split("(", 1)[1].split(","):
            parts = raw.strip().split()
            if len(parts) == 2 and parts[1] in ("TEXT", "INTEGER", "REAL") \
                    and parts[0] not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {parts[0]} {parts[1]}")
    con.commit()


def write(con, table: str, ticker: str, quarter: str, report_date: str,
          rows: list[dict], extra: dict | None = None) -> None:
    """整檔整季覆蓋：重跑時先刪這一季再寫，才不會留下上一版的殘列。"""
    con.execute(f"DELETE FROM {table} WHERE ticker=? AND quarter=?", (ticker, quarter))
    for i, r in enumerate(rows):
        d = {"ticker": ticker, "quarter": quarter, "idx": i, **r, **(extra or {})}
        if table != "earnings_deep_metrics":
            d["report_date"] = report_date
        cols = list(d)
        con.execute(f"INSERT INTO {table} ({','.join(cols)}) "
                    f"VALUES ({','.join('?' * len(cols))})", [d[c] for c in cols])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", action="store_true", help="印出抽到的內容")
    ap.add_argument("--llm-deep", action="store_true",
                    help="用 claude 讀「深度分析」章節，抽自算指標（抽過的自動跳過）")
    ap.add_argument("--redo-deep", action="store_true",
                    help="連抽過的也重抽，會重新花額度")
    a = ap.parse_args()

    notes = notes_for(a.ticker)
    if not notes:
        print("no notes found")
        return

    con = None
    if not a.dry_run:
        con = sqlite3.connect(DB)
        ensure_schema(con)

    # 深度分析已抽過且章節沒變的就跳過，重跑不重複燒額度
    prev_deep = {}
    if a.llm_deep and con is not None:
        prev_deep = {(t, q): h for t, q, h in con.execute(
            "SELECT ticker, quarter, section_hash FROM earnings_deep_metrics GROUP BY ticker, quarter")}

    n_th = n_me = n_dp = n_skip = 0
    for note in notes:
        text = note.read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(text)
        body = text.split("\n---", 1)[1] if text.startswith("---") else text
        ticker = (fm.get("ticker") or note.parent.parent.name).upper()
        quarter, report_date = note.parent.name, fm.get("report_date", "")

        theses, metrics = extract_theses(body), extract_metrics(body)
        n_th += len(theses)
        n_me += len(metrics)
        if a.show:
            print(f"\n=== {ticker} {quarter} — 論點 {len(theses)}、數據 {len(metrics)}")
            for t in theses[:3]:
                print(f"    論點 {t['metric']}｜現 {t['current_value']}｜"
                      f"多 {t['bull_condition'][:22]}｜空 {t['bear_condition'][:22]}")
            for m in metrics[:3]:
                print(f"    數據 [{m['section'][:10]}] {m['metric']}｜{m['value']}｜"
                      f"QoQ {m['qoq']}｜YoY {m['yoy']}")

        deep = []
        if a.llm_deep:
            text, shash = deep_section(body)
            if not text:
                pass                                      # 這篇沒有深度分析章節
            elif prev_deep.get((ticker, quarter)) == shash and not a.redo_deep:
                n_skip += 1                               # 抽過且章節沒變，不重打
            else:
                deep = extract_deep(text, shash, ticker, quarter)
                n_dp += len(deep)

        if con is not None:
            write(con, "earnings_theses", ticker, quarter, report_date, theses)
            write(con, "earnings_metrics", ticker, quarter, report_date, metrics)
            if deep:                                     # 空的別寫，否則會清掉上一輪抽好的
                write(con, "earnings_deep_metrics", ticker, quarter, report_date,
                      deep, extra={"source": "llm"})
            con.commit()

    if con is not None:
        con.close()
    print(f"\n{len(notes)} 篇 → 論點 {n_th} 列、成績單 {n_me} 列"
          + (f"、深度指標 {n_dp} 列（沿用 {n_skip} 篇）" if a.llm_deep else "")
          + (" (dry-run)" if a.dry_run else f" → {DB}"))


if __name__ == "__main__":
    main()
