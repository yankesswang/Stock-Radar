#!/usr/bin/env python3
"""
Scrape the /metrics/ page for every stock in STOCK_LIST.

Each ticker gets a CSV file at:
    data/metrics/{TICKER}_metrics_{YYYY-MM-DD}.csv

Rate-limited: 10-30 sec random delay between requests.
Estimated time: ~45-90 min for 181 stocks.

Usage:
    python scripts/scrape_metrics.py              # all stocks
    python scripts/scrape_metrics.py AAPL MSFT    # specific tickers only
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.scrapers.metrics_scraper import MetricsScraper
from src.config import STOCK_LIST


def get_all_tickers():
    tickers = set()
    for companies in STOCK_LIST.values():
        tickers.update(companies)
    return sorted(tickers)


def main():
    # Allow passing specific tickers as CLI args
    if len(sys.argv) > 1:
        tickers = [t.upper() for t in sys.argv[1:]]
    else:
        tickers = get_all_tickers()

    print("=" * 60)
    print("  Scrape Metrics (stockanalysis.com/stocks/*/metrics/)")
    print("=" * 60)
    print(f"  Tickers: {len(tickers)}")
    print(f"  Rate limit: 10-30s between requests")
    print()

    if len(tickers) > 5:
        response = input(f"Scrape {len(tickers)} stocks? (y/n): ")
        if response.lower() != 'y':
            print("Cancelled.")
            return

    scraper = MetricsScraper()
    results = scraper.run(tickers=tickers)

    print(f"\n{'=' * 60}")
    print(f"  Saved {len(results)}/{len(tickers)} tickers to data/metrics/")
    failed = set(tickers) - set(results.keys())
    if failed:
        print(f"  Failed: {', '.join(sorted(failed))}")
    print("=" * 60)


if __name__ == "__main__":
    main()
