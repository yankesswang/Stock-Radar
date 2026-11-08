#!/usr/bin/env python3
"""把 EDGAR 8-K／6-K 附件的原始財務表截圖補進既有財報筆記。

背景（2026-09-07）：盤點 47 篇，18 篇一張官方圖都沒有、22 篇只有簡報頁沒有原表。
這支腳本對每篇筆記：
  1. 讀 frontmatter 的 ticker／report_date，從 SEC submissions 找報告日 ±5 天內的
     8-K（Item 2.02）或 6-K，抓所有 HTML 附件網址
  2. 用 fetch_ir_docs.capture_exhibit_tables 把附件裡的表逐張截圖
  3. 依表格文字分類：損益表／GAAP 對 Non-GAAP 對帳／資產負債表／現金流量表／分部門或 KPI，
     每類挑第一張，最多五張
  4. 複製進筆記資料夾，命名「{TICKER} {季度} 官方{表名}.png」
  5. 插進對應小節（成績單、資產負債、FCF／現金流、分部門）第一張表格之後，
     並在「官方文件」節加一份清單
只動圖與圖說，不改既有文字。已經有「官方」圖的筆記跳過（冪等）。
用法：backfill_exhibit_tables.py [--ticker T ...] [--dry-run] [--limit N]
"""
from __future__ import annotations
import argparse, json, pathlib, re, shutil, sys, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_ir_docs as F

VAULT = pathlib.Path.home() / "Documents/arthurwang_DB/投資/財報筆記"
SCRATCH = pathlib.Path.home() / ".arthur-digest/earnings_notes/exhibit_backfill"

def _has(s, *kws): return any(k in s for k in kws)


def is_income(s):
    if _has(s[:250], "prior", "updated", "guidance", "outlook", "expect"): return False
    return (_has(s, "total revenue", "revenue", "net sales", "sales", "turnover")
            and _has(s, "operating income", "income from operations", "operating expenses", "cost of revenue",
                     "cost of sales", "cost of goods", "gross profit", "gross margin", "research and development", "operating profit")
            and _has(s, "net income", "net loss", "net earnings", "per share", "profit after tax")
            and not _has(s, "cash flows from", "total assets"))


def is_recon(s):
    return (_has(s, "non-gaap", "non-ifrs", "reconciliation", "adjusted ebitda", "adjusted")
            and _has(s, "stock-based", "share-based", "amortization", "acquisition-related", "restructuring", "impairment", "gaap")
            and not _has(s, "cash flows from", "total assets"))


def is_balance(s):
    return _has(s, "total assets") and _has(s, "total liabilities", "stockholders", "shareholders", "equity")


def is_cashflow(s):
    return _has(s, "operating activities") and _has(s, "investing activities", "financing activities", "purchases of property", "capital expenditure")


def is_segment(s):
    if _has(s[:200], "table of contents", "primary financial statements"): return False
    return _has(s, "segment", "by product", "by geograph", "by region", "revenue by", "key metrics", "selected metrics",
                "operating metrics", "by end market", "by platform", "by business", "by category", "reportable")


CATS = [  # (label, section keywords in order of preference, classifier)
    ("損益表", ["成績單", "整體營運", "營運"], is_income),
    ("GAAP 對 Non-GAAP 對帳", ["成績單", "整體營運", "營運"], is_recon),
    ("資產負債表", ["資產負債", "資負", "資金", "成績單"], is_balance),
    ("現金流量表", ["FCF", "現金流", "資產負債", "成績單"], is_cashflow),
    ("分部門與關鍵指標", ["分部門", "部門", "客戶", "成績單"], is_segment),
]


def digit_density(s: str) -> float:
    return sum(c.isdigit() for c in s) / max(len(s), 1)


def frontmatter(text: str) -> dict:
    fm = {}
    if text.startswith("---"):
        for line in text.split("\n---", 1)[0].splitlines()[1:]:
            if ":" in line and not line.startswith((" ", "-")):
                k, v = line.split(":", 1); fm[k.strip()] = v.strip()
    return fm


def find_exhibits(cik: int, report_date: str, workdir: pathlib.Path) -> list[dict]:
    sub = json.loads(F.get_text(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    r = sub["filings"]["recent"]
    rd = dt.date.fromisoformat(report_date)
    best = []
    for i in range(len(r["form"])):
        form = r["form"][i]
        if form not in ("8-K", "6-K"):
            continue
        fd = dt.date.fromisoformat(r["filingDate"][i])
        if abs((fd - rd).days) > 5:
            continue
        if form == "8-K" and "2.02" not in (r.get("items", [""] * len(r["form"]))[i] or ""):
            continue
        acc = r["accessionNumber"][i].replace("-", "")
        idx = F.get_text(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/")
        links = re.findall(r'href="(/Archives/[^"]*\.htm)"', idx)
        ex = [u for u in links if not re.search(r"/R\d+\.htm$", u) and not re.search(r"[a-z]+-\d{8}\.htm$", u)]
        for u in ex:
            best.append({"date": r["filingDate"][i], "name": pathlib.Path(u).stem, "url": "https://www.sec.gov" + u})
    # 同一天多份 8-K 時全收；capture 只看 prs[0]["date"] 那批，所以統一日期
    if best:
        d0 = best[0]["date"]
        for b in best: b["date"] = d0
    return best


def classify(shots: list[dict]) -> dict[str, dict]:
    """每類挑「符合條件且最高」的那張：財測表、摘要條列、目錄都比正表矮或數字太少。"""
    picked, used = {}, set()
    for label, _, fn in CATS:
        cands = []
        for s in shots:
            txt = s.get("text", s.get("head", "")).lower()
            if s["png"] in used or digit_density(txt) < 0.10 or not fn(txt):
                continue
            cands.append(s)
        if cands:
            best = max(cands, key=lambda s: s.get("height", 0))
            picked[label] = best; used.add(best["png"])
    return picked


def section_bounds(lines: list[str], keywords: list[str]) -> tuple[int, int] | None:
    heads = [(i, l) for i, l in enumerate(lines) if re.match(r"^#{2,3}\s", l)]
    for kw in keywords:
        for n, (i, l) in enumerate(heads):
            if kw in l and not re.search(r"官方文件|資料來源", l):
                end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
                # H2 節：若第一個子標題之前沒有內容才延伸到子節；否則只到第一個子標題
                return i, end
    return None


def insert_point(lines: list[str], start: int, end: int) -> int:
    """節內第一個表格結束後；沒有表格就放節尾（下一個標題前）。"""
    i = start + 1
    while i < end and not lines[i].startswith("|"):
        i += 1
    if i < end:
        while i < end and lines[i].startswith("|"):
            i += 1
        return i
    j = end
    while j > start + 1 and not lines[j - 1].strip():
        j -= 1
    return j


def apply(note: pathlib.Path, picked: dict[str, dict], label_prefix: str, dry: bool) -> list[str]:
    text = note.read_text(encoding="utf-8")
    if re.search(r"!\[\[[^\]]*官方(損益|資產負債|現金流|GAAP|分部門|八季)", text):
        return ["already has official tables, skip"]
    lines = text.split("\n")
    added = []
    inserts = []  # (index, block)
    for label, kws, _ in CATS:
        if label not in picked:
            continue
        s = picked[label]
        fname = f"{label_prefix} 官方{label}.png"
        b = section_bounds(lines, kws)
        if not b:
            b = section_bounds(lines, ["公司整體", "營運", "股價"])
        if not b:
            continue
        idx = insert_point(lines, *b)
        block = ["", f"官方{label}（EDGAR 附件 {s['exhibit']}，原表未改）：", "", f"![[{fname}]]", ""]
        inserts.append((idx, block))
        added.append((fname, s["png"], label))
    if not added:
        return ["no section to insert"]
    for idx, block in sorted(inserts, key=lambda x: -x[0]):
        # 前一行非空就先補空行
        if idx > 0 and lines[idx - 1].strip():
            block = [""] + block[1:]
        lines[idx:idx] = block
    # 官方文件節加清單
    text2 = "\n".join(lines)
    m = re.search(r"^## [^\n]*官方文件[^\n]*\n", text2, flags=re.M)
    listing = "\nEDGAR 官方財務表截圖（8-K／6-K 附件，原表未改，2026-09-07 補入）：\n\n" + "\n".join(f"- [[{f}]]" for f, _, _ in added) + "\n"
    if m:
        text2 = text2[:m.end()] + listing + text2[m.end():]
    else:
        m2 = re.search(r"^## [^\n]*資料來源", text2, flags=re.M)
        text2 = (text2[:m2.start()] + "## 官方文件\n" + listing + "\n" + text2[m2.start():]) if m2 else text2 + "\n## 官方文件\n" + listing
    text2 = re.sub(r"\n{3,}", "\n\n", text2)
    if not dry:
        for fname, src, _ in added:
            shutil.copyfile(src, note.parent / fname)
        note.write_text(text2, encoding="utf-8")
    return [f"{label}: {fname}" for fname, _, label in added]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    notes = sorted(VAULT.rglob("*財報筆記.md"))
    if a.ticker:
        want = {t.upper() for t in a.ticker}
        notes = [n for n in notes if n.parent.parent.name.upper() in want]
    if a.limit:
        notes = notes[: a.limit]
    SCRATCH.mkdir(parents=True, exist_ok=True)
    summary = []
    for note in notes:
        text = note.read_text(encoding="utf-8")
        fm = frontmatter(text)
        ticker, rdate = fm.get("ticker", note.parent.parent.name), fm.get("report_date", "")
        # 檔名用資料夾的季度標籤（2026_Q2 → 2026Q2、FY2027_Q1 → FY2027Q1）；
        # frontmatter 的 quarter 有些寫成「2026 Q2（2026-06-30 止）」不能當檔名
        quarter = note.parent.name.replace("_", "")
        tag = f"{ticker} {quarter}"
        if re.search(r"!\[\[[^\]]*官方(損益|資產負債|現金流|GAAP|分部門|八季)", text):
            print(f"== {tag}: already has official tables, skip"); summary.append((tag, "skip")); continue
        if not rdate:
            print(f"== {tag}: no report_date"); summary.append((tag, "no date")); continue
        workdir = SCRATCH / ticker
        workdir.mkdir(exist_ok=True)
        try:
            cik = F.resolve_cik(ticker, workdir)
            exhibits = find_exhibits(cik, rdate, workdir)
        except Exception as e:
            print(f"== {tag}: SEC lookup failed: {e}"); summary.append((tag, "sec fail")); continue
        if not exhibits:
            print(f"== {tag}: no 8-K/6-K HTML exhibits within ±5d of {rdate}"); summary.append((tag, "no exhibits")); continue
        manifest = {"press_releases": exhibits}
        F.capture_exhibit_tables(workdir, manifest)
        shots = manifest.get("exhibit_tables", [])
        picked = classify(shots)
        print(f"== {tag}: exhibits {len(exhibits)}, tables {len(shots)}, picked {list(picked)}"
              + (f"  note={manifest.get('exhibit_tables_note')}" if manifest.get("exhibit_tables_note") else ""))
        for label, s in picked.items():
            print(f"    [{label}] h={s.get('height')} {s.get('head','')[:110]}")
        res = apply(note, picked, tag, a.dry_run) if picked else ["no tables classified"]
        for r in res: print("   ", r)
        summary.append((tag, f"{len(picked)} tables" if picked else "none"))
    print("\n=== summary ===")
    for tag, s in summary: print(f"{tag:22} {s}")


if __name__ == "__main__":
    main()
