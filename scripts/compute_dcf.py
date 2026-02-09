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
    print("  DCF Valuation Calculator")
    print("=" * 60)
    print(f"  Tickers: {len(tickers)}")
    print(f"  Force overwrite: {args.force}")
    print()

    calc = DCFCalculator()
    success = 0
    failed = []

    fmt_hdr = "  {:6s}  {:>12s}  {:>12s}  {:>12s}  {:>10s}"
    print(fmt_hdr.format("TICKER", "PESSIMISTIC", "MODERATE", "OPTIMISTIC", "PRICE"))
    print("  " + "-" * 56)

    for t in tickers:
        result = calc.compute_and_save(t, force=args.force)
        if result:
            sc = result["scenarios"]
            cp = result.get("current_price")
            price_str = f"${cp:.2f}" if cp else "N/A"
            pess = f"${sc['pessimistic']['intrinsic_value']:>9.2f}"
            mod = f"${sc['moderate']['intrinsic_value']:>9.2f}"
            opt = f"${sc['optimistic']['intrinsic_value']:>9.2f}"
            print(f"  {t:6s}  {pess:>12s}  {mod:>12s}  {opt:>12s}  {price_str:>10s}")
            success += 1
        else:
            failed.append(t)

    print(f"\n{'=' * 60}")
    print(f"  Completed: {success}/{len(tickers)}")
    if failed:
        print(f"  Failed ({len(failed)}): {', '.join(failed)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
