#!/usr/bin/env python3
"""拿 stockanalysis 的 TTM 快照對帳自算的 SEC 指標，抓自己的錯。

這條路徑的價值已經驗證過：人工比對一檔就抓到有息負債把「零件」與「總成」重複
相加（AAPL 189B vs 實際 98.7B），連帶污染淨現金、負債權益比、ROIC。

對帳的兩個前提：
  1. stockanalysis 是 TTM（最近 12 個月），所以自算這邊也要湊 TTM——
     流量科目取最近四季相加，存量科目取最新一期的時點值。拿年度數字比 TTM
     會在財報季之間差一整季，那是假警報。
  2. 差異不等於我錯。口徑本來就會不同（ROE 用期末 vs 期間平均、
     FCF 扣不扣租賃）。所以只標示、不自動修正，由人判斷。

用法:
  reconcile_metrics.py                  全部，只印超過門檻的
  reconcile_metrics.py --all            全部都印
  reconcile_metrics.py --tolerance 0.05 自訂門檻（預設 3%）
"""
import argparse
import pathlib
import sqlite3
from datetime import date, datetime, timedelta

VALUATION_DB = pathlib.Path(__file__).resolve().parents[2] / "backend" / "stock_valuation.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS metric_reconciliation (
  ticker TEXT, metric TEXT, ours REAL, theirs REAL, diff_pct REAL,
  ours_basis TEXT, verdict TEXT, checked_at TEXT,
  PRIMARY KEY (ticker, metric)
);
"""

# 自算欄位 → stockanalysis 的 (section, stat_id)。
# flow=True 的要湊 TTM（最近四季相加），False 是存量或比率（取最新一期）。
PAIRS = [
    ("revenue",          "incomeStatement", "revenue",         True),
    ("gross_profit",     "incomeStatement", "grossProfit",     True),
    ("operating_income", "incomeStatement", "operatingIncome", True),
    ("net_income",       "incomeStatement", "netIncome",       True),
    ("ncfo",             "cashFlow",        "ncfo",            True),
    ("fcf",              "cashFlow",        "fcf",             True),
    ("equity",           "balanceSheet",    "equity",          False),
    ("debt",             "balanceSheet",    "debt",            False),
    ("net_cash",         "balanceSheet",    "netcash",         False),
    ("bvps",             "balanceSheet",    "bvps",            False),
    ("gross_margin",     "margins",         "grossMargin",     False),
    ("operating_margin", "margins",         "operatingMargin", False),
    ("profit_margin",    "margins",         "profitMargin",    False),
    ("fcf_margin",       "margins",         "fcfMargin",       False),
    ("roe",              "financialEfficiency", "roe",         False),
    ("roa",              "financialEfficiency", "roa",         False),
    ("roic",             "financialEfficiency", "roic",        False),
    ("current_ratio",    "financialPosition",   "currentRatio", False),
    ("debt_equity",      "financialPosition",   "debtEquity",   False),
    ("asset_turnover",   "financialEfficiency", "assetturnover", False),
]

# 口徑本來就不同、對不上是正常的欄位。標成 definition 而不是 check，
# 免得真正的錯被埋在幾百條雜訊裡：
#   cash/net_cash  對方的 Cash & Marketable Securities 含短期投資，我只算現金
#   roe/roa/roic   對方用期間平均餘額，我用期末餘額
DEFINITIONAL = {"cash", "net_cash", "roe", "roa", "roic", "bvps"}

# 比率類的欄位在自算表裡是存量比存量，直接取最新一期即可；
# 但如果它的分子是流量（利潤率、ROE、週轉率），最新一「季」的值不能跟 TTM 比，
# 要用 TTM 分子重算。這裡列出需要重算的，其餘直接取值。
RATIO_FROM_TTM = {
    "gross_margin": ("gross_profit", "revenue"),
    "operating_margin": ("operating_income", "revenue"),
    "profit_margin": ("net_income", "revenue"),
    "fcf_margin": ("fcf", "revenue"),
    "roe": ("net_income", "equity"),
    "roa": ("net_income", "total_assets"),
    "roic": ("operating_income", None),          # 分母另外算：equity + debt
    "asset_turnover": ("revenue", "total_assets"),
}


def ttm(con, ticker: str) -> tuple[dict, str]:
    """湊出可以跟對方 TTM 比較的一組數字。回傳 (值, 基準說明)。

    兩條路，取期間較新的那條：
      A) 最新的 12 個月期間（會計年度或公司自報的滾動 12 個月，兩者都可用）
      B) 最近四季相加

    B 有個陷阱：多數公司不單獨揭露第四季（10-K 只給全年），所以「最近四筆季報」
    很可能是 Q3、Q2、Q1、去年Q3——跳過 Q4 又重複算一季，TTM 直接失真。
    所以要檢查四季確實連續（頭尾相距 11~13 個月），不連續就不用這條。
    """
    flows = ["revenue", "gross_profit", "operating_income", "net_income", "ncfo", "capex", "fcf"]
    stocks = ["equity", "debt", "net_cash", "total_assets", "cash", "bvps",
              "current_ratio", "debt_equity"]

    year = con.execute(
        "SELECT * FROM financial_derived WHERE ticker=? AND period_months=12 "
        "ORDER BY period_end DESC LIMIT 1", (ticker,)).fetchone()
    quarters = con.execute(
        "SELECT * FROM financial_derived WHERE ticker=? AND period_months=3 "
        "ORDER BY period_end DESC LIMIT 4", (ticker,)).fetchall()

    quarters_ok = False
    if len(quarters) == 4:
        span = (date.fromisoformat(quarters[0]["period_end"])
                - date.fromisoformat(quarters[3]["period_end"])).days
        quarters_ok = 300 <= span <= 400        # 頭尾差約一年才算連續四季

    use_year = year is not None and (
        not quarters_ok or year["period_end"] >= quarters[0]["period_end"])

    if use_year:
        src, basis = year, f"{year['period_end']} 起算的 12 個月"
        out = {f: src[f] for f in flows}
        latest = src
    elif quarters_ok:
        out = {}
        for f in flows:
            vals = [r[f] for r in quarters if r[f] is not None]
            out[f] = sum(vals) if len(vals) == 4 else None   # 缺一季就不給半套 TTM
        latest = quarters[0]
        basis = f"{quarters[3]['period_end']}~{quarters[0]['period_end']} 四季相加"
    else:
        return {}, ""

    for f in stocks:
        out[f] = latest[f]
    # 分子是流量的比率要用 TTM 重算，不能抄單期的值
    for key, (num, den) in RATIO_FROM_TTM.items():
        n = out.get(num)
        d = (out.get(den) if den else
             ((latest["equity"] or 0) + (latest["debt"] or 0)) or None)
        out[key] = (n / d) if (n is not None and d) else None
    out["_as_of"] = latest["period_end"]
    return out, basis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tolerance", type=float, default=0.03)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(VALUATION_DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    now = datetime.now().isoformat(timespec="seconds")

    tickers = [r[0] for r in con.execute(
        "SELECT DISTINCT ticker FROM sa_statistics ORDER BY ticker")]
    flagged = checked = 0
    rows_out = []
    for tk in tickers:
        sa = {(r["section"], r["stat_id"]): r["value_num"]
              for r in con.execute("SELECT * FROM sa_statistics WHERE ticker=?", (tk,))}
        ours, basis = ttm(con, tk)
        if not ours:
            continue
        # 對方是「最近 12 個月」，我的最新一季可能落後一整季（新財報還沒進 DB）。
        # 拿不同日期的資產負債表互比會產生大量假警報，VIAV 的可轉債轉換就是這樣。
        latest_q = ours.get("_as_of") or ""
        # 這站沒有直接給「財報截止日」，用最近財報日回推：季報大約在季末後 30~45 天
        # 公布，所以「財報日 − 45 天」還晚於我最新一季的季末，就代表對方多一季。
        stale = False
        row = con.execute(
            "SELECT value_text FROM sa_statistics WHERE ticker=? AND stat_id='earningsdate'",
            (tk,)).fetchone()
        if row and row[0] and latest_q:
            try:
                ed = datetime.strptime(row[0].replace(",", ""), "%b %d %Y").date()
                stale = (ed - timedelta(days=45)).isoformat() > latest_q
            except ValueError:
                pass
        bad = []
        for metric, section, stat, _flow in PAIRS:
            o, t = ours.get(metric), sa.get((section, stat))
            if o is None or t is None or t == 0:
                continue
            checked += 1
            diff = abs(o - t) / abs(t)
            if diff <= a.tolerance:
                verdict = "ok"
            elif metric in DEFINITIONAL:
                verdict = "definition"        # 口徑差異，不是錯
            elif stale:
                verdict = "stale"             # 我的資產負債表比對方舊，不能直接比
            else:
                verdict = "check"
            if verdict == "check":
                flagged += 1
                bad.append((metric, o, t, diff))
            rows_out.append((tk, metric, o, t, diff, basis, verdict, now))
        if bad or a.all:
            print(f"\n{tk}  (自算 TTM {basis})")
            for metric, o, t, diff in bad:
                fmt = (lambda v: f"{v*100:>8.1f}%") if abs(o) < 100 and abs(t) < 100 \
                    else (lambda v: f"{v/1e9:>8,.1f}B")
                print(f"    ⚠ {metric:<18} 自算 {fmt(o)}  vs  對方 {fmt(t)}   差 {diff*100:>6.1f}%")

    con.executemany(
        "INSERT OR REPLACE INTO metric_reconciliation "
        "(ticker,metric,ours,theirs,diff_pct,ours_basis,verdict,checked_at) "
        "VALUES (?,?,?,?,?,?,?,?)", rows_out)
    con.commit()
    con.close()
    counts = {}
    for r in rows_out:
        counts[r[6]] = counts.get(r[6], 0) + 1
    print(f"\n比對 {checked} 組：" + "、".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"真正需要看的（check）：{flagged} 組")


if __name__ == "__main__":
    main()
