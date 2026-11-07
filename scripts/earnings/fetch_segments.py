#!/usr/bin/env python3
"""從 SEC 申報抓各部門的營收與利潤，寫進 valuation 資料庫。

為什麼不走 XBRL companyfacts API：那支 API 只回合併數，事實欄位裡沒有任何維度
（accn/end/filed/form/fp/fy/start/val），分部門的軸與成員全被剝掉了。分部門資料
在申報檔自己產生的 R-file 報表裡（FilingSummary.xml 列出），那才是唯一拿得到
「Google Cloud 營收 24,768」這種顆粒度的地方。

R-file 的結構很規矩，解析全程確定性、不用 LLM：
  表頭列一：標題（含 "$ in Millions" 倍率）＋ <th colspan=N>3 Months Ended</th>
  表頭列二：每欄一個期間結束日
  內文 tr.rh：部門名（Productivity and Business Processes）
  內文 tr.re/ro：科目列，td.pl 是標籤、td.nump 是數字
  每個標籤都帶 defref_us-gaap_XXX，直接給到 XBRL 概念名，不必靠顯示文字猜

寫進 backend/stock_valuation.db 的 segment_financials 表（跟估值/財報數據同一個庫）。

用法:
  fetch_segments.py                     registry 裡有 CIK 的公司（預設 49 檔）
  fetch_segments.py --ticker MSFT       單檔
  fetch_segments.py --filings 8         每檔往回抓 8 份申報（預設 4）
  fetch_segments.py --dry-run --show    只印不寫
"""
import argparse
import html
import json
import pathlib
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, date
from datetime import date as dt_date

ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTRY_DB = ROOT / "data" / "earnings_registry.db"
VALUATION_DB = ROOT / "backend" / "stock_valuation.db"   # backend 實際在用的那個
UA = "arthur-stock-radar research yankesswang@gmail.com"

SCHEMA = """
CREATE TABLE IF NOT EXISTS segment_financials (
  ticker TEXT, cik INTEGER, form TEXT, accession TEXT, filed TEXT,
  period_end TEXT, period_months INTEGER,
  segment TEXT, line_item TEXT, concept TEXT,
  value REAL, scale INTEGER, currency TEXT, report_name TEXT, fetched_at TEXT,
  PRIMARY KEY (ticker, accession, report_name, segment, concept, period_end, period_months, currency)
);
CREATE INDEX IF NOT EXISTS idx_segfin_ticker_period
  ON segment_financials (ticker, period_end DESC);
"""

# 只要「明細」表，narrative/policy 那些是文字沒有數字。
# 兩個踩過的坑：WDAY 的報表叫「Revenues by Geographic Area」（複數，單數比對抓不到）；
# UMAC 的分部門營收藏在「Summary of significant accounting policies (Details -
# Disaggregated by revenue)」裡，被 polic 規則誤殺——所以 disaggregat 一律豁免。
WANT = re.compile(r"(segment|disaggregat|revenues? by|net revenue|sales by|by geograph|by product)", re.I)
SKIP = re.compile(r"(narrative|polic|additional information|\(tables\))", re.I)
SKIP_EXEMPT = re.compile(r"disaggregat", re.I)
MONTHS = re.compile(r"(\d+)\s*months?\s*ended", re.I)
# 表頭日期用抓的不用整串 parse：ASX 的格子是 "Dec. 31, 2025 TWD ($)"，
# strptime 整串會失敗，整張表就被判定沒有期間而丟掉
DATE_RE = re.compile(r"([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),\s*(\d{4})")
CCY_RE = re.compile(r"\b(USD|TWD|EUR|JPY|KRW|CNY|GBP|ILS|CHF|SEK)\b")
SCALES = [(re.compile(r"in\s*millions", re.I), 1_000_000),
          (re.compile(r"in\s*thousands", re.I), 1_000),
          (re.compile(r"in\s*billions", re.I), 1_000_000_000)]


def fetch(url: str, tries: int = 3, timeout: int = 30) -> bytes:
    """SEC 偶發連線失敗（掃描時 AAPL 就這樣掉了一次），重試不是可選的。"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def _text(frag: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", frag)).replace("\xa0", " ").strip()


def _num(cell: str):
    """R-file 的數字：$ 82,886 / (1,234) 負數 / 空白。"""
    t = _text(cell).replace("$", "").replace(",", "").strip()
    if not t or t in {"—", "-", "–"}:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?%?", t):
        return None
    if t.endswith("%"):
        return None                       # 百分比不是金額，另談
    v = float(t)
    return -v if neg else v


def parse_periods(trs: list[str]):
    """表頭 → (每個數字欄的 (期間結束日, 月數, 幣別), 金額倍率, 標籤欄數)。

    月數靠第一列 <th colspan=N>3 Months Ended</th> 的 colspan 展開；沒有這一列
    （單期間表）就只有日期、月數留 None，不亂猜。
    """
    scale = 1
    # 左邊的標籤欄不一定只佔一欄：表頭第一格的 colspan 就是答案。QCOM 的分部門表
    # 用 colspan=2，資料列因此比日期欄多一格；假設只有一欄就會整列左移，
    # 本季營收被寫成去年同期（$9,947M 跑到 2025-06-29）。
    label_cols = 1
    if trs:
        first = re.search(r"<th[^>]*>", trs[0])
        if first:
            cs = re.search(r'colspan="?(\d+)', first.group(0))
            if cs:
                label_cols = int(cs.group(1))
    title = _text(trs[0]) if trs else ""
    for pat, mul in SCALES:
        if pat.search(title):
            scale = mul
            break

    months: list[int | None] = []
    for th in re.findall(r"<th[^>]*>.*?</th>", trs[0], flags=re.S) if trs else []:
        label = _text(th)
        m = MONTHS.search(label)
        if not m:
            continue
        span = re.search(r'colspan="?(\d+)', th)
        months += [int(m.group(1))] * (int(span.group(1)) if span else 1)

    # 每一個表頭格都要佔一個位置，即使它不是日期。
    #
    # QCOM 的分部門表在日期欄之外多了一欄 numberOfBusinesses（單位欄）。原本只把
    # 「解析得出日期」的格子收進來，結果 4 個期間對上 5 個資料格，整列往左位移一格，
    # 本季營收 $9,947M 被寫成去年同期——數字全錯而且不會報錯。
    dates: list[str | None] = []
    ccys: list[str] = []
    for tr in trs[1:3]:
        cells = [_text(x) for x in re.findall(r"<th[^>]*>.*?</th>", tr, flags=re.S)]
        cand: list[str | None] = []
        cc: list[str] = []
        for c in cells:
            m = DATE_RE.search(c)
            parsed = None
            if m:
                norm = f"{m.group(1)} {m.group(2)} {m.group(3)}"
                for fmt in ("%b %d %Y", "%B %d %Y"):
                    try:
                        parsed = datetime.strptime(norm, fmt).date().isoformat()
                        break
                    except ValueError:
                        continue
            cand.append(parsed)
            mc = CCY_RE.search(c)
            cc.append(mc.group(1) if mc else "")
        if any(cand):
            dates, ccys = cand, cc
            break
    if not any(dates):
        return [], scale, label_cols
    # 「X Months Ended」群組永遠在左邊，右邊常多出沒有群組的時點欄（例如 DHI 那張
    # 表最後一欄是資產負債表日期）。原本要求兩邊長度相等、對不起來就整批設成
    # None，等於把整張表的月數丟掉——全庫 6,178 列因此在 API 上完全查不到。
    # 改成左對齊：能對到的就給，多出來的留 None。
    months = (months + [None] * len(dates))[:len(dates)]
    return list(zip(dates, months, ccys)), scale, label_cols


def parse_report(raw: str, report_name: str) -> list[dict]:
    trs = re.findall(r"<tr[^>]*>.*?</tr>", raw, flags=re.S)
    if len(trs) < 3:
        return []
    periods, scale, label_cols = parse_periods(trs)
    if not periods:
        return []

    out, segment = [], "（合併）"
    for tr in trs:
        cls = re.match(r'<tr[^>]*class="([^"]*)"', tr)
        klass = cls.group(1) if cls else ""
        cells = re.findall(r"<t[dh][^>]*>.*?</t[dh]>", tr, flags=re.S)
        if not cells:
            continue
        label = _text(cells[0])
        if klass == "rh":                 # 部門標題列：接下來的科目都屬於它
            # IFRS 申報把成員寫成「Packaging segment [member]」，原本「含 [ 就跳過」
            # 的規則會把整家 IFRS 公司的部門全部濾掉（ASX 就是這樣只剩合併數）
            name = re.sub(r"\[member\]", "", label, flags=re.I).strip()
            if name and not re.search(r"\[(abstract|line items)\]$", name, re.I):
                segment = name
            continue
        if not label or label.endswith("[Line Items]") or label.endswith("[Abstract]"):
            continue
        concept = ""
        d = re.search(r"defref_([A-Za-z0-9_\-]+)", cells[0])
        if d:
            concept = d.group(1)
        nums = [_num(c) for c in cells[label_cols:]]
        if all(v is None for v in nums):
            continue
        for (pend, pmon, ccy), val in zip(periods, nums):
            if val is None or pend is None:      # 非日期欄（單位欄之類）不是資料
                continue
            out.append({"period_end": pend, "period_months": pmon, "segment": segment,
                        "line_item": label, "concept": concept, "value": val,
                        "scale": scale, "currency": ccy, "report_name": report_name})
    return out


PERIODIC = ("10-Q", "10-K", "20-F")


def _collect(block: dict, cutoff: str | None, limit: int | None,
             out: list[dict]) -> bool:
    """從一份 submissions 區塊挑出定期報告。回傳 True 代表已經挖到夠舊/夠多了。"""
    for i, form in enumerate(block["form"]):
        if form not in PERIODIC:
            continue
        filed = block["filingDate"][i]
        if cutoff and filed < cutoff:
            return True
        out.append({"form": form, "accession": block["accessionNumber"][i], "filed": filed})
        if limit and len(out) >= limit:
            return True
    return False


def filings_for(cik: int, limit: int | None = None, years: int | None = None) -> list[dict]:
    """公司的定期報告清單。

    submissions API 的 `recent` 只放最近約 1000 筆（MSFT 是 25 份定期報告、回到
    2020）。要更長的歷史得往 `filings.files` 列出的分頁檔挖，那裡一路回到 2008。
    years 指定要回溯幾年；不給就照 limit 取最近幾份。
    """
    sub = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    cutoff = None
    if years:
        cutoff = (date.today().replace(year=date.today().year - years)).isoformat()
    out: list[dict] = []
    done = _collect(sub["filings"]["recent"], cutoff, limit, out)
    if not done and years:
        for f in sub["filings"].get("files", []):
            page = json.loads(fetch(f"https://data.sec.gov/submissions/{f['name']}"))
            time.sleep(0.12)
            if _collect(page, cutoff, limit, out):
                break
    return out


def segment_rows(cik: int, filing: dict, sleep: float) -> list[dict]:
    accn = filing["accession"].replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}"
    try:
        fs = fetch(f"{base}/FilingSummary.xml").decode("utf-8", "replace")
    except Exception:
        return []
    rows = []
    for rep in re.findall(r"<Report[^>]*>(.*?)</Report>", fs, flags=re.S):
        n = re.search(r"<ShortName>(.*?)</ShortName>", rep)
        f = re.search(r"<HtmlFileName>(.*?)</HtmlFileName>", rep)
        if not (n and f):
            continue
        name = html.unescape(n.group(1))
        if not WANT.search(name) or (SKIP.search(name) and not SKIP_EXEMPT.search(name)):
            continue
        try:
            raw = fetch(f"{base}/{f.group(1)}").decode("utf-8", "replace")
        except Exception:
            continue
        rows += parse_report(raw, name)
        time.sleep(sleep)
    for r in rows:
        r.update(form=filing["form"], accession=filing["accession"], filed=filing["filed"])
    return rows


def stored_accessions(tickers: list[str]) -> dict[str, set[str]]:
    """DB 裡每檔已經抓過的申報編號，用來跳過沒有新財報的公司。"""
    if not VALUATION_DB.exists():
        return {}
    con = sqlite3.connect(f"file:{VALUATION_DB}?mode=ro", uri=True)
    out: dict[str, set[str]] = {}
    try:
        for tk, accn in con.execute(
                "SELECT ticker, accession FROM segment_financials GROUP BY ticker, accession"):
            out.setdefault(tk, set()).add(accn)
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return out


def targets(arg_ticker: str | None) -> list[tuple[str, int]]:
    """要處理的 (ticker, cik)。

    優先用 stock_cik——那是 App 全庫 259 檔的對照（resolve_ciks.py 建的）。
    沒有那張表時退回 earnings_registry.companies 的 49 檔，維持舊行為。
    """
    try:
        con = sqlite3.connect(f"file:{VALUATION_DB}?mode=ro", uri=True)
        q = "SELECT ticker, cik FROM stock_cik WHERE cik IS NOT NULL"
        rows = con.execute(q + (" AND ticker=?" if arg_ticker else ""),
                           (arg_ticker.upper(),) if arg_ticker else ()).fetchall()
        con.close()
        if rows:
            return sorted(rows)
    except sqlite3.Error:
        pass
    con = sqlite3.connect(f"file:{REGISTRY_DB}?mode=ro", uri=True)
    q = "SELECT ticker, cik FROM companies WHERE cik IS NOT NULL"
    rows = con.execute(q + (" AND ticker=?" if arg_ticker else ""),
                       (arg_ticker.upper(),) if arg_ticker else ()).fetchall()
    con.close()
    return sorted(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--filings", type=int, default=4, help="每檔往回抓幾份申報")
    ap.add_argument("--years", type=int,
                    help="改用年份回溯（會挖 submissions 分頁檔，比 --filings 深很多）")
    ap.add_argument("--sleep", type=float, default=0.15, help="SEC 請求間隔秒數")
    ap.add_argument("--keep-years", type=int, default=10,
                    help="只保留近幾年的期間（0 = 全部）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--only-new", action="store_true",
                    help="最新申報已經抓過就跳過（每檔只花一次 submissions 查詢）")
    a = ap.parse_args()

    tgts = targets(a.ticker)
    if not tgts:
        print("no targets with CIK")
        return

    con = None
    if not a.dry_run:
        con = sqlite3.connect(VALUATION_DB, timeout=120)
        con.executescript(SCHEMA)
        have = {r[1] for r in con.execute("PRAGMA table_info(segment_financials)")}
        if "currency" not in have:        # 舊表沒這欄，CREATE IF NOT EXISTS 不會補
            con.execute("ALTER TABLE segment_financials ADD COLUMN currency TEXT")
        con.commit()

    now = datetime.now().isoformat(timespec="seconds")
    # 分部門資料一季才更新一次，每天重抓 49 檔＝上千個 SEC 請求純屬浪費。
    # --only-new 先用一個 submissions 查詢比對最新申報編號，沒新東西就整檔跳過。
    known = stored_accessions([t for t, _ in tgts]) if a.only_new else {}
    if a.years:
        print(f"回溯 {a.years} 年")
    total = skipped = 0
    for tk, cik in tgts:
        rows = []
        try:
            filings = filings_for(cik, None if a.years else a.filings, a.years)
            if a.only_new and filings and filings[0]["accession"] in known.get(tk, ()):
                skipped += 1
                continue
            for filing in filings:
                rows += segment_rows(cik, filing, a.sleep)
                time.sleep(a.sleep)
        except Exception as e:
            print(f"{tk:<6} ✗ {type(e).__name__}: {e}")
            continue

        segs = {r["segment"] for r in rows}
        periods = {r["period_end"] for r in rows}
        print(f"{tk:<6} {len(rows):>5} 列 | 部門 {len(segs):>2} | 期間 {len(periods):>2}"
              + (f" | {sorted(segs - {'（合併）'})[:3]}" if a.show else ""))
        total += len(rows)

        if a.keep_years:
            # 跟三大表同一條線：只留近 N 年，避免全庫展開後 DB 爆量
            cut = (dt_date.today().replace(year=dt_date.today().year - a.keep_years)).isoformat()
            rows = [r for r in rows if r["period_end"] >= cut]
        if con is not None and rows:
            con.execute("DELETE FROM segment_financials WHERE ticker=?", (tk,))
            con.executemany(
                "INSERT OR REPLACE INTO segment_financials "
                "(ticker,cik,form,accession,filed,period_end,period_months,segment,"
                " line_item,concept,value,scale,currency,report_name,fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(tk, cik, r["form"], r["accession"], r["filed"], r["period_end"],
                  r["period_months"], r["segment"], r["line_item"], r["concept"],
                  r["value"], r["scale"], r.get("currency", ""), r["report_name"], now)
                 for r in rows])
            con.commit()

    if con is not None:
        con.close()
    print(f"\n{len(tgts)} 檔 → {total} 列"
          + (f"（跳過 {skipped} 檔：沒有新申報）" if a.only_new else "")
          + (" (dry-run)" if a.dry_run else f" → {VALUATION_DB}"))


if __name__ == "__main__":
    main()
