#!/usr/bin/env python3
"""替 App 裡所有股票補上 CIK，讓 SEC 那套管線能跑全庫而不只是 registry 的 49 檔。

CIK 是所有 SEC 資料的鑰匙（三大表、分部門、Non-GAAP 全部靠它定位申報）。
SEC 有官方的 ticker→CIK 對照檔，一次請求拿全部，不需要逐檔查詢。

寫進 stock_cik（估值庫），不動 earnings_registry.companies——那張表是財報筆記
管線的個股情報表，有 IR 模板、fiscal 規則這些人工維護的欄位，不該被批次覆寫。

用法:
  resolve_ciks.py          解析 stocks 表裡所有 ticker
  resolve_ciks.py --show   列出解不到的
"""
import argparse
import json
import pathlib
import sqlite3
import urllib.request
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALUATION_DB = ROOT / "backend" / "stock_valuation.db"
REGISTRY_DB = ROOT / "data" / "earnings_registry.db"
UA = "arthur-stock-radar research yankesswang@gmail.com"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_cik (
  ticker TEXT PRIMARY KEY, cik INTEGER, title TEXT, source TEXT, resolved_at TEXT
);
"""


def sec_map() -> dict[str, tuple[int, str]]:
    """SEC 官方的 ticker → CIK 對照。一個檔案涵蓋所有申報人。"""
    req = urllib.request.Request(
        "https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": UA})
    data = json.loads(urllib.request.urlopen(req, timeout=60).read())
    out = {}
    for row in data.values():
        t = (row.get("ticker") or "").upper()
        if t:
            out[t] = (int(row["cik_str"]), row.get("title", ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    mapping = sec_map()
    print(f"SEC 對照表: {len(mapping):,} 個代號")

    con = sqlite3.connect(VALUATION_DB)
    con.executescript(SCHEMA)
    tickers = [r[0].upper() for r in con.execute("SELECT DISTINCT ticker FROM stocks")]

    # registry 已經人工確認過的 CIK 優先，那些經過財報筆記管線驗證
    reg = dict(sqlite3.connect(f"file:{REGISTRY_DB}?mode=ro", uri=True).execute(
        "SELECT ticker, cik FROM companies WHERE cik IS NOT NULL"))

    now = datetime.now().isoformat(timespec="seconds")
    rows, missing = [], []
    for t in tickers:
        if t in reg:
            rows.append((t, reg[t], "", "registry", now))
        elif t in mapping:
            cik, title = mapping[t]
            rows.append((t, cik, title, "sec", now))
        else:
            missing.append(t)

    con.executemany(
        "INSERT OR REPLACE INTO stock_cik (ticker,cik,title,source,resolved_at) "
        "VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()

    print(f"解析成功 {len(rows)} / {len(tickers)}（registry {sum(1 for r in rows if r[3]=='registry')}、"
          f"SEC {sum(1 for r in rows if r[3]=='sec')}）")
    print(f"解不到 {len(missing)} 檔" + (f": {missing}" if a.show or len(missing) <= 20 else ""))


if __name__ == "__main__":
    main()
