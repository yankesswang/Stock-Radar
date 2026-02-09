#!/usr/bin/env python3
"""Check earnings page for quarterly EPS data"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.utils import fetch_url, parse_html
import json

ticker = "AAPL"  # Use AAPL since we saw it had quarterly data in old file
url = f"https://stockanalysis.com/stocks/{ticker}/forecast/"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
                  ' Chrome/91.0.4472.124 Safari/537.36'
}

print(f"Checking forecast page for quarterly EPS data: {ticker}\n")
response = fetch_url(url, headers)

if response:
    soup = parse_html(response.text)

    # Look for the main historical forecast table (Table 4 based on earlier debug)
    tables = soup.find_all('table')

    if len(tables) >= 4:
        table = tables[3]  # Table 4 (index 3) had historical + forecast annual data

        print("="*70)
        print("HISTORICAL + FORECAST TABLE (Table 4)")
        print("="*70)

        # Get all headers
        header_row = table.find('thead')
        if header_row:
            all_headers = header_row.find_all('th')
            headers_text = [th.get_text(strip=True) for th in all_headers]
            print(f"\nTotal header columns: {len(headers_text)}")
            print(f"Headers: {headers_text[:15]}")  # Show first 15

        # Get EPS row
        body = table.find('tbody')
        if body:
            rows = body.find_all('tr')
            for row in rows:
                cells = row.find_all(['th', 'td'])
                if cells:
                    label = cells[0].get_text(strip=True)
                    if 'EPS' in label and 'Growth' not in label:
                        print(f"\n{label} row:")
                        values = []
                        for cell in cells[1:15]:  # Get first 14 values
                            val = cell.get('title') or cell.get_text(strip=True)
                            values.append(val)
                        print(f"Values: {values}")

                        # Parse into numbers
                        parsed_values = []
                        for v in values:
                            try:
                                if v and v != '-' and v != 'n/a':
                                    parsed_values.append(float(v.replace(',', '').replace('$', '')))
                                else:
                                    parsed_values.append(None)
                            except:
                                parsed_values.append(None)

                        print(f"\nParsed EPS values: {parsed_values}")
                        print(f"Number of non-null values: {sum(1 for v in parsed_values if v is not None)}")

print("\n" + "="*70)
print("\nCONCLUSION:")
print("The forecast page only has ANNUAL forecasts, not quarterly.")
print("Quarterly EPS data would need to come from actual earnings history,")
print("not forecasts. The old scraper may have been combining:")
print("  1. Historical quarterly EPS (from earnings history)")
print("  2. Future quarterly EPS estimates (may not be available)")
