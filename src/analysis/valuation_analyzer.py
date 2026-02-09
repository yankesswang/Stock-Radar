import time
import random
from tqdm import tqdm
import pandas as pd
from datetime import date
import json
import glob
import os

from src import DATA_DIR, OUTPUT_DIR

class Valuation_Analyzer_Pure:
    """Pure calculation valuation analyzer - NO web scraping, only uses pre-collected data"""

    def __init__(self, current_year=2025):
        self.current_year = current_year

        # Load all data files at initialization
        self.pe_data = self._load_latest_pe_data()
        self.ratio_data = self._load_latest_ratio_data()
        self.forecast_data = self._load_latest_forecast_data()

    def _load_latest_pe_data(self):
        """Load the latest PE data file"""
        pe_files = glob.glob(str(DATA_DIR / "pe" / "stock_list_PE_*.json"))
        if not pe_files:
            print("Warning: No PE data files found")
            return {}

        latest_pe_file = max(pe_files, key=os.path.getmtime)
        print(f"Loading PE data: {os.path.basename(latest_pe_file)}")

        with open(latest_pe_file, 'r') as f:
            return json.load(f)

    def _load_latest_ratio_data(self):
        """Load the latest ratio/metrics data file"""
        ratio_files = glob.glob(str(DATA_DIR / "ratio" / "stock_list_metrics_*.json"))
        if not ratio_files:
            print("Warning: No ratio data files found")
            return {}

        latest_ratio_file = max(ratio_files, key=os.path.getmtime)
        print(f"Loading ratio data: {os.path.basename(latest_ratio_file)}")

        with open(latest_ratio_file, 'r') as f:
            return json.load(f)

    def _load_latest_forecast_data(self):
        """Load the latest forecast data file"""
        forecast_files = glob.glob(str(DATA_DIR / "forecast" / "stock_list_forecasts_*.json"))
        if not forecast_files:
            print("Warning: No forecast data files found")
            return {}

        latest_forecast_file = max(forecast_files, key=os.path.getmtime)
        print(f"Loading forecast data: {os.path.basename(latest_forecast_file)}")

        with open(latest_forecast_file, 'r') as f:
            return json.load(f)

    def safe_get(self, data, key, default=None):
        """Safely get a value from dictionary, returning default if None or missing"""
        value = data.get(key, default)
        return value if value is not None else default

    def calculate_valuations(self, stock_data, pe_median):
        """
        Calculates various valuations and differences based on stock data.

        Args:
            stock_data (dict): Contains EPS, price, growth data
            pe_median (float): The five-year median PE ratio

        Returns:
            dict: Calculated valuations and differences
        """
        valuations = {}

        current_year_suffix = str(self.current_year)[-2:]
        next_year_suffix = str(self.current_year + 1)[-2:]

        # Get growth rate (prefer eps_growth_5y, fallback to past_eps_growth)
        growth = self.safe_get(stock_data, 'eps_growth_5y') or self.safe_get(stock_data, 'past_eps_growth', 0) or 0

        # Calculate estimated PE based on growth
        valuations['預估PE'] = growth

        if 0 < growth < 5:
            valuations['預估PE'] *= 0.8
        elif 5 <= growth < 10:
            valuations['預估PE'] *= 1.0
        elif 10 <= growth < 15:
            valuations['預估PE'] *= 1.1
        elif 15 <= growth < 20:
            valuations['預估PE'] *= 1.2
        elif 20 <= growth < 30:
            valuations['預估PE'] *= 1.5
        else:
            valuations['預估PE'] *= 2.0

        valuations['預估PE'] = round(valuations['預估PE'], 2) if valuations['預估PE'] else 0.0

        # Get EPS values
        eps_current = self.safe_get(stock_data, f'eps_{current_year_suffix}', 0.0) or 0.0
        eps_next = self.safe_get(stock_data, f'eps_{next_year_suffix}', 0.0) or 0.0

        # Current year calculations
        valuations[f"{current_year_suffix}年EPS"] = eps_current
        valuations[f"{current_year_suffix}年合理價"] = round(eps_current * valuations['預估PE']) if eps_current else 0

        # Next year calculations
        valuations[f"{next_year_suffix}年EPS"] = eps_next
        valuations[f"{next_year_suffix}年合理價"] = round(eps_next * valuations['預估PE']) if eps_next else 0

        # Five-year PE Median calculations
        valuations["五年PE MEDIAN"] = pe_median or 0
        valuations["五年PE中位價"] = round((pe_median or 0) * eps_current) if eps_current else 0

        # Current price
        current_price = self.safe_get(stock_data, '股價', 0) or 0

        # Valuation comparison for current year
        if current_price and valuations["五年PE中位價"]:
            valuations[f"{current_year_suffix}年估值"] = "高估" if current_price > valuations["五年PE中位價"] else "低估"

            if valuations["五年PE中位價"] != 0:
                diff_pct = (valuations['五年PE中位價'] - current_price) / valuations['五年PE中位價'] * 100
                valuations[f"{current_year_suffix}年相差百分比"] = f"{round(diff_pct)}%"
            else:
                valuations[f"{current_year_suffix}年相差百分比"] = "N/A"
        else:
            valuations[f"{current_year_suffix}年估值"] = "N/A"
            valuations[f"{current_year_suffix}年相差百分比"] = "N/A"

        # Next year PE median valuation
        valuations[f"{next_year_suffix}年PE中位價"] = round((pe_median or 0) * eps_next) if eps_next else 0

        # Valuation comparison for next year
        if current_price and valuations[f"{next_year_suffix}年PE中位價"]:
            valuations[f"{next_year_suffix}年估值"] = "高估" if current_price > valuations[f"{next_year_suffix}年PE中位價"] else "低估"

            if valuations[f"{next_year_suffix}年PE中位價"] != 0:
                diff_pct = (valuations[f"{next_year_suffix}年PE中位價"] - current_price) / valuations[f"{next_year_suffix}年PE中位價"] * 100
                valuations[f"{next_year_suffix}年相差百分比"] = f"{round(diff_pct)}%"
            else:
                valuations[f"{next_year_suffix}年相差百分比"] = "N/A"
        else:
            valuations[f"{next_year_suffix}年估值"] = "N/A"
            valuations[f"{next_year_suffix}年相差百分比"] = "N/A"

        return valuations

    def process_company(self, ticker):
        """
        Process data for a single company using only pre-collected data.

        Args:
            ticker (str): The stock ticker symbol

        Returns:
            dict or None: Dictionary containing all valuation data, or None if failed
        """
        current_year_suffix = str(self.current_year)[-2:]
        next_year_suffix = str(self.current_year + 1)[-2:]

        # Check if ticker exists in all required datasets
        if ticker not in self.forecast_data:
            print(f"Warning: {ticker} not found in forecast data")
            return None

        if ticker not in self.ratio_data:
            print(f"Warning: {ticker} not found in ratio data")
            return None

        if ticker not in self.pe_data:
            print(f"Warning: {ticker} not found in PE data")
            return None

        # Get data from pre-loaded datasets
        forecast = self.forecast_data[ticker]['annual']
        ratio = self.ratio_data[ticker]
        pe_median = self.pe_data[ticker]

        # Build stock_data dictionary from all sources
        stock_data = {}

        # EPS data from forecast
        stock_data[f'eps_{current_year_suffix}'] = forecast.get('current_eps')
        stock_data[f'eps_{next_year_suffix}'] = forecast.get('next_year_eps')
        stock_data['past_eps_growth'] = forecast.get('current_growth')

        # Growth forecasts (try to get from ratio data)
        stock_data['revenue_growth_5y'] = ratio.get('revenue5y')
        stock_data['eps_growth_5y'] = ratio.get('eps5y')

        # Market data from ratio
        marketcap_raw = ratio.get('marketcap')
        if marketcap_raw:
            stock_data['市值'] = f"{round(marketcap_raw / 1e9, 2)}B"
        else:
            stock_data['市值'] = "N/A"

        # Get current price from pre-collected ratio data
        stock_data['股價'] = ratio.get('currentPrice', 0) or 0
        if stock_data['股價'] == 0:
            print(f"Warning: No current price for {ticker} in ratio data. Re-run ratio scraper to collect prices.")

        # Beta from ratio data
        stock_data['Beta'] = ratio.get('beta')

        # Perform valuation calculations
        valuations = self.calculate_valuations(stock_data, pe_median)

        # Build final result
        company_data = {
            "Revenue Growth Forecast (5Y)": f"{stock_data.get('revenue_growth_5y', 0) or 0}%",
            "EPS Growth Forecast (5Y)": f"{stock_data.get('eps_growth_5y', 0) or 0}%",
            "EPS Growth Past 5 Years": f"{stock_data.get('past_eps_growth', 0) or 0}%",
            "預估PE": valuations.get("預估PE", 0),
            f"{current_year_suffix}年EPS": valuations.get(f"{current_year_suffix}年EPS", 0),
            f"{current_year_suffix}年合理價": valuations.get(f"{current_year_suffix}年合理價", 0),
            f"{next_year_suffix}年EPS": valuations.get(f"{next_year_suffix}年EPS", 0),
            f"{next_year_suffix}年合理價": valuations.get(f"{next_year_suffix}年合理價", 0),
            "五年PEMEDIAN": valuations.get("五年PE MEDIAN", 0),
            "五年PE中位價": valuations.get("五年PE中位價", 0),
            "市值": stock_data.get("市值", "N/A"),
            "股價": stock_data.get("股價", 0),
            f"{current_year_suffix}年估值": valuations.get(f"{current_year_suffix}年估值", "N/A"),
            f"{current_year_suffix}年相差百分比": valuations.get(f"{current_year_suffix}年相差百分比", "N/A"),
            f"{next_year_suffix}年PE中位價": valuations.get(f"{next_year_suffix}年PE中位價", 0),
            f"{next_year_suffix}年估值": valuations.get(f"{next_year_suffix}年估值", "N/A"),
            f"{next_year_suffix}年相差百分比": valuations.get(f"{next_year_suffix}年相差百分比", "N/A"),
        }

        return company_data

    def aggregate_company_data(self, stock_list):
        """
        Process all companies and aggregate data by industry.

        Args:
            stock_list (dict): Dictionary with industry as key and set of tickers as values

        Returns:
            dict: Industry-wise DataFrames with transposed data
        """
        industry_dataframes = {}

        for industry, companies in stock_list.items():
            print(f"\n正在處理產業：{industry}，包含 {len(companies)} 家公司。")
            all_companies_data = []

            for company in tqdm(list(companies), desc=f"處理 {industry}", unit="公司"):
                company_data = self.process_company(company)

                if company_data:
                    company_data['Industry'] = industry
                    company_data['Company'] = company
                    all_companies_data.append(company_data)
                else:
                    print(f"{company} 的數據處理失敗。")

            if all_companies_data:
                # Create DataFrame
                df = pd.DataFrame(all_companies_data)
                # Move Industry & Company to the front
                df.insert(0, 'Industry', df.pop('Industry'))
                df.insert(1, 'Company', df.pop('Company'))
                # Transpose
                df_transposed = df.set_index(['Industry', 'Company']).transpose()
                industry_dataframes[industry] = df_transposed
            else:
                print(f"{industry} 沒有可用的公司數據。")

        return industry_dataframes

    def save_to_excel(self, industry_dataframes, path=None):
        """
        Save industry DataFrames to Excel file with separate worksheets.

        Args:
            industry_dataframes (dict): Industry DataFrames
            path (str): Excel file path to save. If None, uses default output dir.
        """
        if path is None:
            path = str(OUTPUT_DIR / f'stock_data_{date.today()}.xlsx')

        with pd.ExcelWriter(path, engine='xlsxwriter') as writer:
            for industry, df in industry_dataframes.items():
                df.to_excel(writer, sheet_name=industry, index=True)
        print(f"\n所有數據已保存到 {path}")


if __name__ == "__main__":
    from src.config import STOCK_LIST

    print("="*70)
    print("Pure Valuation Analyzer - Using Pre-collected Data Only")
    print("="*70)

    analyzer = Valuation_Analyzer_Pure(2025)

    # Test with one company first
    print("\nTesting with NVDA...")
    test_result = analyzer.process_company('NVDA')
    if test_result:
        print("\n✓ Test successful!")
        for key, value in list(test_result.items())[:10]:
            print(f"  {key}: {value}")

    # Uncomment to run full analysis
    print("\nProcessing all companies...")
    df_dict = analyzer.aggregate_company_data(STOCK_LIST)
    analyzer.save_to_excel(df_dict)
