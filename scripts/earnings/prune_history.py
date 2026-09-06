#!/usr/bin/env python3
"""把 SEC 相關資料表裁到近 N 年，控制資料庫大小。

全庫 259 檔 × 20 年會讓 financial_facts 逼近百萬列、DB 接近 1GB。十年以前的
季度數字對現在的判斷幾乎沒有作用（公司結構、會計準則、經營團隊都換過），
而且每次查詢都要掃過去。

保留規則刻意不是「一刀切日期」：
  * 年度資料（12 個月／會計年度）留到 N 年，那是看長期趨勢的骨架
  * 季度與半年資料只留一半年限，季度序列超過五年就很少人往回看
  * 三大表的科目結構（financial_line_items）完全不動，那是每檔一份、不佔空間

用法:
  prune_history.py --dry-run     只算會刪多少，不動資料
  prune_history.py               實際執行並 VACUUM
"""
import argparse
import pathlib
import sqlite3
from datetime import date

VALUATION_DB = pathlib.Path(__file__).resolve().parents[2] / "backend" / "stock_valuation.db"


def cut(years: int) -> str:
    t = date.today()
    return t.replace(year=t.year - years).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10, help="年度資料保留幾年")
    ap.add_argument("--quarter-years", type=int, default=5, help="季度資料保留幾年")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    annual_cut, quarter_cut = cut(a.years), cut(a.quarter_years)
    con = sqlite3.connect(VALUATION_DB)
    before = VALUATION_DB.stat().st_size

    # (表, 期間欄, 月數欄)。月數欄是 None 代表該表沒有期間長度的概念
    plans = [
        ("financial_facts", "period_end", "period_months"),
        ("financial_derived", "period_end", "period_months"),
        ("segment_financials", "period_end", "period_months"),
        ("nongaap_reconciliation", "period_end", "period_months"),
    ]
    for table, pcol, mcol in plans:
        try:
            total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            print(f"{table:<24} （不存在，跳過）")
            continue
        # 年度／時點值用寬的年限，季度與半年用窄的
        where = (f"({mcol} >= 12 OR {mcol} = 0 OR {mcol} IS NULL) AND {pcol} < ?",
                 f"({mcol} > 0 AND {mcol} < 12) AND {pcol} < ?")
        n = 0
        for clause, bound in zip(where, (annual_cut, quarter_cut)):
            sql_count = f"SELECT COUNT(*) FROM {table} WHERE {clause}"
            n += con.execute(sql_count, (bound,)).fetchone()[0]
            if not a.dry_run:
                con.execute(f"DELETE FROM {table} WHERE {clause}", (bound,))
        print(f"{table:<24} {total:>9,} 列 → 刪 {n:>9,} ({n/total*100 if total else 0:>4.1f}%)")

    if not a.dry_run:
        con.commit()
        con.execute("VACUUM")          # 不 VACUUM 的話刪掉的空間不會還給檔案系統
    con.close()
    after = VALUATION_DB.stat().st_size
    print(f"\nDB {before/1e6:.0f} MB → {after/1e6:.0f} MB"
          + ("（dry-run，未變動）" if a.dry_run else ""))


if __name__ == "__main__":
    main()
