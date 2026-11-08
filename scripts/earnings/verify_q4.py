#!/usr/bin/env python3
"""驗證推算出來的第四季，拿 stockanalysis 的季度財報對答案。

背景：SEC 的 XBRL 裡沒有第四季——10-K 呈現的是全年，Q4 從來沒被當成獨立期間
標記過（49 檔實測，原生 Q4 事實 0 筆）。所以 derive_q4.py 用「全年 − 前三季」
補出來。那在會計上是恆等式、不是估計，但值得拿外部資料實際驗一次。

stockanalysis 的季度財報端點有 Q4，而且帶 fiscalQuarter 標記：
    stockanalysis.com/stocks/{t}/financials/__data.json?p=quarterly

比對只針對「我標了 derived=1」的期間。相符就代表減法在實務上沒踩到重述的坑。

用法:
  verify_q4.py                  全部
  verify_q4.py --ticker AAPL    單檔
  verify_q4.py --tolerance 0.01 容許誤差（預設 0.5%）
"""
import argparse
import json
import pathlib
import sqlite3
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALUATION_DB = ROOT / "backend" / "stock_valuation.db"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# 對方欄位 → 我的 XBRL 概念候選（先命中的先用）
FIELDS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenuesNetOfInterestExpense", "RevenueFromContractsWithCustomers",
                "Revenue", "SalesRevenueNet"],
    "gp": ["GrossProfit"],
    "opinc": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "netinccmn": ["NetIncomeLoss", "ProfitLoss"],
}


def fetch(url: str, tries: int = 3, timeout: int = 30) -> bytes:
    last = None
    for i in range(tries):
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout).read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def resolve(arr, idx, depth=0):
    if depth > 14 or idx is None or idx == -1:
        return None
    node = arr[idx] if isinstance(idx, int) and 0 <= idx < len(arr) else idx
    if isinstance(node, dict):
        return {k: resolve(arr, v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(arr, v, depth + 1) for v in node]
    return node


def their_quarters(ticker: str) -> dict[str, dict]:
    """→ {period_end: {field: value}}，只取有 fiscalQuarter 標記的那張主表。"""
    raw = fetch(f"https://stockanalysis.com/stocks/{ticker.lower()}"
                f"/financials/__data.json?p=quarterly")
    d = json.loads(raw)
    nodes = [n for n in d.get("nodes", []) if isinstance(n, dict) and n.get("type") == "data"]
    if not nodes:
        return {}
    root = resolve(max(nodes, key=lambda n: len(n.get("data") or []))["data"], 0)
    for sec in (root or {}).get("sections", []):
        data = (sec or {}).get("data") or {}
        if "datekey" not in data or "revenue" not in data:
            continue
        out = {}
        for i, pend in enumerate(data["datekey"]):
            row = {}
            for f in FIELDS:
                vals = data.get(f)
                if isinstance(vals, list) and i < len(vals) and vals[i] is not None:
                    row[f] = vals[i]
            row["fq"] = (data.get("fiscalQuarter") or [None] * (i + 1))[i]
            out[pend] = row
        return out
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--tolerance", type=float, default=0.005)
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args()

    con = sqlite3.connect(VALUATION_DB)
    con.row_factory = sqlite3.Row
    tickers = ([a.ticker.upper()] if a.ticker else
               [r[0] for r in con.execute(
                   "SELECT DISTINCT ticker FROM financial_facts WHERE derived=1 ORDER BY ticker")])

    ok = bad = skip = 0
    problems = []
    for tk in tickers:
        try:
            theirs = their_quarters(tk)
        except Exception as e:
            print(f"{tk:<6} ✗ 取不到：{type(e).__name__}")
            time.sleep(a.sleep)
            continue
        if not theirs:
            print(f"{tk:<6} ✗ 解不出季度表")
            time.sleep(a.sleep)
            continue

        # 用「這家公司損益表自己使用的概念」，不是固定順序。
        # 固定順序會挑錯：SOFI 是銀行，總營收記在 RevenuesNetOfInterestExpense，
        # 拿 RevenueFromContractWithCustomer 去比只會比到技術服務那一小塊（0.17B
        # vs 實際 1.03B）；MA 同理。損益表結構是各公司自己排的，照它挑最準。
        stmt_concepts = [r[0] for r in con.execute(
            "SELECT concept FROM financial_line_items "
            "WHERE ticker=? AND statement='income' ORDER BY order_idx", (tk,))]
        preferred = {f: next((c for c in stmt_concepts if c in set(cands)), None)
                     for f, cands in FIELDS.items()}

        mine = {}
        for r in con.execute(
                "SELECT concept, period_end, value FROM financial_facts "
                "WHERE ticker=? AND derived=1", (tk,)):
            mine.setdefault(r["period_end"], {})[r["concept"]] = r["value"]

        n_ok = n_bad = 0
        for pend, concepts in mine.items():
            t = theirs.get(pend)
            if not t:
                skip += 1
                continue
            for field, cands in FIELDS.items():
                if field not in t:
                    continue
                pref = preferred.get(field)
                mv = (concepts.get(pref) if pref in concepts else
                      next((concepts[c] for c in cands if c in concepts), None))
                if mv is None:
                    continue
                tv = t[field]
                if not tv:
                    continue
                diff = abs(mv - tv) / abs(tv)
                if diff <= a.tolerance:
                    n_ok += 1
                else:
                    n_bad += 1
                    problems.append((tk, pend, t.get("fq"), field, mv, tv, diff))
        ok += n_ok
        bad += n_bad
        mark = "✓" if n_bad == 0 else f"✗ {n_bad} 項不符"
        print(f"{tk:<6} 比對 {n_ok + n_bad:>3} 項 {mark}")
        time.sleep(a.sleep)

    # 結果存表，之後可以稽核而不用重打對方伺服器
    con.executescript("""
    CREATE TABLE IF NOT EXISTS q4_verification (
      ticker TEXT, period_end TEXT, field TEXT,
      ours REAL, theirs REAL, diff_pct REAL, verdict TEXT,
      PRIMARY KEY (ticker, period_end, field)
    );""")
    con.execute("DELETE FROM q4_verification")
    con.executemany(
        "INSERT OR REPLACE INTO q4_verification "
        "(ticker,period_end,field,ours,theirs,diff_pct,verdict) VALUES (?,?,?,?,?,?,?)",
        [(tk, pend, field, mv, tv, diff, "mismatch")
         for tk, pend, fq, field, mv, tv, diff in problems])
    con.commit()
    con.close()

    print(f"\n相符 {ok}、不符 {bad}、對方沒有該期間 {skip}")
    if problems:
        from collections import Counter
        by_field = Counter(f for *_, f, _, _, _ in
                           ((a, b, c, d, e, g, h) for a, b, c, d, e, g, h in problems))
        print("\n不符的欄位分布（全部，不是只有前 25 筆）:")
        for f, n in Counter(p[3] for p in problems).most_common():
            print(f"  {f:<12} {n}")
        print("\n差異最大的 10 筆:")
        for tk, pend, fq, field, mv, tv, diff in sorted(problems, key=lambda x: -x[6])[:10]:
            print(f"  {tk:<6} {pend} {fq or '':<3} {field:<10} "
                  f"我 {mv/1e9:>9,.2f}B  對方 {tv/1e9:>9,.2f}B  差 {diff*100:>7.2f}%")


if __name__ == "__main__":
    main()
