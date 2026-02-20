import re
import requests
import pandas as pd
from bs4 import BeautifulSoup
from io import StringIO
import time, random
from requests.exceptions import RequestException, HTTPError
import numpy as np
from typing import Dict, List, Optional, Tuple
import json
from datetime import datetime, date

from src.utils import fetch_url, parse_html, compute_iqr_statistics, filter_outliers
from src.config import STOCK_LIST, PE_TICKER_TO_COMPANY
from src import DATA_DIR
from src.db_service import save_pe_median


def _derive_macrotrends_slug(ticker: str) -> Optional[str]:
    """
    Derive a MacroTrends URL slug for a ticker.
    Checks static mapping first; falls back to yfinance company name.
    """
    # 1. Static mapping
    slug = PE_TICKER_TO_COMPANY.get(ticker.lower())
    if slug:
        return slug

    # 2. Derive from yfinance longName
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        name = info.get("longName") or info.get("shortName", "")
        if not name:
            return None

        name = name.lower()
        # Strip common legal suffixes
        for suffix in [
            ", inc.", " inc.", ", corp.", " corp.", ", ltd.", " ltd.",
            " corporation", ", llc", " llc", ", plc", " plc",
            " co.", ", co.", " holdings", " group", ", n.v.", " n.v.",
        ]:
            if name.endswith(suffix):
                name = name[: -len(suffix)]

        # Remove special characters, collapse whitespace, replace with dashes
        name = re.sub(r"[^a-z0-9\s-]", " ", name)
        name = re.sub(r"\s+", "-", name.strip())
        name = re.sub(r"-+", "-", name)
        return name
    except Exception:
        return None

class PERatioScraper:
    def __init__(self):
        self.current_date = datetime.now().strftime('%Y-%m-%d')
        self.output_dir = DATA_DIR / "pe"

    def parse_pe_ratios(self, ticker: str) -> List[float]:
        """
        Parse PE ratios from the given HTML content.

        Args:
            ticker (str): Stock ticker symbol.

        Returns:
            List[float]: List of PE ratios extracted.
        """
        company = _derive_macrotrends_slug(ticker)
        if not company:
            print(f"No MacroTrends slug found for {ticker} — skipping PE scrape.")
            return None
        url = f"https://www.macrotrends.net/stocks/charts/{ticker.upper()}/{company}/pe-ratio"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }

        try:
            response = fetch_url(url, headers)
            if not response:
                return None

            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find("table", class_="table")

            if not table:
                print("PE ratio table not found in the HTML content.")
                return []

            rows = table.find("tbody").find_all("tr")
            pe_ratios = []

            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 4:
                    pe_ratio_str = cells[3].get_text(strip=True)
                    # Handle cases like 'N/A' or empty strings
                    try:
                        pe_ratio = float(pe_ratio_str.replace(",", ""))
                        pe_ratios.append(pe_ratio)
                    except ValueError:
                        # Skip invalid PE ratio values
                        continue
            return pe_ratios
        except Exception as e:
            print(f"Error extracting PE ratios: {e}")
            return None

    def analyze_pe_ratios(self, pe_ratios: List[float], num_latest: int = 20) -> Optional[float]:
        """
        Analyze PE ratios by computing median after outlier removal.

        Args:
            pe_ratios (List[float]): List of PE ratios.
            num_latest (int): Number of latest PE ratios to consider.

        Returns:
            Optional[float]: Median PE after outlier removal.
        """
        if not pe_ratios:
            return None, None
        # Remove zeros from the PE ratios array
        pe_ratios = [pe for pe in pe_ratios if pe != 0]
        if not pe_ratios:
            return None
        # Consider the latest `num_latest` PE ratios
        latest_pe_ratios = pe_ratios[:num_latest]
        pe_array = np.array(latest_pe_ratios)

        # Compute IQR statistics
        q1, q3, iqr = compute_iqr_statistics(pe_array)
        filtered_pe = filter_outliers(pe_array, q1, q3, iqr)

        # Compute means
        if filtered_pe.size > 0:
            median_filtered_pe = round(np.median(filtered_pe), 1)
            return median_filtered_pe
        else:
            return 0


    def get_company_metrics(self):
        """
        Get PE ratio metrics for all companies in STOCK_LIST.

        Returns:
            dict: Dictionary mapping ticker to median PE ratio.
        """
        all_companies_metrics = {}
        for industry, companies in STOCK_LIST.items():
            for company in companies:
                time.sleep(random.uniform(10, 30))
                pe_ratios = self.parse_pe_ratios(company)
                if not pe_ratios:
                    print(f"Failed to fetch PE ratios for {company}")
                    continue
                # Analyze PE ratios
                print("Analyzing PE ratios for:", company)
                print(f"PE ratios fetched for {company}: {pe_ratios}")
                pe_median = self.analyze_pe_ratios(pe_ratios)
                if pe_median is None:
                    print(f"Failed to analyze PE ratios for {company}")
                    continue

                # Store the metrics
                print(f"Fetched and analyzed PE ratios for {company}: {pe_median}")

                # Store the company metrics
                all_companies_metrics[company] = pe_median

                time.sleep(random.uniform(10, 30))
        return all_companies_metrics

    def run(self):
        """Run the scraper and save results to JSON and database."""
        pe_data = self.get_company_metrics()
        output_path = self.output_dir / f'stock_list_PE_{self.current_date}.json'
        with open(output_path, 'w') as f:
            json.dump(pe_data, f, indent=2)
        print(f"Saved PE data to {output_path}")
        
        # Save to database
        snapshot_date = date.fromisoformat(self.current_date)
        print(f"Saving PE medians to database for {snapshot_date}...")
        for ticker, pe_median in pe_data.items():
            if pe_median is not None:
                save_pe_median(ticker, pe_median, snapshot_date)
        print(f"Database save complete for {len(pe_data)} PE medians")
        
        return pe_data


if __name__ == "__main__":
    pe_scraper = PERatioScraper()
    pe_scraper.run()
