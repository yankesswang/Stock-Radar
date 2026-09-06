#!/usr/bin/env python3
"""抓 stockanalysis 的個股統計，補 SEC 給不了的欄位，並拿來跟自算的數字對帳。

為什麼走 __data.json 而不是爬 HTML：這站是 SvelteKit，頁面自己吃的就是這個端點。
同樣 106 個項目，JSON 15KB / 0.3s，HTML 123KB / 0.5s；而且 JSON 的鍵是穩定 id
（grossMargin、zScore），HTML 只有會被改名的英文標題。robots.txt 只擋 /e/ 與 /p/，
這條路徑沒有被禁。但它沒有相容性承諾，所以解析失敗要能自己察覺（見 --check）。

存的是 TTM 單一快照，不是時間序列——這站沒有歷史。歷史一律以 SEC 為準，
這裡只負責兩件事：
  1. 補 SEC 沒有的欄位（員工數、WACC、EBITDA、Z-Score、分析師預估、股利品質）
  2. 跟 financial_derived 的自算值對帳，抓自己的錯

用法:
  fetch_stockanalysis.py                抓全部（registry 裡的公司）
  fetch_stockanalysis.py --ticker AAPL  單檔
  fetch_stockanalysis.py --show         印出抓到的項目
"""
import argparse
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
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sa_statistics (
  ticker TEXT, section TEXT, stat_id TEXT, title TEXT,
  value_text TEXT, value_num REAL, fetched_at TEXT,
  PRIMARY KEY (ticker, section, stat_id)
);
"""


def fetch(url: str, tries: int = 3, timeout: int = 30) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def resolve(arr: list, idx, depth: int = 0):
    """SvelteKit __data.json 的索引指標壓縮：dict 的值是指回同一個陣列的下標。

    -1 代表 null。深度上限是保險絲，這種結構理論上不會循環，但外部格式不歸我管。
    """
    if depth > 14 or idx is None or idx == -1:
        return None
    node = arr[idx] if isinstance(idx, int) and 0 <= idx < len(arr) else idx
    if isinstance(node, dict):
        return {k: resolve(arr, v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(arr, v, depth + 1) for v in node]
    return node


_NUM = re.compile(r"^-?[\d,]+(\.\d+)?$")
_MULT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def to_num(item: dict):
    """優先用 hover（未縮寫的完整值），退回 value（會有 B/M/% 縮寫）。

    hover 是這站給滑鼠提示用的原始精度：value 顯示 48.65%，hover 是 48.653%。
    """
    for raw in (item.get("hover"), item.get("value")):
        if not isinstance(raw, str):
            continue
        s = raw.strip().replace("$", "").replace(",", "")
        if s in ("", "n/a", "-", "—"):
            continue
        pct = s.endswith("%")
        s = s.rstrip("%")
        mult = 1.0
        if s and s[-1].upper() in _MULT:
            mult = _MULT[s[-1].upper()]
            s = s[:-1]
        try:
            v = float(s) * mult
        except ValueError:
            continue
        return v / 100 if pct else v          # 百分比一律存成小數，跟自算的欄位同單位
    return None


def parse(payload: bytes) -> dict[str, list[dict]]:
    """→ {section: [{id, title, value_text, value_num}, …]}"""
    d = json.loads(payload)
    nodes = [n for n in d.get("nodes", []) if isinstance(n, dict) and n.get("type") == "data"]
    if not nodes:
        return {}
    # 統計資料在最大的那個 node；其他是 session、頁面 meta 之類
    stats = max(nodes, key=lambda n: len(n.get("data") or []))
    root = resolve(stats["data"], 0)
    if not isinstance(root, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for section, body in root.items():
        if not isinstance(body, dict):
            continue
        rows = body.get("data")
        if not isinstance(rows, list):
            continue
        items = []
        for it in rows:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            items.append({"id": it["id"], "title": it.get("title", ""),
                          "value_text": str(it.get("value", "")), "value_num": to_num(it)})
        if items:
            out[section] = items
    return out


def targets(arg_ticker: str | None) -> list[str]:
    con = sqlite3.connect(f"file:{REGISTRY_DB}?mode=ro", uri=True)
    q = "SELECT ticker FROM companies"
    rows = [r[0] for r in con.execute(q + (" WHERE ticker=?" if arg_ticker else ""),
                                      (arg_ticker.upper(),) if arg_ticker else ())]
    con.close()
    return sorted(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--sleep", type=float, default=1.0, help="請求間隔（秒）")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tks = targets(a.ticker)
    if not tks:
        print("no targets")
        return

    con = None
    if not a.dry_run:
        con = sqlite3.connect(VALUATION_DB)
        con.executescript(SCHEMA)

    now = datetime.now().isoformat(timespec="seconds")
    ok = fail = 0
    for tk in tks:
        url = f"https://stockanalysis.com/stocks/{tk.lower()}/statistics/__data.json"
        try:
            sections = parse(fetch(url))
        except Exception as e:
            print(f"{tk:<6} ✗ {type(e).__name__}: {e}")
            fail += 1
            time.sleep(a.sleep)
            continue
        n = sum(len(v) for v in sections.values())
        if n < 20:
            # 這站沒有相容性承諾。項目數異常少＝格式可能變了，寧可報警不要靜默寫入半套
            print(f"{tk:<6} ⚠ 只解出 {n} 項（{len(sections)} 區塊）—— 格式可能變了")
            fail += 1
            time.sleep(a.sleep)
            continue
        ok += 1
        print(f"{tk:<6} {n:>3} 項 / {len(sections)} 區塊"
              + (f" | {', '.join(list(sections)[:5])}" if a.show else ""))
        if con is not None:
            con.execute("DELETE FROM sa_statistics WHERE ticker=?", (tk,))
            con.executemany(
                "INSERT OR REPLACE INTO sa_statistics "
                "(ticker,section,stat_id,title,value_text,value_num,fetched_at) "
                "VALUES (?,?,?,?,?,?,?)",
                [(tk, sec, it["id"], it["title"], it["value_text"], it["value_num"], now)
                 for sec, items in sections.items() for it in items])
            con.commit()
        time.sleep(a.sleep)

    if con is not None:
        con.close()
    print(f"\n成功 {ok} / 失敗 {fail}" + (" (dry-run)" if a.dry_run else f" → {VALUATION_DB}"))


if __name__ == "__main__":
    main()
