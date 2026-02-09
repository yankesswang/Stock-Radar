#!/usr/bin/env python3
"""Regenerate forecast data using the working scraper"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.scrapers.forecast_scraper import Forecast_Scraper_Working
import json

print("="*70)
print("Regenerating Forecast Data with Working Scraper")
print("="*70)
print("\nThis will take a while due to rate limiting (10-30 sec between stocks)")
print("Estimated time: ~30-60 minutes for all stocks\n")

# Ask for confirmation
response = input("Continue? (y/n): ")
if response.lower() != 'y':
    print("Cancelled.")
    exit()

scraper = Forecast_Scraper_Working()

print("\nStarting data collection...")
all_forecasts = scraper.get_company_metrics()

output_file = str(scraper.output_dir / f'stock_list_forecasts_{scraper.current_date}.json')

with open(output_file, 'w') as f:
    json.dump(all_forecasts, f, indent=2)

print(f"\n{'='*70}")
print(f"Data saved to: {output_file}")
print(f"Total stocks processed: {len(all_forecasts)}")

# Show summary
with_next_year = sum(1 for v in all_forecasts.values() if v['annual']['next_year_eps'] is not None)
print(f"Stocks with next_year_eps: {with_next_year}/{len(all_forecasts)}")
print("="*70)
