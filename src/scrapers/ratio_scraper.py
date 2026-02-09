from src.utils import fetch_url, parse_html
from src.config import STOCK_LIST
from src import DATA_DIR
from src.db_service import save_metrics
import re, json, time, random
from datetime import datetime, date
import yfinance as yf

class Ratio_Scraper_Fixed():
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
                          ' Chrome/91.0.4472.124 Safari/537.36'
        }
        self.current_date = datetime.now().strftime('%Y-%m-%d')
        self.output_dir = DATA_DIR / "ratio"

    def parse_value(self, value_str):
        """Parse a value string from the website into a numeric value"""
        if not value_str or value_str == 'n/a' or value_str == '-':
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

    def extract_ticker_metrics(self, ticker, industry=None):
        """
        Extract financial metrics for a given ticker symbol by parsing HTML tables.
        """
        url = f"https://stockanalysis.com/stocks/{ticker}/statistics/"
        response = fetch_url(url, self.headers)

        if not response:
            print(f"Failed to fetch data for {ticker}")
            return None

        soup = parse_html(response.text)
        metrics = {"industry": industry}

        # Find all table rows
        rows = soup.find_all('tr')

        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 2:
                continue

            # Get the label (first cell)
            label_cell = cells[0]
            label = label_cell.get_text(strip=True)

            # Get the value (last cell, usually has title attribute with full value)
            value_cell = cells[-1]
            value = value_cell.get('title') or value_cell.get_text(strip=True)

            # Parse the value
            parsed_value = self.parse_value(value)

            # Map common labels to metric keys (based on your original ratio_names)
            label_mapping = {
                'Market Cap': 'marketcap',
                'Enterprise Value': 'enterpriseValue',
                'Earnings Date': 'earningsdate',
                'Ex-Dividend Date': 'exdivdate',
                'Current Share Class': 'sharesOutClass',
                'Shares Outstanding': 'sharesout',
                'Shares Change (YoY)': 'sharesgrowthyoy',
                'Shares Change (QoQ)': 'sharesgrowthqoq',
                'Shares Held by Insiders': 'sharesInsiders',
                'Shares Held by Institutions': 'sharesInstitutions',
                'Float': 'float',
                'PE Ratio': 'pe',
                'Forward PE': 'peForward',
                'PS Ratio': 'ps',
                'Forward PS': 'psForward',
                'PB Ratio': 'pb',
                'Price to Tangible Book': 'ptbvRatio',
                'Price to Free Cash Flow': 'pfcf',
                'Price to Operating Cash Flow': 'pocf',
                'PEG Ratio': 'pegRatio',
                'EV / Earnings': 'evEarnings',
                'EV / Sales': 'evSales',
                'EV / EBITDA': 'evEbitda',
                'EV / EBIT': 'evEbit',
                'EV / FCF': 'evFcf',
                'Current Ratio': 'currentRatio',
                'Quick Ratio': 'quickRatio',
                'Debt / Equity': 'debtEquity',
                'Debt / EBITDA': 'debtEbitda',
                'Debt / FCF': 'debtFcf',
                'Interest Coverage': 'interestCoverage',
                'ROE': 'roe',
                'ROA': 'roa',
                'ROIC': 'roic',
                'ROCE': 'roce',
                'Revenue per Employee': 'revPerEmployee',
                'Profit per Employee': 'profitPerEmployee',
                'Employees': 'employees',
                'Asset Turnover': 'assetturnover',
                'Inventory Turnover': 'inventoryturnover',
                'Tax Rate': 'taxrate',
                'Beta': 'beta',
                '52-Week Change': 'ch1y',
                '50-Day MA': 'sma50',
                '200-Day MA': 'sma200',
                'RSI': 'rsi',
                'Average Volume': 'averageVolume',
                'Short Interest': 'shortInterest',
                'Short Interest (Prior Month)': 'shortPriorMonth',
                'Short % of Shares Out': 'shortShares',
                'Short % of Float': 'shortFloat',
                'Short Ratio': 'shortRatio',
                'Revenue': 'revenue',
                'Gross Profit': 'gp',
                'Operating Income': 'opinc',
                'Pretax Income': 'pretax',
                'Net Income': 'netinc',
                'EBITDA': 'ebitda',
                'EBIT': 'ebit',
                'EPS (Diluted)': 'eps',
                'Total Cash': 'totalcash',
                'Total Debt': 'debt',
                'Net Cash / Debt': 'netcash',
                'Book Value per Share': 'bvps',
                'Working Capital': 'workingcapital',
                'Operating Cash Flow': 'ncfo',
                'Capital Expenditures': 'capex',
                'Free Cash Flow': 'fcf',
                'FCF per Share': 'fcfps',
                'Gross Margin': 'grossMargin',
                'Operating Margin': 'operatingMargin',
                'Pretax Margin': 'pretaxMargin',
                'Profit Margin': 'profitMargin',
                'EBITDA Margin': 'ebitdaMargin',
                'EBIT Margin': 'ebitMargin',
                'FCF Margin': 'fcfMargin',
                'Dividend per Share': 'dps',
                'Dividend Yield': 'dividendYield',
                'Dividend Growth': 'dividendGrowth',
                'Years of Dividend Growth': 'dividendGrowthYears',
                'Payout Ratio': 'payoutRatio',
                'Buyback Yield': 'buybackYield',
                'Total Shareholder Return': 'totalReturn',
                'Earnings Yield': 'earningsYield',
                'FCF Yield': 'fcfYield',
                'Price Target': 'priceTarget',
                'Analyst Ratings': 'analystRatings',
                'Number of Analysts': 'analystCount',
                'Revenue Growth Forecast (5Y)': 'revenue5y',
                'EPS Growth Forecast (5Y)': 'eps5y',
            }

            # Store the value if we have a mapping for this label
            metric_key = label_mapping.get(label)
            if metric_key:
                metrics[metric_key] = parsed_value

        # Also get current stock price from yfinance
        try:
            ticker_obj = yf.Ticker(ticker)
            stock_info = ticker_obj.info
            metrics['currentPrice'] = stock_info.get('regularMarketPrice') or stock_info.get('currentPrice')
        except Exception as e:
            print(f"Warning: Could not get current price for {ticker}: {e}")
            metrics['currentPrice'] = None

        print(f"Extracted {len(metrics)} metrics for {ticker}")
        return metrics

    def get_company_metrics(self):
        """Get financial metrics for all companies in STOCK_LIST"""
        all_companies_metrics = {}
        for industry, companies in STOCK_LIST.items():
            for company in companies:
                print(f"Fetching data for {company}...")
                metrics = self.extract_ticker_metrics(company, industry)
                if metrics:
                    all_companies_metrics[company] = metrics
                # Wait to avoid rate limiting
                time.sleep(random.uniform(10, 30))
        return all_companies_metrics

    def run(self):
        """Run the scraper and save results to JSON and database."""
        all_metrics = self.get_company_metrics()
        output_path = self.output_dir / f'stock_list_metrics_{self.current_date}.json'
        with open(output_path, 'w') as f:
            json.dump(all_metrics, f, indent=2)
        print(f"Saved ratio data to {output_path}")
        
        # Save to database
        snapshot_date = date.fromisoformat(self.current_date)
        print(f"Saving metrics to database for {snapshot_date}...")
        for ticker, metrics in all_metrics.items():
            industry = metrics.get("industry", "Unknown")
            save_metrics(ticker, industry, metrics, snapshot_date)
        print(f"Database save complete for {len(all_metrics)} stocks")
        
        return all_metrics


if __name__ == "__main__":
    scraper = Ratio_Scraper_Fixed()

    # Test with a single stock first
    print("Testing with GOOG...")
    test_result = scraper.extract_ticker_metrics("GOOG", "Tech")
    print(f"\nTest result for GOOG:")
    for key, value in list(test_result.items())[:10]:  # Show first 10 items
        print(f"  {key}: {value}")

    # Uncomment below to run for all stocks
    scraper.run()
