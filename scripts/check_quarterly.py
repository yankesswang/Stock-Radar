#!/usr/bin/env python3
"""Check if quarterly forecast data exists and where"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.utils import fetch_url, parse_html

ticker = "NVDA"

# Check different possible pages for quarterly data
pages = [
    ("forecast", f"https://stockanalysis.com/stocks/{ticker}/forecast/"),
    ("financials", f"https://stockanalysis.com/stocks/{ticker}/financials/"),
    ("financials-quarterly", f"https://stockanalysis.com/stocks/{ticker}/financials/?p=quarterly"),
]

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
                  ' Chrome/91.0.4472.124 Safari/537.36'
}

for page_name, url in pages:
    print(f"\n{'='*70}")
    print(f"Checking: {page_name}")
    print(f"URL: {url}")
    print('='*70)

    response = fetch_url(url, headers)
    if not response:
        print("Failed to fetch")
        continue

    soup = parse_html(response.text)

    # Look for quarterly indicators
    text = str(soup)

    # Check for quarterly indicators
    if 'Q1' in text or 'Q2' in text or 'Q3' in text or 'Q4' in text:
        print("Found quarterly indicators (Q1/Q2/Q3/Q4)")

        # Find tables with quarterly data
        tables = soup.find_all('table')
        for i, table in enumerate(tables):
            header_row = table.find('thead')
            if header_row:
                headers_list = [th.get_text(strip=True) for th in header_row.find_all('th')]
                if any('Q' in h for h in headers_list):
                    print(f"\n  Table {i+1} with quarterly headers:")
                    print(f"    Headers: {headers_list[:8]}")  # Show first 8 headers

                    # Show first data row
                    body = table.find('tbody')
                    if body:
                        first_row = body.find('tr')
                        if first_row:
                            cells = first_row.find_all(['th', 'td'])
                            row_data = [cell.get_text(strip=True) for cell in cells[:8]]
                            print(f"    First row: {row_data}")
    else:
        print("No quarterly indicators found")

print("\n" + "="*70)
