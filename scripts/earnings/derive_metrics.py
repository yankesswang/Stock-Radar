#!/usr/bin/env python3
"""從 SEC 三大表算出基本面指標，補上 metrics 表空著的欄位。

現有的 metrics 表是爬來的快照：270 檔 × 約 24 個快照，只回溯到 2025-07，
而且 72 個欄位裡多數是空的（AAPL 只有 26 個有值，幾乎都是股價類的 PE/PS/PB）。
營收、毛利率、ROE、FCF 這些基本面欄位大多沒有資料。

這支腳本用 financial_facts（SEC 官方數字、十年以上）把基本面那半邊算出來，
寫進 financial_derived。股價類比率不算——那需要當下股價，現有 metrics 已經有了，
兩邊各司其職：
    metrics            股價、市值、PE/PS/PB/EV 倍數（每日快照）
    financial_derived  營收、獲利、利潤率、報酬率、現金流、財務結構（每個會計期間）

用法:
  derive_metrics.py                 全部
  derive_metrics.py --ticker AAPL   單檔
  derive_metrics.py --show          印出算出來的結果
"""
import argparse
import pathlib
import sqlite3

VALUATION_DB = pathlib.Path(__file__).resolve().parents[2] / "backend" / "stock_valuation.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS financial_derived (
  ticker TEXT, period_end TEXT, period_months INTEGER,
  revenue REAL, gross_profit REAL, operating_income REAL, pretax_income REAL,
  net_income REAL, eps_diluted REAL, shares_diluted REAL,
  gross_margin REAL, operating_margin REAL, pretax_margin REAL, profit_margin REAL,
  rnd_expense REAL, sga_expense REAL, rnd_intensity REAL,
  ncfo REAL, capex REAL, fcf REAL, fcf_margin REAL, sbc REAL, dna REAL,
  total_assets REAL, total_liabilities REAL, equity REAL,
  cash REAL, debt REAL, net_cash REAL, inventory REAL, receivables REAL,
  current_assets REAL, current_liabilities REAL,
  current_ratio REAL, debt_equity REAL, roe REAL, roa REAL, roic REAL,
  bvps REAL, asset_turnover REAL, debt_components TEXT, has_derived INTEGER,
  is_fiscal_year INTEGER,
  PRIMARY KEY (ticker, period_end, period_months)
);
"""

# 每個指標的候選概念，先到先得。同一件事各家用的標籤不同，靠概念名對才穩。
PICK = {
    "revenue": [
                # 金融業的總營收是「淨收入（扣除利息支出後）」，跟一般公司完全不同科目。
                # 少了這條，SOFI 只會抓到非利息收入裡的技術服務 0.6B，實際 4.3B。
                # 非金融業不會標這個概念，放最前面沒有副作用。
                "RevenuesNetOfInterestExpense",
                "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "RevenueFromContractsWithCustomers", "Revenue",
                # ASC606（2018）之前的寫法，補上才有長期歷史
                "SalesRevenueNet", "SalesRevenueGoodsNet", "SalesRevenueServicesNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "eps_diluted": ["EarningsPerShareDiluted", "DilutedEarningsLossPerShare"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "rnd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": ["SellingGeneralAndAdministrativeExpense"],
    "ncfo": ["NetCashProvidedByUsedInOperatingActivities",
             "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "sbc": ["ShareBasedCompensation"],
    "dna": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
            "Depreciation", "DepreciationAndAmortisationExpense"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               "EquityAttributableToOwnersOfParent", "Equity"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "_ifrs_current_assets": ["CurrentAssets"],
    "inventory": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "current_assets": ["AssetsCurrent", "CurrentAssets"],
    "current_liabilities": ["LiabilitiesCurrent", "CurrentLiabilities"],
    "inventory_ifrs": ["Inventories"],
    # 有息負債不能把所有找得到的科目一股腦相加——申報裡同時有「零件」與「總成」：
    # AAPL 同時報 LongTermDebtNoncurrent(78.3B)、LongTermDebtCurrent(12.3B) 與
    # LongTermDebt(90.7B)，而第三個就是前兩個的總和。全加會得到 190B，實際約 98.7B。
    #
    # 所以分組：每一組內部照優先序只取一個（總成優先於零件），組與組之間才相加。
    "_debt_groups": [
        # 長期債務：總成 → 零件 → IFRS 寫法
        [["LongTermDebt"],
         ["LongTermDebtNoncurrent", "LongTermDebtCurrent"],
         ["LongtermBorrowings", "CurrentPortionOfLongtermBorrowings"],
         ["BorrowingsNoncurrent"],
         ["SeniorNotes"],
         ["NotesPayable"],                       # DHI 這類建商用這個當總額
         ["NotesPayableCurrent", "NotesPayableNoncurrent"],
         # 成對的候選只找到一半時的後備。排在最後，只有前面全部湊不齊才用，
         # 寧可少算一半也不要把總成跟零件加在一起
         ["LongTermDebtNoncurrent"], ["LongTermDebtCurrent"],
         ["LongtermBorrowings"], ["CurrentPortionOfLongtermBorrowings"],
         ["NotesPayableNoncurrent"], ["NotesPayableCurrent"]],
        # 短期借款／商業本票：跟長期債務不重疊
        [["ShortTermBorrowings"], ["ShorttermBorrowings"],
         ["CommercialPaper"], ["LinesOfCreditCurrent"], ["DebtCurrent"]],
        # 可轉換債
        [["ConvertibleDebtNoncurrent", "ConvertibleDebtCurrent"],
         ["ConvertibleDebtNoncurrent"], ["ConvertibleDebtCurrent"]],
        # 融資租賃
        [["FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent"],
         ["FinanceLeaseLiabilityNoncurrent"]],
    ],
}


def safe_div(a, b):
    return (a / b) if (a is not None and b) else None


def build(con: sqlite3.Connection, ticker: str) -> list[dict]:
    rows = con.execute(
        "SELECT concept, period_end, period_months, value, fp, COALESCE(derived,0) derived "
        "FROM financial_facts WHERE ticker=?", (ticker,)).fetchall()
    if not rows:
        return []

    flow: dict[tuple, dict[str, float]] = {}     # (period_end, months) → concept → value
    instant: dict[str, dict[str, float]] = {}    # period_end → concept → value（資產負債表）
    # fp='FY' 的 12 個月才是會計年度。季報也會揭露滾動 12 個月（TTM），期間長度
    # 一樣是 12 個月——不分開的話 AMZN 會冒出 74 個「年度」，其實只有 19 個。
    fiscal_years: set[str] = set()
    derived_periods: set[tuple] = set()      # 哪些期間用到了推算的 Q4
    for concept, pend, months, val, fp, is_derived in rows:
        c = concept.split("_")[-1]
        if is_derived:
            derived_periods.add((pend, months))
        if months == 12 and fp == "FY":
            fiscal_years.add(pend)
        if months == 0:
            instant.setdefault(pend, {})[c] = val
        else:
            flow.setdefault((pend, months), {})[c] = val

    def pick(bag: dict, key: str):
        for c in PICK[key]:
            if c in bag and bag[c] is not None:
                return bag[c]
        return None

    out = []
    for (pend, months), bag in sorted(flow.items()):
        bs = instant.get(pend, {})               # 同一天的資產負債表；對不到就沒有存量指標
        d = {"ticker": ticker, "period_end": pend, "period_months": months,
             "is_fiscal_year": int(months == 12 and pend in fiscal_years),
             # 這一期的數字裡有沒有用到推算出來的 Q4——前端要標出來
             "has_derived": int((pend, months) in derived_periods)}
        for key in ("revenue", "gross_profit", "operating_income", "pretax_income",
                    "net_income", "eps_diluted", "shares_diluted", "rnd_expense",
                    "sga_expense", "ncfo", "capex", "sbc", "dna"):
            d[key] = pick(bag, key)
        for key in ("total_assets", "total_liabilities", "equity", "cash", "inventory",
                    "receivables", "current_assets", "current_liabilities"):
            d[key] = pick(bs, key)

        # 有息負債：把找得到的部分加總。全找不到才給 None，不要把 0 當成「沒有負債」。
        # 同時記下用到哪些科目——這個欄位是全表唯一「加總多個科目」的推算，
        # 成分寫出來才有辦法被稽核，而不是一個沒人知道怎麼來的數字。
        # 每組取第一個湊得齊的候選（避免總成與零件重複計），組間相加
        used: list[str] = []
        total = 0.0
        counted: list[float] = []
        for group in PICK["_debt_groups"]:
            for candidate in group:
                vals = [bs[c] for c in candidate if bs.get(c) is not None]
                if len(vals) != len(candidate):        # 這個候選的科目要齊才用
                    continue
                # 同一筆債常被兩個標籤同時報：VIAV 的可轉債 0.84B 也記在
                # LongTermDebt 底下，分屬不同組就會被加兩次。值一樣就當成同一筆。
                if any(any(abs(v - c) < max(1.0, abs(c) * 1e-6) for c in counted)
                       for v in vals):
                    break
                total += sum(vals)
                counted += vals
                used += candidate
                break
        d["debt"] = total if used else None
        d["debt_components"] = ",".join(used) if used else None
        d["net_cash"] = (d["cash"] - d["debt"]) if (d["cash"] is not None and d["debt"] is not None) else None

        rev = d["revenue"]
        d["gross_margin"] = safe_div(d["gross_profit"], rev)
        d["operating_margin"] = safe_div(d["operating_income"], rev)
        d["pretax_margin"] = safe_div(d["pretax_income"], rev)
        d["profit_margin"] = safe_div(d["net_income"], rev)
        d["rnd_intensity"] = safe_div(d["rnd_expense"], rev)

        # capex 在現金流量表是流出（正數表示付出去），FCF 要減掉
        d["fcf"] = (d["ncfo"] - abs(d["capex"])) if (d["ncfo"] is not None and d["capex"] is not None) else None
        d["fcf_margin"] = safe_div(d["fcf"], rev)

        d["current_ratio"] = safe_div(d["current_assets"], d["current_liabilities"])
        d["debt_equity"] = safe_div(d["debt"], d["equity"])
        d["bvps"] = safe_div(d["equity"], d["shares_diluted"])
        # 報酬率用期末餘額算（不是平均），跟多數看盤軟體一致，跨期比較才不會打架
        d["roe"] = safe_div(d["net_income"], d["equity"])
        d["roa"] = safe_div(d["net_income"], d["total_assets"])
        invested = ((d["equity"] or 0) + (d["debt"] or 0)) or None
        d["roic"] = safe_div(d["operating_income"], invested)
        d["asset_turnover"] = safe_div(rev, d["total_assets"])
        out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(VALUATION_DB)
    con.executescript(SCHEMA)
    # CREATE TABLE IF NOT EXISTS 對既有的表不會加新欄位，加欄位後直接寫入會炸在
    # "table has no column named ..."。這個坑在這個 repo 已經踩過三次了。
    have = {r[1] for r in con.execute("PRAGMA table_info(financial_derived)")}
    for raw in SCHEMA.split("(", 1)[1].split(","):
        parts = raw.strip().split()
        if len(parts) == 2 and parts[1] in ("TEXT", "REAL", "INTEGER") and parts[0] not in have:
            con.execute(f"ALTER TABLE financial_derived ADD COLUMN {parts[0]} {parts[1]}")
    con.commit()
    tickers = ([a.ticker.upper()] if a.ticker else
               [r[0] for r in con.execute("SELECT DISTINCT ticker FROM financial_facts ORDER BY ticker")])

    total = 0
    for tk in tickers:
        rows = build(con, tk)
        if not rows:
            print(f"{tk:<6} 無資料")
            continue
        cols = list(rows[0].keys())
        con.execute("DELETE FROM financial_derived WHERE ticker=?", (tk,))
        con.executemany(
            f"INSERT OR REPLACE INTO financial_derived ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [tuple(r[c] for c in cols) for r in rows])
        con.commit()
        total += len(rows)
        ann = [r for r in rows if r["is_fiscal_year"]]
        filled = sum(1 for r in ann if r.get("roe") is not None)
        print(f"{tk:<6} {len(rows):>4} 期（年度 {len(ann):>3}，其中 {filled} 期算得出 ROE）")
        if a.show and ann:
            r = ann[-1]
            print(f"       {r['period_end']} 營收 {(r['revenue'] or 0)/1e9:,.1f}B "
                  f"毛利率 {(r['gross_margin'] or 0)*100:.1f}% "
                  f"營益率 {(r['operating_margin'] or 0)*100:.1f}% "
                  f"ROE {(r['roe'] or 0)*100:.1f}% "
                  f"FCF {(r['fcf'] or 0)/1e9:,.1f}B")
    con.close()
    print(f"\n{len(tickers)} 檔 → {total:,} 期 → {VALUATION_DB}")


if __name__ == "__main__":
    main()
