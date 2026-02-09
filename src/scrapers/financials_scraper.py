from src.utils import fetch_url, parse_html
from src import DATA_DIR
import pandas as pd
from datetime import datetime
import re

class FinancialsScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
                          ' Chrome/91.0.4472.124 Safari/537.36'
        }
        self.current_date = datetime.now().strftime('%Y-%m-%d')
        self.output_dir = DATA_DIR / "financials"

    def parse_value(self, value_str):
        """Parse a value string into a numeric value or keep as string"""
        if not value_str or value_str == 'n/a' or value_str == '-' or value_str == '—':
            return None

        # Check if it's a paywall message
        if 'Upgrade to Stock Analysis Pro' in value_str or 'PRO' in value_str:
            return None

        # Remove currency symbols and commas
        value_str = value_str.replace('$', '').replace(',', '').strip()

        try:
            # Handle percentage
            if '%' in value_str:
                return float(value_str.replace('%', ''))

            # Handle billions/millions/trillions
            multipliers = {'T': 1e12, 'B': 1e9, 'M': 1e6, 'K': 1e3}
            for suffix, multiplier in multipliers.items():
                if value_str.endswith(suffix):
                    return float(value_str[:-1]) * multiplier

            # Try direct conversion
            return float(value_str)
        except (ValueError, AttributeError):
            return value_str  # Return as string if can't convert

    def is_quarterly_date(self, date_str):
        """Determine if a date string represents quarterly data"""
        if not date_str or not isinstance(date_str, str):
            return None

        date_str = date_str.strip()

        # Annual data patterns
        annual_patterns = ['FY', 'Fiscal', 'Year']
        if any(pattern in date_str for pattern in annual_patterns):
            return False

        # If it's just a year (4 digits), it's annual
        if re.match(r'^\d{4}$', date_str):
            return False

        # If it's a year range like "2016 - 2020", it's annual
        if re.match(r'^\d{4}\s*-\s*\d{4}$', date_str):
            return False

        # Quarterly data typically has month names (Mar, Jun, Sep, Dec)
        quarterly_patterns = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Q1', 'Q2', 'Q3', 'Q4']

        if any(pattern in date_str for pattern in quarterly_patterns):
            return True

        return True  # Default to quarterly if uncertain

    def parse_table_to_dataframe(self, table, category=None):
        """
        Parse a single HTML table to pandas DataFrames
        Returns tuple of (quarterly_df, annual_df) since tables can have both
        """
        rows = table.find_all('tr')

        if len(rows) < 2:
            return None, None

        # Get header row (period dates)
        header_cells = rows[0].find_all(['th', 'td'])
        if len(header_cells) < 2:
            return None, None

        # Extract periods (skip first cell which is typically "Period Ending" or "Metric")
        periods = [cell.get_text(strip=True) for cell in header_cells[1:]]

        # Separate quarterly and annual periods
        quarterly_periods = [p for p in periods if self.is_quarterly_date(p)]
        annual_periods = [p for p in periods if not self.is_quarterly_date(p)]

        # Extract metric rows
        all_data = []
        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2:
                continue

            metric_name = cells[0].get_text(strip=True)

            # Skip empty or header rows
            if not metric_name or metric_name in ['Metric', 'Period Ending', '']:
                continue

            row_data = {'Metric': metric_name}

            # Parse values for each period
            for i, cell in enumerate(cells[1:]):
                if i < len(periods):
                    value_text = cell.get('title') or cell.get_text(strip=True)
                    parsed_value = self.parse_value(value_text)
                    row_data[periods[i]] = parsed_value

            all_data.append(row_data)

        # Create DataFrames for quarterly and annual data
        quarterly_df = None
        annual_df = None

        if len(all_data) > 0:
            if quarterly_periods:
                q_data = []
                for row in all_data:
                    q_row = {'Metric': row['Metric'], 'Category': category}
                    for period in quarterly_periods:
                        q_row[period] = row.get(period)
                    q_data.append(q_row)

                quarterly_df = pd.DataFrame(q_data)
                # Reorder columns
                cols = ['Category', 'Metric'] + quarterly_periods
                quarterly_df = quarterly_df[cols]

            if annual_periods:
                a_data = []
                for row in all_data:
                    a_row = {'Metric': row['Metric'], 'Category': category}
                    for period in annual_periods:
                        a_row[period] = row.get(period)
                    a_data.append(a_row)

                annual_df = pd.DataFrame(a_data)
                # Reorder columns
                cols = ['Category', 'Metric'] + annual_periods
                annual_df = annual_df[cols]

        return quarterly_df, annual_df

    def scrape_page(self, url, category_prefix=None):
        """Helper method to scrape a single page and return dataframes"""
        response = fetch_url(url, self.headers)

        if not response:
            print(f"Failed to fetch data from {url}")
            return [], []

        soup = parse_html(response.text)

        quarterly_dfs = []
        annual_dfs = []

        # Find all tables on the page
        tables = soup.find_all('table')

        for table in tables:
            # Get table header/title if available
            category = None
            prev_element = table.find_previous_sibling(['h2', 'h3'])
            if prev_element:
                category = prev_element.get_text(strip=True)
                if category_prefix:
                    category = f"{category_prefix} - {category}"

            q_df, a_df = self.parse_table_to_dataframe(table, category)

            if q_df is not None:
                quarterly_dfs.append(q_df)
            if a_df is not None:
                annual_dfs.append(a_df)

        return quarterly_dfs, annual_dfs

    def scrape_financials(self, ticker):
        """
        Scrape all financial data for a given ticker from stockanalysis.com
        Fetches from 4 pages: Income Statement, Balance Sheet, Cash Flow, Ratios
        Returns two DataFrames: quarterly and annual data
        """
        all_quarterly_dfs = []
        all_annual_dfs = []

        # Define the financial pages to scrape
        pages = {
            'Income Statement': f"https://stockanalysis.com/stocks/{ticker}/financials/",
            'Balance Sheet': f"https://stockanalysis.com/stocks/{ticker}/financials/balance-sheet/",
            'Cash Flow': f"https://stockanalysis.com/stocks/{ticker}/financials/cash-flow-statement/",
            'Ratios': f"https://stockanalysis.com/stocks/{ticker}/financials/ratios/"
        }

        for page_name, url in pages.items():
            print(f"Scraping {page_name} for {ticker}...")
            q_dfs, a_dfs = self.scrape_page(url, page_name)
            all_quarterly_dfs.extend(q_dfs)
            all_annual_dfs.extend(a_dfs)

        # Combine all quarterly and annual dataframes
        quarterly_df = pd.concat(all_quarterly_dfs, ignore_index=True) if all_quarterly_dfs else None
        annual_df = pd.concat(all_annual_dfs, ignore_index=True) if all_annual_dfs else None

        if quarterly_df is not None:
            print(f"\nExtracted {len(quarterly_df)} quarterly financial metrics for {ticker}")
        if annual_df is not None:
            print(f"Extracted {len(annual_df)} annual financial metrics for {ticker}")

        return quarterly_df, annual_df

    def save_financials(self, ticker, quarterly_df, annual_df):
        """Save financial DataFrames to CSV files"""
        saved_files = []

        if quarterly_df is not None:
            quarterly_path = self.output_dir / f'{ticker}_financials_quarterly_{self.current_date}.csv'
            quarterly_df.to_csv(quarterly_path, index=False)
            print(f"Saved quarterly financial data to {quarterly_path}")
            saved_files.append(str(quarterly_path))

        if annual_df is not None:
            annual_path = self.output_dir / f'{ticker}_financials_annual_{self.current_date}.csv'
            annual_df.to_csv(annual_path, index=False)
            print(f"Saved annual financial data to {annual_path}")
            saved_files.append(str(annual_path))

        return saved_files


if __name__ == "__main__":
    scraper = FinancialsScraper()

    # Scrape QCOM financials
    print("Scraping all financial data for QCOM...")
    quarterly_df, annual_df = scraper.scrape_financials("qcom")

    if quarterly_df is not None:
        print(f"\nQuarterly financial data shape: {quarterly_df.shape}")
        print(f"\nCategories in quarterly data:")
        print(quarterly_df['Category'].value_counts())
        print(f"\nFirst few rows of quarterly data:")
        print(quarterly_df.head())

    if annual_df is not None:
        print(f"\nAnnual financial data shape: {annual_df.shape}")
        print(f"\nCategories in annual data:")
        print(annual_df['Category'].value_counts())
        print(f"\nFirst few rows of annual data:")
        print(annual_df.head())

    # Save to CSV files
    scraper.save_financials("qcom", quarterly_df, annual_df)
