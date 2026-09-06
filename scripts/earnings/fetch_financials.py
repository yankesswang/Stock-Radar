#!/usr/bin/env python3
"""抓個股的三大財報表（損益 / 資產負債 / 現金流量），十年以上歷史。

兩個來源各取所長，缺一不可：

  結構 ← SEC 申報的 R-file 報表
      FilingSummary.xml 用 <MenuCategory>Statements</MenuCategory> 標出三大表
      （R2 損益、R4 資產負債、R6 現金流量）。從那裡拿到**有序、有中文可對照的
      標籤、有小計層級**的科目清單。companyfacts 只給概念名與數字，沒有順序也
      沒有「哪幾條加起來等於毛利」這種結構。

  數值 ← XBRL companyfacts API
      一次請求拿到該公司每個概念的完整歷史（MSFT 的毛利有 79 個季度、回到 2007）。
      用 R-file 逐份申報去湊十年要幾百個請求，這裡只要一個。

寫進 backend/stock_valuation.db（估值庫）的兩張表：
  financial_line_items  結構：哪張表、第幾列、概念、標籤、是不是小計
  financial_facts       數值：每個概念的每一期（季度 3 個月 / 年度 12 個月 /
                        資產負債表的時點值）

用法:
  fetch_financials.py                  registry 裡有 CIK 的公司
  fetch_financials.py --ticker MSFT    單檔
  fetch_financials.py --dry-run --show 只印不寫
"""
import argparse
import datetime as dt
import html
import json
import pathlib
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTRY_DB = ROOT / "data" / "earnings_registry.db"
VALUATION_DB = ROOT / "backend" / "stock_valuation.db"
UA = "arthur-stock-radar research yankesswang@gmail.com"

SCHEMA = """
CREATE TABLE IF NOT EXISTS financial_line_items (
  ticker TEXT, statement TEXT, order_idx INTEGER,
  concept TEXT, label TEXT, is_total INTEGER, depth INTEGER,
  source_form TEXT, fetched_at TEXT,
  PRIMARY KEY (ticker, statement, concept)
);
CREATE TABLE IF NOT EXISTS financial_facts (
  ticker TEXT, concept TEXT, period_end TEXT, period_months INTEGER,
  value REAL, unit TEXT, form TEXT, filed TEXT, fy INTEGER, fp TEXT,
  PRIMARY KEY (ticker, concept, period_end, period_months)
);
CREATE INDEX IF NOT EXISTS idx_ffacts_ticker_period
  ON financial_facts (ticker, period_end DESC);
"""

# 報表名稱 → 三大表。刻意排除的：Parenthetical（只有股數面額之類的附註）、
# Comprehensive（綜合損益不是主損益表）、Equity（股東權益變動表不在三大表）
STATEMENTS = [
    # NOW 與 ASX 的損益表就叫 "Statements of Comprehensive Income"——沒有別的
    # 損益表可選，所以第二段（allow_comprehensive）必須認得這個名字
    ("income",   re.compile(r"(income statement|statements? of (operations|income|earnings|"
                            r"comprehensive income)|statements? of profit or loss)", re.I)),
    ("balance",  re.compile(r"(balance sheet|statements? of financial position)", re.I)),
    ("cashflow", re.compile(r"cash flow", re.I)),
]
# Parenthetical 只有股數面額之類的附註，權益變動表不在三大表，兩者一律排除。
STMT_SKIP = re.compile(r"(parenthetical|equity|deficit)", re.I)
# 「comprehensive」不能無條件排除：很多公司的損益表就叫
#   Consolidated Statements of Operations and Comprehensive Income（SOFI/DHI）
#   Consolidated Statements of Comprehensive Income（NOW/ASX）
#   CONSOLIDATED STATEMENTS OF PROFIT OR LOSS AND OTHER COMPREHENSIVE INCOME（TSM）
# 直接濾掉會讓這 8 檔完全沒有損益表，營收只能退回別名猜——SOFI 因此只抓到
# 非利息收入 0.6B，實際 4.3B。改成優先挑不含 comprehensive 的，沒有才用它。
STMT_COMPREHENSIVE = re.compile(r"comprehensive", re.I)


def fetch(url: str, tries: int = 3, timeout: int = 45) -> bytes:
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


def latest_annual(cik: int) -> dict | None:
    """最近一份年報（10-K / 20-F）。結構取年報而不是季報，科目最完整。"""
    sub = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    r = sub["filings"]["recent"]
    for i, form in enumerate(r["form"]):
        if form in ("10-K", "20-F"):
            return {"form": form, "accession": r["accessionNumber"][i],
                    "filed": r["filingDate"][i]}
    return None


def statement_structure(cik: int, filing: dict, sleep: float) -> dict[str, list[dict]]:
    """→ {statement: [{concept, label, is_total, depth}, …]}，順序照申報上的排版。"""
    accn = filing["accession"].replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}"
    try:
        fs = fetch(f"{base}/FilingSummary.xml").decode("utf-8", "replace")
    except Exception:
        return {}

    candidates: list[tuple[str, str]] = []       # (報表名, 檔名)
    for rep in re.findall(r"<Report[^>]*>(.*?)</Report>", fs, flags=re.S):
        cat = re.search(r"<MenuCategory>(.*?)</MenuCategory>", rep)
        n = re.search(r"<ShortName>(.*?)</ShortName>", rep)
        f = re.search(r"<HtmlFileName>(.*?)</HtmlFileName>", rep)
        if not (cat and n and f) or cat.group(1) != "Statements":
            continue
        name = html.unescape(n.group(1))
        if STMT_SKIP.search(name):
            continue
        candidates.append((name, f.group(1)))

    picked: dict[str, str] = {}
    # 兩段式：先挑名稱不含 comprehensive 的，剩下沒配到的才允許用含它的
    for allow_comprehensive in (False, True):
        for name, fn in candidates:
            if not allow_comprehensive and STMT_COMPREHENSIVE.search(name):
                continue
            for key, pat in STATEMENTS:
                if key not in picked and pat.search(name):
                    picked[key] = fn
                    break

    out: dict[str, list[dict]] = {}
    for key, fn in picked.items():
        try:
            raw = fetch(f"{base}/{fn}").decode("utf-8", "replace")
        except Exception:
            continue
        rows, seen = [], set()
        for tr in re.findall(r"<tr[^>]*>.*?</tr>", raw, flags=re.S):
            cls = re.match(r'<tr[^>]*class="([^"]*)"', tr)
            klass = cls.group(1) if cls else ""
            cells = re.findall(r"<t[dh][^>]*>.*?</t[dh]>", tr, flags=re.S)
            if not cells:
                continue
            d = re.search(r"defref_([A-Za-z0-9_\-]+)", cells[0])
            if not d:
                continue                      # 沒有概念名的列是標題或空白列
            concept = d.group(1).split("_", 1)[-1]
            if concept in seen:
                continue
            seen.add(concept)
            label = _text(cells[0])
            if not label or label.endswith(("[Abstract]", "[Line Items]")):
                continue
            pad = re.search(r"padding-left:\s*(\d+)", cells[0])
            rows.append({
                "concept": concept, "label": label,
                # reu / rou 是加了上框線的小計列（毛利、營業利益、稅前淨利）
                "is_total": int(klass in ("reu", "rou")),
                "depth": int(int(pad.group(1)) / 12) if pad else 0,
            })
        if rows:
            out[key] = rows
        time.sleep(sleep)
    return out


# 只抓「當期損益表上有的概念」會漏掉歷史：ASC606（2018）之前營收記在
# SalesRevenueNet，那個概念不在今天的結構裡，於是 AAPL 19 個年度只有 9 期有營收。
# IFRS 公司（TSM/ASX/SAP）整套概念名都不同，同樣抓不到。
# 這組別名固定一起抓，補齊關鍵指標的長期歷史。
EXTRA_CONCEPTS = {
    # 美國 GAAP 的歷史寫法
    "SalesRevenueNet", "SalesRevenueGoodsNet", "SalesRevenueServicesNet",
    # 金融業
    "RevenuesNetOfInterestExpense", "InterestIncomeExpenseNet", "NoninterestIncome",
    "InterestAndDividendIncomeOperating", "InterestIncomeOperating",
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "CostOfGoodsSold", "CostOfServices", "CostOfGoodsAndServicesSold", "CostOfRevenue",
    "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss", "ProfitLoss",
    "EarningsPerShareDiluted", "WeightedAverageNumberOfDilutedSharesOutstanding",
    "ResearchAndDevelopmentExpense", "SellingGeneralAndAdministrativeExpense",
    "Assets", "Liabilities", "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "AssetsCurrent", "LiabilitiesCurrent", "InventoryNet", "AccountsReceivableNetCurrent",
    "CashAndCashEquivalentsAtCarryingValue",
    "LongTermDebtNoncurrent", "LongTermDebtCurrent", "LongTermDebt", "DebtCurrent",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "DepreciationDepletionAndAmortization", "ShareBasedCompensation",
    # IFRS
    "Revenue", "RevenueFromContractsWithCustomers", "CostOfSales",
    "GrossProfitIfrs", "ProfitLossFromOperatingActivities",
    "ProfitLossAttributableToOwnersOfParent",
    "Equity", "EquityAttributableToOwnersOfParent", "Liabilities",
    "CurrentAssets", "CurrentLiabilities", "Inventories", "TradeAndOtherCurrentReceivables",
    "CashAndCashEquivalents", "CashFlowsFromUsedInOperatingActivities",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "DepreciationAndAmortisationExpense",
    "BasicEarningsLossPerShare", "DilutedEarningsLossPerShare",
}


# 只保留近 N 年。全庫 259 檔 × 20 年會讓 financial_facts 長到百萬列、DB 逼近 1GB，
# 而十年以前的季度數字對現在的判斷幾乎沒有作用（公司結構、會計準則都變過）。
KEEP_YEARS = 10


def cutoff_date(years: int = KEEP_YEARS) -> str:
    today = dt.date.today()
    return today.replace(year=today.year - years).isoformat()


def _months(start: str, end: str) -> int | None:
    """期間長度歸類成 3 / 6 / 9 / 12 個月；對不上的（52/53 週會差幾天）不猜。"""
    n = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
    for m, lo, hi in ((3, 80, 100), (6, 170, 190), (9, 260, 285), (12, 350, 380)):
        if lo <= n <= hi:
            return m
    return None


def company_facts(cik: int, concepts: set[str]) -> list[dict]:
    """companyfacts 裡屬於這些概念的所有期間值。

    同一期間會被多份申報重報（原始 + 重述），照 filed 取最新的那一版。
    """
    data = json.loads(fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"))
    best: dict[tuple, dict] = {}
    for taxonomy, facts in data.get("facts", {}).items():
        if taxonomy == "dei":
            continue
        for concept, body in facts.items():
            if concept not in concepts:
                continue
            for unit, vals in body.get("units", {}).items():
                for v in vals:
                    end = v.get("end")
                    if not end:
                        continue
                    months = _months(v["start"], end) if v.get("start") else 0   # 0 = 時點值
                    if months is None:
                        continue
                    key = (concept, end, months)
                    prev = best.get(key)
                    if prev and prev["filed"] >= v.get("filed", ""):
                        continue
                    best[key] = {"concept": concept, "period_end": end,
                                 "period_months": months, "value": v.get("val"),
                                 "unit": unit, "form": v.get("form", ""),
                                 "filed": v.get("filed", ""), "fy": v.get("fy"),
                                 "fp": v.get("fp", "")}
    return list(best.values())


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
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--years", type=int, default=KEEP_YEARS,
                    help="只保留近幾年的期間（預設 10）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    tgts = targets(a.ticker)
    if not tgts:
        print("no targets with CIK")
        return

    con = None
    if not a.dry_run:
        con = sqlite3.connect(VALUATION_DB)
        con.executescript(SCHEMA)

    now = dt.datetime.now().isoformat(timespec="seconds")
    for tk, cik in tgts:
        try:
            filing = latest_annual(cik)
            if not filing:
                print(f"{tk:<6} ✗ 沒有年報")
                continue
            struct = statement_structure(cik, filing, a.sleep)
            if not struct:
                print(f"{tk:<6} ✗ 抓不到三大表結構（{filing['form']}）")
                continue
            concepts = {r["concept"] for rows in struct.values() for r in rows} | EXTRA_CONCEPTS
            facts = [f for f in company_facts(cik, concepts)
                     if f["period_end"] >= cutoff_date(a.years)]
        except Exception as e:
            print(f"{tk:<6} ✗ {type(e).__name__}: {e}")
            continue

        periods = {f["period_end"] for f in facts}
        oldest = min(periods) if periods else "-"
        print(f"{tk:<6} {filing['form']:<5} "
              + " ".join(f"{k}:{len(v)}" for k, v in sorted(struct.items()))
              + f" | 事實 {len(facts):>5} 期間 {len(periods):>3} 最舊 {oldest}")
        if a.show:
            for k, rows in sorted(struct.items()):
                print(f"    [{k}] " + " / ".join(r["label"][:14] for r in rows[:6]))

        if con is not None:
            con.execute("DELETE FROM financial_line_items WHERE ticker=?", (tk,))
            con.execute("DELETE FROM financial_facts WHERE ticker=?", (tk,))
            con.executemany(
                "INSERT OR REPLACE INTO financial_line_items "
                "(ticker,statement,order_idx,concept,label,is_total,depth,source_form,fetched_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                [(tk, stmt, i, r["concept"], r["label"], r["is_total"], r["depth"],
                  filing["form"], now)
                 for stmt, rows in struct.items() for i, r in enumerate(rows)])
            con.executemany(
                "INSERT OR REPLACE INTO financial_facts "
                "(ticker,concept,period_end,period_months,value,unit,form,filed,fy,fp)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(tk, f["concept"], f["period_end"], f["period_months"], f["value"],
                  f["unit"], f["form"], f["filed"], f["fy"], f["fp"]) for f in facts])
            con.commit()
        time.sleep(a.sleep)

    if con is not None:
        con.close()
    print(f"\n{len(tgts)} 檔" + (" (dry-run)" if a.dry_run else f" → {VALUATION_DB}"))


if __name__ == "__main__":
    main()
