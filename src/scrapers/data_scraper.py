import pandas as pd
import re, json, json5
import glob
import os
import yfinance as yf

from src.utils import fetch_url, parse_html, extract_percentage
from src import DATA_DIR
import ast

class StockAnalysisScraper:
    def __init__(self, ticker, current_year=2025):
        self.headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
                      ' Chrome/91.0.4472.124 Safari/537.36'
        }
        self.ticker = ticker
        self.current_year = current_year

        # Find latest ratio file
        ratio_pattern = str(DATA_DIR / "ratio" / "stock_list_metrics_*.json")
        ratio_files = glob.glob(ratio_pattern)
        latest_ratio = max(ratio_files, key=os.path.getmtime) if ratio_files else None

        # Find latest forecast file
        forecast_pattern = str(DATA_DIR / "forecast" / "stock_list_forecasts_*.json")
        forecast_files = glob.glob(forecast_pattern)
        latest_forecast = max(forecast_files, key=os.path.getmtime) if forecast_files else None

        if latest_ratio:
            print(f"Using ratio data: {os.path.basename(latest_ratio)}")
            with open(latest_ratio, "r") as f:
                self.ratio_data = json.load(f)
        else:
            print("Warning: No ratio data file found")
            self.ratio_data = {}

        if latest_forecast:
            print(f"Using forecast data: {os.path.basename(latest_forecast)}")
            with open(latest_forecast, "r") as f:
                self.forecast_data = json.load(f)
        else:
            print("Warning: No forecast data file found")
            self.forecast_data = {}

    def safe_round(self, value, decimals=2):
        """Safely round a value, returning None if value is None"""
        if value is None:
            return None
        try:
            return round(float(value), decimals)
        except (TypeError, ValueError):
            return None

    def get_eps_forecast(self):
        """
        Retrieves EPS forecasts for a given company from StockAnalysis.

        Returns:
            dict or None: Dictionary containing EPS for current and next year, or None if failed.
        """
        current_year_suffix = str(self.current_year)[-2:]
        next_year_suffix = str(self.current_year + 1)[-2:]

        # Get data safely
        if self.ticker not in self.forecast_data:
            print(f"Warning: {self.ticker} not found in forecast data")
            return {
                f"eps_{current_year_suffix}": None,
                f"eps_{next_year_suffix}": None,
                "past_eps_growth": None
            }

        annual_data = self.forecast_data[self.ticker].get("annual", {})

        eps_current_year = self.safe_round(annual_data.get("current_eps", None), 2)
        eps_next_year = self.safe_round(annual_data.get("next_year_eps", None), 2)

        # Use annual growth rate instead of quarterly (quarterly data is not available)
        eps_growth = self.safe_round(annual_data.get("current_growth", None), 2)

        return {
            f"eps_{current_year_suffix}": eps_current_year,
            f"eps_{next_year_suffix}": eps_next_year,
            "past_eps_growth": eps_growth  # Now uses annual growth from forecast
        }

    def get_growth_forecasts(self):
        """
        Retrieves 5-year growth forecasts from StockAnalysis.com.

        Returns:
            dict or None: Dictionary containing revenue and EPS growth forecasts
        """
        url = f"https://stockanalysis.com/stocks/{self.ticker}/statistics/"

        try:
            response = fetch_url(url, self.headers)
            if not response:
                return None

            soup = parse_html(response.text)

            # Find growth forecast values
            forecasts = {
                'revenue_growth_5y': None,
                'eps_growth_5y': None
            }

            # Find elements containing the forecasts
            for row in soup.find_all('tr'):
                cells = row.find_all('td')
                if not cells:
                    continue

                label_cell = cells[0].get_text(strip=True)
                if 'Revenue Growth Forecast' in label_cell and '5Y' in label_cell:
                    forecasts['revenue_growth_5y'] = extract_percentage(cells[-1].get_text(strip=True))
                elif 'EPS Growth Forecast' in label_cell and '5Y' in label_cell:
                    forecasts['eps_growth_5y'] = extract_percentage(cells[-1].get_text(strip=True))

            if forecasts['revenue_growth_5y'] is None and forecasts['eps_growth_5y'] is None:
                print(f"No growth forecasts found for {self.ticker}")

            return forecasts

        except Exception as e:
            print(f"Error extracting growth forecasts: {e}")
            return None

    def fetch_stock_data(self, ticker, endpoint="forecast"):
        """Fetch data from StockAnalysis for the given ticker and endpoint."""
        url = f"https://stockanalysis.com/stocks/{ticker}/{endpoint}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = fetch_url(url, headers)
        return (parse_html(response.text)) if response else None

    def get_price(self, ticker):
        soup_content = str(self.fetch_stock_data(ticker))

        try:
            if 'ex:"NASDAQ"' in soup_content:
                split_after_ex = soup_content.split('ex:"NASDAQ"')[1]
                if 'pd:' in split_after_ex:
                    stock_price= split_after_ex.split('pd:')[1].split(',td')[0].strip('"')
                    print(f"Extracted stock price: {stock_price}")  # Debugging output
                    return float(stock_price)
                else:
                    return 0.0
            else:
                return 0.0
        except Exception as e:
            return f"Error parsing data: {str(e)}"

    def get_market_cap_and_price(self):
        """
        Retrieves the market capitalization and current price for a given stock ticker.

        Returns:
            dict: Dictionary containing market cap, price, and beta, or None if failed.
        """
        try:
            # Get market cap from ratio data
            if self.ticker not in self.ratio_data:
                print(f"Warning: {self.ticker} not found in ratio data")
                return None

            ticker_data = self.ratio_data[self.ticker]

            # Get market cap (handle None values)
            marketcap_raw = ticker_data.get("marketcap")
            if marketcap_raw is not None:
                market_cap = round(marketcap_raw / 1000000000.0, 2)
            else:
                print(f"Warning: No market cap data for {self.ticker}")
                market_cap = 0

            # Get stock price from yfinance
            try:
                stock_price = yf.Ticker(self.ticker).info.get("regularMarketPrice")
                if stock_price is None:
                    # Try currentPrice as backup
                    stock_price = yf.Ticker(self.ticker).info.get("currentPrice", 0)
            except Exception as e:
                print(f"Warning: Could not get price from yfinance for {self.ticker}: {e}")
                stock_price = 0

            # Get beta value
            beta_value = ticker_data.get("beta")

            return {
                'ticker': self.ticker,
                '市值': str(market_cap) + "B",
                '股價': stock_price,
                'Beta': beta_value
            }

        except Exception as e:
            print(f"Error retrieving data for {self.ticker}: {e}")
            return None


if __name__ == '__main__':
    scraper = StockAnalysisScraper('AMZN', 2025)

    print(scraper.get_growth_forecasts())
    print(scraper.get_market_cap_and_price())
