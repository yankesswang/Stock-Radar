#!/usr/bin/env python3
"""每日把最新一筆估值快照的現價換成真正的收盤價，並重算高估／低估。

為什麼需要這支：`valuations.current_price` 由每週一次的比率表爬蟲寫入，抄的是
stockanalysis 的 `Last Close Price`。爬蟲排在台北凌晨、也就是美股盤中，所以那個
數字最好的情況也落後一個交易日，最壞會停留整整一週以上（實測 AAPL 的 9/19–9/21
三筆快照都還掛著 9/3 的收盤，落後 12 個交易日）。

`valuation_status` 與 `valuation_diff_pct` 是拿這個價跟 `pe_median_price` 比出來的，
所以價一舊，高估／低估的判定與幅度跟著錯 —— 而讀 DB 的那一票（同業比較、戰情室、
每日摘要、財報筆記）都沒有即時價可以蓋過去。

這支只動「最新一筆」快照的價格與由價格衍生的四個欄位，不碰 EPS、PE 中位數、
合理價這些爬蟲的產物，也不回頭改歷史快照。

用法：
    python3 scripts/refresh_prices.py               # 全部
    python3 scripts/refresh_prices.py --ticker AAPL # 單檔
    python3 scripts/refresh_prices.py --dry-run     # 只印不寫
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func  # noqa: E402

# 走 backend.app.* 而不是把 backend/ 塞進 sys.path 再 import app.*：後者在
# 排程從伺服器行程內呼叫這支時，會把同一批 model 再註冊成第二份 registry。
from backend.app.database import SessionLocal  # noqa: E402
from backend.app.models import Stock, Valuation  # noqa: E402

from src.quotes import get_quotes  # noqa: E402

# 報價與存檔差超過這個比例才寫 —— 省掉幾百筆沒有意義的 UPDATE
MIN_DELTA_PCT = 0.01


def _status(price: float | None, fair: float | None) -> tuple[str, float | None]:
    """價格對合理價的高估／低估判定，跟 src.db_service 的算法逐字一致。"""
    if not price or not fair:
        return "N/A", None
    return (
        "overvalued" if price > fair else "undervalued",
        round((fair - price) / fair * 100, 1),
    )


def refresh(tickers: list[str] | None = None, dry_run: bool = False) -> dict:
    db = SessionLocal()
    try:
        # 每檔各自的最新快照日期（不同檔可能不同天，不能用全庫 max）
        latest = (
            db.query(Valuation.stock_id, func.max(Valuation.snapshot_date).label("d"))
            .group_by(Valuation.stock_id)
            .subquery()
        )
        q = (
            db.query(Valuation, Stock.ticker)
            .join(Stock, Stock.id == Valuation.stock_id)
            .join(latest, (latest.c.stock_id == Valuation.stock_id)
                        & (latest.c.d == Valuation.snapshot_date))
        )
        if tickers:
            up = [t.upper() for t in tickers]
            q = q.filter(func.upper(Stock.ticker).in_(up))
        rows = q.all()

        by_ticker = {t.upper(): v for v, t in rows}
        quotes = get_quotes(list(by_ticker), use_cache=False)

        updated, unchanged, missing = 0, 0, []
        for ticker, val in by_ticker.items():
            quote = quotes.get(ticker)
            if not quote or not quote.get("price"):
                missing.append(ticker)
                continue
            price = round(float(quote["price"]), 2)
            old = val.current_price
            if old and abs(price - old) / old * 100 < MIN_DELTA_PCT:
                unchanged += 1
                continue

            status, diff = _status(price, val.pe_median_price)
            ny_status, ny_diff = _status(price, val.next_year_pe_median_price)
            print(
                f"  {ticker:8s} {old if old else 0:>9.2f} → {price:>9.2f} "
                f"({(price - old) / old * 100:+6.2f}%)  {val.valuation_status} → {status}"
                if old else
                f"  {ticker:8s} {'—':>9s} → {price:>9.2f}  {val.valuation_status} → {status}"
            )
            if not dry_run:
                val.current_price = price
                val.valuation_status = status
                val.valuation_diff_pct = diff
                val.next_year_valuation_status = ny_status
                val.next_year_valuation_diff_pct = ny_diff
            updated += 1

        if not dry_run:
            db.commit()
        return {"updated": updated, "unchanged": unchanged, "missing": missing,
                "total": len(by_ticker)}
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", action="append", help="只更新這些檔（可重複）")
    ap.add_argument("--dry-run", action="store_true", help="只印出會改什麼，不寫入")
    args = ap.parse_args()

    print(f"{'=' * 78}\n  更新估值快照現價{'（dry-run）' if args.dry_run else ''}\n{'=' * 78}")
    r = refresh(args.ticker, args.dry_run)
    print(f"\n{'=' * 78}")
    print(f"  共 {r['total']} 檔：更新 {r['updated']}、無變動 {r['unchanged']}、"
          f"抓不到報價 {len(r['missing'])}")
    if r["missing"]:
        head = ", ".join(r["missing"][:20])
        print(f"  抓不到（保留原值，不歸零）：{head}"
              f"{' …' if len(r['missing']) > 20 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
