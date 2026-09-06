#!/usr/bin/env python3
"""從財報新聞稿抽出 GAAP → Non-GAAP 對帳表。

Non-GAAP 只存在於新聞稿：SEC 的 XBRL 只標記 GAAP 財報，stockanalysis 給的是
S&P 正規化後的 GAAP（158 個欄位裡沒有任何公司自訂的 Non-GAAP）。準則要求公司
在新聞稿裡附「GAAP 起點 → 調整項 → Non-GAAP 結果」的對帳表，那是唯一的來源。

新聞稿本身是 8-K 的 Item 2.02 附件，fetch_ir_docs.py 已經在抓（見 fetch_8ks）。
這支從那些附件的 HTML 裡把對帳表解出來。

解析走 HTML 而不是純文字：表頭的「Three Months Ended」跨幾欄是 colspan 決定的，
轉成文字就沒了，期間對不回去。

自我驗證兩層（都在 verify 模式印出來）：
  1. 表內：GAAP 值 + 各調整項 ≈ Non-GAAP 值
  2. 跨源：表上的 GAAP 值 ≈ financial_facts 裡同期的 SEC 申報值
兩層都過才算解析正確——解析錯位會在第一層就現形。

用法:
  extract_nongaap.py --ticker ADI          單檔
  extract_nongaap.py --filings 8           每檔往回抓幾份財報 8-K
  extract_nongaap.py --show                印出解出來的內容
"""
import argparse
import html
import json
import pathlib
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTRY_DB = ROOT / "data" / "earnings_registry.db"
VALUATION_DB = ROOT / "backend" / "stock_valuation.db"
UA = "arthur-stock-radar research yankesswang@gmail.com"

SCHEMA = """
CREATE TABLE IF NOT EXISTS nongaap_reconciliation (
  ticker TEXT, period_end TEXT, period_months INTEGER,
  gaap_label TEXT, gaap_value REAL,
  nongaap_label TEXT, nongaap_value REAL,
  adjustments TEXT, residual REAL, unit TEXT,
  accession TEXT, filed TEXT, source_url TEXT, fetched_at TEXT,
  PRIMARY KEY (ticker, period_end, period_months, nongaap_label)
);
"""

# 對帳表的標題用語。準則沒有規定字面，但實務上高度集中在這幾種說法
RECON_TITLE = re.compile(
    r"reconciliation of (gaap to non[- ]?gaap|non[- ]?gaap)|non[- ]?gaap reconciliation", re.I)
# Non-GAAP 結果列的前綴。各家用詞不同：ADI 用 Adjusted，ServiceNow 用 Non-GAAP，
# 也有人用 Core。
NONGAAP_PREFIX = re.compile(r"^\s*(adjusted|non[- ]?gaap|core)\s+", re.I)
# 配對時兩側都要正規化：ADI 是 "Gross margin" → "Adjusted gross margin"（GAAP 側
# 沒前綴），ServiceNow 是 "GAAP gross profit" → "Non-GAAP gross profit"（兩側都有）。
# 只剝 Non-GAAP 那一側的話，ServiceNow 這種寫法永遠配不到，整份解出 0 列。
LABEL_PREFIX = re.compile(r"^\s*(gaap|adjusted|non[- ]?gaap|core|total)\s+", re.I)


def norm_label(label: str) -> str:
    s = LABEL_PREFIX.sub("", label or "").strip().lower()
    return re.sub(r"[\s,()]+", " ", s).strip(" :.")
MONTHS_HDR = re.compile(r"(\w+|\d+)\s*months?\s*ended", re.I)
# 不是每家都寫「X Months Ended」：MU 的表頭是「3rd Qtr.」「2nd Qtr.」，
# 完全沒有月數字樣，不認的話整份解不出期間
QTR_HDR = re.compile(r"\b(\d(?:st|nd|rd|th)\s*qtr|quarter\s*ended|quarterly)\b", re.I)
YTD_HDR = re.compile(r"\b(year\s*to\s*date|twelve\s*months|fiscal\s*year\s*ended)\b", re.I)
MONTH_WORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
DATE_RE = re.compile(r"([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),\s*(\d{4})")


def fetch(url: str, tries: int = 3, timeout: int = 40) -> str:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def _text(frag: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", frag)).replace("\xa0", " ").strip()


def _num(cell: str):
    t = _text(cell).replace("$", "").replace(",", "").replace("%", "").strip()
    if not t or t in {"—", "-", "–"}:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


def _cells(tr: str) -> list[str]:
    return re.findall(r"<t[dh][^>]*>.*?</t[dh]>", tr, flags=re.S)


def _spans(tr: str):
    """逐格回傳 (起始欄索引, 佔幾欄, 文字)，把 colspan 算進去。

    欄位對應一定要用真實欄索引：NVDA 的單季有 3 欄、半年只有 2 欄，
    用「日期數 ÷ 群組數」平均分配會除不盡而整組放棄，或把半年的累計數
    標成單季（跟 SEC 一比會差一倍以上）。
    """
    col = 0
    for cell in _cells(tr):
        m = re.search(r'colspan="?(\d+)', cell)
        n = int(m.group(1)) if m else 1
        yield col, n, _text(cell)
        col += n


def _header_months(tr: str) -> list[tuple[int, int, int]]:
    """→ [(起始欄, 結束欄, 月數)]，一個期間群組一筆。

    三種寫法都要認：
      「Three Months Ended」        最常見
      「3rd Qtr.」／「Quarter Ended」 MU 用這種，沒有月數字樣
      「Year to Date」／「Twelve Months」
    """
    out = []
    for col, n, text in _spans(tr):
        months = None
        m = MONTHS_HDR.search(text)
        if m:
            tok = m.group(1).lower()
            months = MONTH_WORD.get(tok) or (int(tok) if tok.isdigit() else None)
        elif QTR_HDR.search(text):
            months = 3
        elif YTD_HDR.search(text):
            months = 12
        if months:
            out.append((col, col + n, months))
    return out


# 月日可能單獨成格（NVDA 的 "July 26,"），也可能黏在期間說明句尾
# （WDAY 的 "Three Months Ended April 30,"），所以用 search 掃句尾而不是整串比對
_MD = re.compile(r"([A-Z][a-z]{2,8})\.?\s+(\d{1,2})\s*,?\s*$")


def _header_dates(tr: str, year_row: str | None = None) -> list[tuple[int, str]]:
    """→ [(欄索引, 日期)]。

    日期可能整個在一格（"Aug. 1, 2026"），也可能月日與年份拆成上下兩列
    （NVDA：上列 "July 26," 下列 "2026"）。拆行時用欄索引把年份對回去。
    """
    years = {}
    if year_row:
        for col, _n, text in _spans(year_row):
            if re.fullmatch(r"(19|20)\d{2}", text.strip()):
                years[col] = text.strip()

    def _mk(col, md, y):
        for fmt in ("%b %d %Y", "%B %d %Y"):
            try:
                return (col, datetime.strptime(f"{md} {y}", fmt).date().isoformat())
            except ValueError:
                continue
        return None

    out = []
    for col, n, text in _spans(tr):
        t = text.strip()
        m = DATE_RE.search(t)
        if m:
            got = _mk(col, f"{m.group(1)} {m.group(2)}", m.group(3))
            if got:
                out.append(got)
            continue
        m2 = _MD.search(t)
        if not m2:
            continue
        md = f"{m2.group(1)} {m2.group(2)}"
        # 這一格橫跨的欄位裡有幾個年份，就產生幾個期間：WDAY 的
        # 「Three Months Ended April 30,」跨兩欄，年份 2026 / 2025 在下一列
        in_span = sorted(c for c in years if col <= c < col + n)
        if in_span:
            out += [g for g in (_mk(c, md, years[c]) for c in in_span) if g]
        elif col in years:
            got = _mk(col, md, years[col])
            if got:
                out.append(got)
    return out


def walk_rows(doc: str):
    """整份文件的 <tr> 依序走一遍，邊走邊維護「目前的期間欄位」。

    新聞稿常常整份是一張巨大的表格（ADI 那份 63 列、5 萬字），裡面含好幾個區段、
    每段自己一組表頭。所以不能逐 <table> 解析，要照列流走：遇到「X Months Ended」
    就換一組期間，後面的資料列都對到那組，直到下一個表頭出現。

    yield (periods, {label, values, is_pct})
    """
    trs = [tr for table in re.findall(r"<table[^>]*>.*?</table>", doc, flags=re.S)
           for tr in re.findall(r"<tr[^>]*>.*?</tr>", table, flags=re.S)]
    periods: list[tuple[str, int]] = []
    groups: list[tuple[int, int, int]] = []
    for i, tr in enumerate(trs):
        g = _header_months(tr)
        if g:
            groups = g
            # 期間說明與日期可能在同一格：WDAY 寫「Three Months Ended April 30,」
            # 年份才放下一列。所以認出群組後不能直接跳過，同一列要再找一次日期。
            same_row = _header_dates(tr, trs[i + 1] if i + 1 < len(trs) else None)
            if not same_row:
                continue
            dates = same_row
        else:
            dates = _header_dates(tr, trs[i + 1] if i + 1 < len(trs) else None)
        if dates:
            # 每個日期欄落在哪個「X Months Ended」群組的欄範圍裡
            periods = []
            for col, d in dates:
                months = next((m for a, b, m in groups if a <= col < b), None)
                if months:
                    periods.append((d, months))
            continue
        if not periods:
            continue
        cells = _cells(tr)
        if not cells:
            continue
        label = _text(cells[0])
        if not label:
            continue
        nums = [v for v in (_num(c) for c in cells[1:]) if v is not None]
        # 數字個數要剛好等於期間數。對不上代表這一列的欄位結構不同（例如多了
        # Change 欄），硬對會把去年同期的值寫成本期
        if len(nums) != len(periods):
            continue
        # 判斷是不是百分比列之前要先去掉註腳符號：NVDA 寫「Non-GAAP gross margin*」，
        # 星號會讓 endswith("margin") 失效，那一列就被當成金額存起來，
        # 跟 SEC 的毛利金額一比會差好幾個數量級
        low = re.sub(r"[\s*†‡§¹²³(){}\[\]0-9.]+$", "", label.lower())
        yield periods, {"label": label, "values": nums,
                        "is_pct": low.endswith(("percentage", "margin", "rate", "ratio"))}


def pair_rows(rows: list[dict]) -> list[tuple[dict, list[dict], dict]]:
    """把「GAAP 起點 → 調整項 → Non-GAAP 結果」配成三元組。

    靠的是 Non-GAAP 列的名字：Adjusted gross margin 去掉前綴就是 Gross margin，
    往回找同名的 GAAP 列，中間夾的就是調整項。
    """
    out = []
    for i, r in enumerate(rows):
        m = NONGAAP_PREFIX.match(r["label"])
        if not m:
            continue
        base = norm_label(r["label"])
        if not base:
            continue
        # 往回找同名的 GAAP 列。窗開到 20 列：調整項有時列得很細。
        # 先找完全相同，找不到再退到「其中一邊是另一邊的開頭」——ABBV 的 GAAP 列是
        # 「Diluted earnings per share attributable to AbbVie Inc.」，Non-GAAP 列是
        # 「Adjusted diluted earnings per share」，字面不等但講的是同一件事。
        cand = None
        for exact in (True, False):
            for j in range(i - 1, max(-1, i - 20), -1):
                if NONGAAP_PREFIX.match(rows[j]["label"]):
                    continue
                other = norm_label(rows[j]["label"])
                if not other:
                    continue
                hit = (other == base) if exact else (
                    (other.startswith(base) or base.startswith(other))
                    # 長度落差太大就不是同一個科目，避免「Revenue」配到「Revenue by segment」
                    and min(len(other), len(base)) / max(len(other), len(base)) > 0.5)
                if hit:
                    cand = j
                    break
            if cand is not None:
                break
        if cand is None:
            continue
        # 夾在中間的才是調整項；百分比列（Operating margin、Percent of revenue）
        # 是同一個科目的另一種表示，加進去會把 23.1 當成金額
        adjs = [a for a in rows[cand + 1:i] if not a.get("is_pct")]
        out.append((rows[cand], adjs, r))
    return out


def earnings_exhibits(cik: int, limit: int) -> list[dict]:
    """財報申報的附件。

    美國發行人用 8-K + Item 2.02（Results of Operations）。
    外國發行人（ARM/ASML/ASX/CAMT/SAP/TSM）**不交 8-K，交 6-K**，而且 6-K 沒有
    items 欄位可以篩——只認 8-K 的話這 6 檔會直接抓不到任何附件。
    """
    sub = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    r = sub["filings"]["recent"]
    out = []
    for i in range(len(r["form"])):
        if len(out) >= limit:
            break
        form = r["form"][i]
        items = (r.get("items") or [""] * len(r["form"]))[i] or ""
        if form == "8-K":
            if "2.02" not in items:
                continue
        elif form == "6-K":
            pass                    # 6-K 沒有 items，附件內容再由對帳表解析判斷
        else:
            continue
        acc = r["accessionNumber"][i].replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
        try:
            idx = fetch(base + "/")
        except Exception:
            continue
        links = re.findall(r'href="(/Archives/[^"]*\.htm)"', idx)
        ex = [u for u in links
              if not re.search(r"/R\d+\.htm$", u)
              and not re.search(r"[a-z]+-\d{8}\.htm$", u)]
        if ex:
            out.append({"accession": r["accessionNumber"][i], "filed": r["filingDate"][i],
                        "urls": ["https://www.sec.gov" + u for u in ex]})
    return out


def extract(ticker: str, cik: int, limit: int, sleep: float, show: bool) -> list[tuple]:
    now = datetime.now().isoformat(timespec="seconds")
    rows_out = []
    for filing in earnings_exhibits(cik, limit):
        for url in filing["urls"]:
            try:
                doc = fetch(url)
            except Exception:
                continue
            time.sleep(sleep)
            # 依期間組把資料列分堆，同一組裡才配對 GAAP → Non-GAAP
            groups: dict[tuple, list[dict]] = {}
            for periods, row in walk_rows(doc):
                groups.setdefault(tuple(periods), []).append(row)
            for periods, rows in groups.items():
                for gaap, adjs, ng in pair_rows(rows):
                    for k, (pend, months) in enumerate(periods):
                        if k >= len(ng["values"]) or k >= len(gaap["values"]):
                            continue
                        rows_out.append((
                            ticker, pend, months, gaap["label"], gaap["values"][k],
                            ng["label"], ng["values"][k],
                            json.dumps([{"label": a["label"], "value": a["values"][k]}
                                        for a in adjs if k < len(a["values"])],
                                       ensure_ascii=False),
                            # GAAP + 調整項與 Non-GAAP 的差額。理論上是 0；不是 0
                            # 代表有調整項沒被收進來（某欄是「—」時整列會被跳過）。
                            # 存下來讓它可被稽核，而不是靜靜地漏掉
                            round(ng["values"][k] - gaap["values"][k]
                                  - sum(a["values"][k] for a in adjs
                                        if k < len(a["values"])), 2),
                            "pct" if ng.get("is_pct") else "amount",
                            filing["accession"], filing["filed"], url, now))
                        if show and k == 0:
                            print(f"    {pend} {months:>2}月  {gaap['label'][:28]:<28}"
                                  f" {gaap['values'][k]:>13,.1f} → {ng['label'][:28]:<28}"
                                  f" {ng['values'][k]:>13,.1f}")
    return rows_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--filings", type=int, default=4)
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # 全庫 259 檔的 CIK 對照（resolve_ciks.py 建），沒有才退回 registry 的 49 檔
    targets = []
    try:
        vc = sqlite3.connect(f"file:{VALUATION_DB}?mode=ro", uri=True)
        q = "SELECT ticker, cik FROM stock_cik WHERE cik IS NOT NULL"
        targets = sorted(vc.execute(q + (" AND ticker=?" if a.ticker else ""),
                                    (a.ticker.upper(),) if a.ticker else ()).fetchall())
        vc.close()
    except sqlite3.Error:
        pass
    if not targets:
        reg = sqlite3.connect(f"file:{REGISTRY_DB}?mode=ro", uri=True)
        q = "SELECT ticker, cik FROM companies WHERE cik IS NOT NULL"
        targets = sorted(reg.execute(q + (" AND ticker=?" if a.ticker else ""),
                                     (a.ticker.upper(),) if a.ticker else ()).fetchall())
        reg.close()

    con = None
    if not a.dry_run:
        con = sqlite3.connect(VALUATION_DB)
        con.executescript(SCHEMA)
        have = {r[1] for r in con.execute("PRAGMA table_info(nongaap_reconciliation)")}
        if "residual" not in have:      # CREATE IF NOT EXISTS 不會補欄位
            con.execute("ALTER TABLE nongaap_reconciliation ADD COLUMN residual REAL")
        con.commit()

    total = 0
    for tk, cik in targets:
        try:
            rows = extract(tk, cik, a.filings, a.sleep, a.show)
        except Exception as e:
            print(f"{tk:<6} ✗ {type(e).__name__}: {e}")
            continue
        pairs = len({(r[1], r[5]) for r in rows})
        print(f"{tk:<6} {len(rows):>4} 列（{pairs} 組對帳 × 期間）")
        total += len(rows)
        if con is not None and rows:
            con.execute("DELETE FROM nongaap_reconciliation WHERE ticker=?", (tk,))
            con.executemany(
                "INSERT OR REPLACE INTO nongaap_reconciliation "
                "(ticker,period_end,period_months,gaap_label,gaap_value,nongaap_label,"
                " nongaap_value,adjustments,residual,unit,accession,filed,source_url,"
                " fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
    if con is not None:
        con.close()
    print(f"\n{len(targets)} 檔 → {total:,} 列" + (" (dry-run)" if a.dry_run else ""))


if __name__ == "__main__":
    main()
