#!/usr/bin/env python3
"""
Compute DCF valuations for stocks using pre-scraped data.

Output: data/dcf/{TICKER}_dcf_{YYYY-MM-DD}.json

Usage:
    python scripts/compute_dcf.py --ticker AAPL       # single stock
    python scripts/compute_dcf.py --all                # all stocks
    python scripts/compute_dcf.py --all --force        # overwrite existing
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import STOCK_LIST
from src.analysis.dcf_calculator import DCFCalculator


def get_all_tickers():
    tickers = set()
    for companies in STOCK_LIST.values():
        tickers.update(companies)
    return sorted(tickers)


def main():
    parser = argparse.ArgumentParser(description="Compute DCF valuations")
    parser.add_argument("--ticker", type=str, help="Single ticker to compute")
    parser.add_argument("--all", action="store_true", help="Compute for all stocks")
    parser.add_argument("--force", action="store_true", help="Overwrite existing results")
    args = parser.parse_args()

    if not args.ticker and not args.all:
        parser.print_help()
        sys.exit(1)

    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers = get_all_tickers()

    print("=" * 60)
    print("  現金流折現估值計算")
    print("=" * 60)
    print(f"  標的數：{len(tickers)}")
    print(f"  強制覆寫：{args.force}")
    print()

    calc = DCFCalculator()
    success = 0
    skipped = []
    flagged = []

    fmt_hdr = "  {:6s} {:>9s} {:>9s} {:>9s} {:>9s} {:>8s} {:>5s}  {}"
    print(fmt_hdr.format("標的", "現價", "悲觀", "加權", "樂觀", "漲跌", "可信", "備註"))
    print("  " + "-" * 78)

    for t in tickers:
        result = calc.compute_and_save(t, force=args.force)
        if not result:
            # Not a failure: an ETF, a bank, or a ticker without scraped inputs
            # simply has nothing for a free-cash-flow model to work on.
            skipped.append(t)
            continue

        sc = result["scenarios"]
        fv = result["fair_value"]
        conf = result["confidence"]
        app = result["applicability"]
        cp = result.get("current_price")

        note = "" if app["meaningful"] else "⚠ " + app["reasons"][0]
        if not app["meaningful"]:
            flagged.append(t)

        print(
            f"  {t:6s} "
            f"{('$%.2f' % cp) if cp else 'N/A':>9s} "
            f"{'$%.2f' % sc['pessimistic']['intrinsic_value']:>9s} "
            f"{'$%.2f' % fv['weighted']:>9s} "
            f"{'$%.2f' % sc['optimistic']['intrinsic_value']:>9s} "
            f"{('%+.0f%%' % fv['upside_pct']) if fv['upside_pct'] is not None else 'N/A':>8s} "
            f"{str(conf['score']) + conf['grade']:>5s}  {note}"
        )
        success += 1

    print(f"\n{'=' * 78}")
    print(f"  完成估值：{success}/{len(tickers)}")
    if flagged:
        print(f"  不適用（{len(flagged)}）—— 不要引用這些數字：{', '.join(flagged)}")
    if skipped:
        print(f"  無估值輸入（{len(skipped)}）—— ETF、銀行、尚未爬取：{', '.join(skipped)}")
    print("=" * 78)


if __name__ == "__main__":
    main()
