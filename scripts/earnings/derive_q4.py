#!/usr/bin/env python3
"""補出第四季：10-K 只報全年，不單獨揭露 Q4。

季度序列每年會斷一次，而且是必然的——公司在 10-K 裡給的是全年數字，第四季
從來沒有被單獨標記過。實測 49 檔近五年：每檔約 17 季、5 個缺口，剛好一年一個。

唯一的補法是 Q4 = 全年 − Q1 − Q2 − Q3。這是算出來的、不是公司報的，所以：
  * 寫進 financial_facts 時標 derived=1
  * API 一律把來源帶出去，前端標「推算」
  * 三季缺任何一季就不補，寧可留缺口也不要給半套推算值

只補流量科目（損益表、現金流量表）。資產負債表是時點值，年度末的餘額本來就
有申報，不需要也不能用減法推。
"""
import argparse
import pathlib
import sqlite3
from datetime import date

VALUATION_DB = pathlib.Path(__file__).resolve().parents[2] / "backend" / "stock_valuation.db"


def ensure_column(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(financial_facts)")}
    if "derived" not in cols:
        con.execute("ALTER TABLE financial_facts ADD COLUMN derived INTEGER DEFAULT 0")
        con.commit()


def build(con: sqlite3.Connection, ticker: str) -> list[tuple]:
    rows = con.execute(
        "SELECT concept, period_end, period_months, value, unit, form, filed, fy, fp "
        "FROM financial_facts WHERE ticker=? AND period_months IN (3, 12) "
        "AND COALESCE(derived,0)=0", (ticker,)).fetchall()

    years: dict[tuple, dict] = {}      # (concept, fy_end) → 全年那筆
    quarters: dict[str, list] = {}     # concept → [(period_end, value)]
    for concept, pend, months, val, unit, form, filed, fy, fp in rows:
        if val is None:
            continue
        if months == 12 and fp == "FY":
            years[(concept, pend)] = (val, unit, form, filed, fy)
        elif months == 3:
            quarters.setdefault(concept, []).append((pend, val))

    out = []
    for (concept, fy_end), (fy_val, unit, form, filed, fy) in years.items():
        end = date.fromisoformat(fy_end)
        # 同一會計年度內、結束日早於年度末的三季（往回 365 天）
        qs = sorted(
            (p, v) for p, v in quarters.get(concept, [])
            if 0 < (end - date.fromisoformat(p)).days <= 365)
        if len(qs) != 3:
            continue                    # 不足三季就不補，半套的減法會給出錯的 Q4
        if any(p == fy_end for p, _ in quarters.get(concept, [])):
            continue                    # 這一季公司自己有報，不需要推算
        q4 = fy_val - sum(v for _, v in qs)
        out.append((ticker, concept, fy_end, 3, q4, unit, form, filed, fy, "Q4", 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(VALUATION_DB)
    ensure_column(con)
    tickers = ([a.ticker.upper()] if a.ticker else
               [r[0] for r in con.execute(
                   "SELECT DISTINCT ticker FROM financial_facts ORDER BY ticker")])

    total = 0
    for tk in tickers:
        rows = build(con, tk)
        total += len(rows)
        print(f"{tk:<6} 補出 {len(rows):>4} 筆 Q4")
        if rows and not a.dry_run:
            con.execute("DELETE FROM financial_facts WHERE ticker=? AND derived=1", (tk,))
            con.executemany(
                "INSERT OR REPLACE INTO financial_facts "
                "(ticker,concept,period_end,period_months,value,unit,form,filed,fy,fp,derived) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
    con.close()
    print(f"\n{len(tickers)} 檔 → 補出 {total:,} 筆" + (" (dry-run)" if a.dry_run else ""))


if __name__ == "__main__":
    main()
