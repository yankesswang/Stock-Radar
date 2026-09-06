#!/usr/bin/env python3
"""
一次補齊 stockanalysis 的 statistics。

平常這份跟著每週的 refresh 走（backend/app/routers/scheduler.py 的 _scrape_one
第 4 步）。這支腳本是給「一次補完」用的：statistics 整批停在 2026-02，
等每週那輪要等一個禮拜。

挑最舊的先跑，所以中斷之後直接再跑一次就會接著補，不必記進度。
走頁面的 JSON 端點（15KB 而不是 123KB 的 HTML），所以間隔 3–6 秒就夠。

    uv run python scripts/backfill_stockanalysis.py            # 全部
    uv run python scripts/backfill_stockanalysis.py --limit 50 # 只補 50 檔
"""
import argparse
import os
import random
import sys
import time
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.database import SessionLocal          # noqa: E402
from backend.app.models import Statistic, Stock        # noqa: E402
from sqlalchemy import func                            # noqa: E402

MIN_DELAY, MAX_DELAY = 3, 6


def targets():
    """今天還沒抓過的 ticker，最舊的排前面。"""
    db = SessionLocal()
    try:
        latest = (
            db.query(Statistic.stock_id, func.max(Statistic.snapshot_date).label("d"))
            .group_by(Statistic.stock_id).subquery()
        )
        rows = (
            db.query(Stock.ticker, latest.c.d)
            .outerjoin(latest, latest.c.stock_id == Stock.id)
            .all()
        )
    finally:
        db.close()

    today = date.today()
    pending = [(d or date.min, t) for t, d in rows if d != today]
    pending.sort(key=lambda x: x[0])
    return [t for _, t in pending]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = 全部")
    args = ap.parse_args()

    from src.scrapers.statistics_scraper import StatisticsScraper

    todo = targets()
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("沒有要補的。")
        return

    print(f"要補 {len(todo)} 檔，預估 "
          f"{len(todo) * MIN_DELAY / 60:.0f}–{len(todo) * MAX_DELAY / 60:.0f} 分鐘")

    scraper = StatisticsScraper()
    ok = fail = 0
    for i, ticker in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {ticker}", flush=True)
        try:
            d = scraper.scrape_statistics(ticker)
            if d:
                scraper.save(ticker, d)
                ok += 1
            else:
                fail += 1
        except Exception as exc:
            print(f"  \u2717 {ticker}: {exc}")
            fail += 1
        if i < len(todo):
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    print(f"\n完成：{ok} 檔、失敗 {fail}", flush=True)


if __name__ == "__main__":
    main()
