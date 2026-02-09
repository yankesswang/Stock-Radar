#!/usr/bin/env python3
"""
PE Valuation Analysis Tool - Main Entry Point

Usage:
    python main.py              # Run full valuation analysis
    python main.py --scrape     # Run all scrapers first, then analyze
    python main.py --test NVDA  # Test with a single ticker
"""

import argparse
from datetime import date

from src.config import STOCK_LIST
from src.analysis.valuation_analyzer import Valuation_Analyzer_Pure


def run_analysis(current_year=2025):
    """Run valuation analysis on all stocks using pre-collected data."""
    print("=" * 70)
    print("PE Valuation Analyzer - Using Pre-collected Data")
    print("=" * 70)

    analyzer = Valuation_Analyzer_Pure(current_year)
    df_dict = analyzer.aggregate_company_data(STOCK_LIST)
    analyzer.save_to_excel(df_dict)


def run_scrape_and_analyze(current_year=2025):
    """Run all scrapers first, then perform analysis."""
    from src.runner import run_all

    print("=" * 70)
    print("Step 1: Running all scrapers in parallel...")
    print("=" * 70)
    run_all()

    print("\n")
    print("=" * 70)
    print("Step 2: Running valuation analysis...")
    print("=" * 70)
    run_analysis(current_year)


def test_single(ticker, current_year=2025):
    """Test valuation analysis for a single stock."""
    print(f"Testing valuation for {ticker}...\n")

    analyzer = Valuation_Analyzer_Pure(current_year)
    result = analyzer.process_company(ticker)

    if result:
        print(f"\nValuation results for {ticker}:")
        for key, value in result.items():
            print(f"  {key}: {value}")
    else:
        print(f"Failed to process {ticker}. Check that data files exist.")


def main():
    parser = argparse.ArgumentParser(description="PE Valuation Analysis Tool")
    parser.add_argument("--scrape", action="store_true", help="Run scrapers before analysis")
    parser.add_argument("--test", type=str, metavar="TICKER", help="Test with a single ticker")
    parser.add_argument("--year", type=int, default=2025, help="Current fiscal year (default: 2025)")

    args = parser.parse_args()

    if args.test:
        test_single(args.test, args.year)
    elif args.scrape:
        run_scrape_and_analyze(args.year)
    else:
        run_analysis(args.year)


if __name__ == "__main__":
    main()
