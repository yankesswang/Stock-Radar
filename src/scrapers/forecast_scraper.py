from src.utils import fetch_url, parse_html
from src.config import STOCK_LIST
from src import DATA_DIR
from src.db_service import save_forecast
from src.scrapers.forecast_fallback import apply_forecast_fallback
import json, time, random
from datetime import datetime, date


class Forecast_Scraper_Working():
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
                          ' Chrome/91.0.4472.124 Safari/537.36'
        }
        self.current_date = datetime.now().strftime('%Y-%m-%d')
        self.output_dir = DATA_DIR / "forecast"

    def parse_value(self, value_str):
        """Parse a value string from the website into a numeric value"""
        if not value_str or value_str in ('n/a', '-', 'N/A', '') or 'Pro' in value_str or 'Upgrade' in value_str:
            return None

        value_str = value_str.replace('$', '').replace(',', '').strip()

        try:
            if '%' in value_str:
                return float(value_str.replace('%', '').replace('+', ''))

            multipliers = {'T': 1e12, 'B': 1e9, 'M': 1e6, 'K': 1e3}
            for suffix, multiplier in multipliers.items():
                if value_str.endswith(suffix):
                    return float(value_str[:-1]) * multiplier

            return float(value_str)
        except (ValueError, AttributeError):
            return None

    def _extract_price_targets(self, table):
        """Extract price target data from Table 0."""
        body = table.find('tbody')
        if not body:
            return None

        result = {}
        for row in body.find_all('tr'):
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) < 5:
                continue
            label = cells[0].lower()
            if label == 'price':
                result['low'] = self.parse_value(cells[1])
                result['average'] = self.parse_value(cells[2])
                result['median'] = self.parse_value(cells[3])
                result['high'] = self.parse_value(cells[4])
            elif label == 'change':
                result['low_change_pct'] = self.parse_value(cells[1])
                result['average_change_pct'] = self.parse_value(cells[2])
                result['median_change_pct'] = self.parse_value(cells[3])
                result['high_change_pct'] = self.parse_value(cells[4])

        return result if result else None

    def _extract_analyst_ratings(self, table):
        """Extract analyst ratings history from Table 1."""
        header_row = table.find('thead')
        if not header_row:
            return None

        headers = [th.get_text(strip=True) for th in header_row.find_all('th')]
        periods = headers[1:]  # Skip "Rating" column

        body = table.find('tbody')
        if not body:
            return None

        result = {'periods': periods}
        rating_map = {
            'Strong Buy': 'strong_buy',
            'Buy': 'buy',
            'Hold': 'hold',
            'Sell': 'sell',
            'Strong Sell': 'strong_sell',
        }

        for row in body.find_all('tr'):
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) < 2:
                continue
            label = cells[0]
            key = rating_map.get(label)
            if key:
                result[key] = [int(c) if c.isdigit() else 0 for c in cells[1:]]

        return result

    def _extract_analyst_recommendations(self, table):
        """Extract individual analyst recommendations from Table 2."""
        body = table.find('tbody')
        if not body:
            return None

        recommendations = []
        for row in body.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) < 8:
                continue

            # Cells: [analyst+firm combined, firm, rating_display, rating, action, price_target, upside, date]
            rec = {
                'firm': cells[1].get_text(strip=True),
                'rating': cells[3].get_text(strip=True),
                'action': cells[4].get_text(strip=True),
                'price_target': cells[5].get_text(strip=True),
                'upside': cells[6].get_text(strip=True),
                'date': cells[7].get_text(strip=True),
            }

            # Extract analyst name from the first cell (contains analyst name + firm)
            first_cell = cells[0].get_text(strip=True)
            firm = rec['firm']
            if firm and firm in first_cell:
                rec['analyst'] = first_cell.replace(firm, '').strip()
            else:
                rec['analyst'] = first_cell

            recommendations.append(rec)

        return recommendations

    def _extract_annual_forecast(self, table):
        """Extract full annual forecast timeline from Table 3."""
        header_row = table.find('thead')
        if not header_row:
            return None

        headers = [th.get_text(strip=True) for th in header_row.find_all('th')]
        # Headers have two rows merged: ["Fiscal Year", "FY 2021", ..., "Period Ending", "Sep 25, 2021", ...]
        # Split at "Period Ending"
        fy_headers = []
        period_endings = []
        split_idx = None
        for i, h in enumerate(headers):
            if h == 'Period Ending':
                split_idx = i
                break
        if split_idx:
            fy_headers = headers[1:split_idx]
            period_endings = headers[split_idx + 1:]
        else:
            fy_headers = headers[1:]

        body = table.find('tbody')
        if not body:
            return None

        result = {
            'periods': fy_headers,
            'period_endings': period_endings,
        }

        row_map = {
            'Revenue': 'revenue',
            'Revenue Growth': 'revenue_growth',
            'EPS': 'eps',
            'EPS Growth': 'eps_growth',
        }

        for row in body.find_all('tr'):
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if not cells:
                continue
            label = cells[0]
            key = row_map.get(label)
            if key:
                result[key] = [self.parse_value(c) for c in cells[1:]]

        return result

    def _extract_range_table(self, table):
        """Extract High/Avg/Low range data from Tables 4-7."""
        header_row = table.find('thead')
        if not header_row:
            return None

        headers = [th.get_text(strip=True) for th in header_row.find_all('th')]
        table_type = headers[0]  # "Revenue", "Revenue Growth", "EPS", "EPS Growth"
        years = headers[1:]

        body = table.find('tbody')
        if not body:
            return None

        result = {'years': years}

        for row in body.find_all('tr'):
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) < 2:
                continue
            label = cells[0].lower()
            if label in ('high', 'avg', 'average', 'low'):
                key = 'avg' if label == 'average' else label
                result[key] = [self.parse_value(c) for c in cells[1:]]

        return table_type, result

    def extract_forecast_data(self, ticker, current_year=2025, fallback=True):
        """
        Extract all forecast data for a given ticker from the forecast page.

        Returns a comprehensive dict with:
        - price_target: Low/Avg/Median/High consensus targets
        - analyst_ratings: Historical Strong Buy/Buy/Hold/Sell counts
        - analyst_recommendations: Individual analyst recs
        - annual_forecast: Full FY timeline (Revenue, EPS, Growth)
        - forecast_ranges: High/Avg/Low estimates per year
        - annual: Legacy format (current/next year EPS/Revenue for DB)
        """
        url = f"https://stockanalysis.com/stocks/{ticker.lower()}/forecast/"
        response = fetch_url(url, self.headers)

        if not response:
            print(f"Failed to fetch forecast data for {ticker}")
            return None

        soup = parse_html(response.text)
        tables = soup.find_all('table')

        forecast_data = {
            'ticker': ticker.upper(),
            'date': self.current_date,
            'price_target': None,
            'analyst_ratings': None,
            'analyst_recommendations': None,
            'annual_forecast': None,
            'forecast_ranges': {},
            # Legacy annual format for DB/valuation compatibility
            'annual': {
                'current_eps': None,
                'current_growth': None,
                'next_year_eps': None,
                'next_year_growth': None,
                'current_revenue': None,
                'current_revenue_growth': None,
                'next_year_revenue': None,
                'next_year_revenue_growth': None,
            },
        }

        for table in tables:
            header_row = table.find('thead')
            if not header_row:
                continue

            headers = [th.get_text(strip=True) for th in header_row.find_all('th')]
            if len(headers) < 2:
                continue

            first_header = headers[0]

            # Table 0: Price targets
            if first_header == 'Target':
                forecast_data['price_target'] = self._extract_price_targets(table)

            # Table 1: Analyst ratings history
            elif first_header == 'Rating':
                forecast_data['analyst_ratings'] = self._extract_analyst_ratings(table)

            # Table 2: Individual analyst recommendations
            elif first_header == 'Analyst':
                forecast_data['analyst_recommendations'] = self._extract_analyst_recommendations(table)

            # Table 3: Full annual forecast timeline
            elif first_header == 'Fiscal Year':
                forecast_data['annual_forecast'] = self._extract_annual_forecast(table)

            # Tables 4-7: Range tables (Revenue, Revenue Growth, EPS, EPS Growth)
            elif first_header in ('Revenue', 'Revenue Growth', 'EPS', 'EPS Growth'):
                result = self._extract_range_table(table)
                if result:
                    table_type, range_data = result
                    key_map = {
                        'Revenue': 'revenue',
                        'Revenue Growth': 'revenue_growth',
                        'EPS': 'eps',
                        'EPS Growth': 'eps_growth',
                    }
                    range_key = key_map.get(table_type, table_type.lower().replace(' ', '_'))
                    forecast_data['forecast_ranges'][range_key] = range_data

        # Fill Pro-locked years before deriving the legacy annual dict, so
        # next_year_eps / next_year_revenue pick up the fallback values too.
        if fallback:
            apply_forecast_fallback(forecast_data)

        # Populate legacy annual format from range tables (Avg row)
        self._populate_legacy_annual(forecast_data, current_year)

        print(f"Extracted forecast data for {ticker}")
        return forecast_data

    def _populate_legacy_annual(self, forecast_data, current_year):
        """Fill the legacy 'annual' dict from range tables or annual forecast."""
        annual = forecast_data['annual']
        ranges = forecast_data.get('forecast_ranges', {})

        # Try range tables first (these have the clean Avg values)
        for range_key, annual_key_current, annual_key_next in [
            ('eps', 'current_eps', 'next_year_eps'),
            ('eps_growth', 'current_growth', 'next_year_growth'),
            ('revenue', 'current_revenue', 'next_year_revenue'),
            ('revenue_growth', 'current_revenue_growth', 'next_year_revenue_growth'),
        ]:
            rng = ranges.get(range_key, {})
            avg_vals = rng.get('avg', [])
            if len(avg_vals) >= 1:
                annual[annual_key_current] = avg_vals[0]
            if len(avg_vals) >= 2:
                annual[annual_key_next] = avg_vals[1]

        # Fallback to annual_forecast table if range tables are empty
        af = forecast_data.get('annual_forecast')
        if af:
            current_year_str = str(current_year + 1)  # FY 2026 for current_year=2025
            next_year_str = str(current_year + 2)      # FY 2027
            periods = af.get('periods', [])

            current_idx = None
            next_idx = None
            for idx, p in enumerate(periods):
                if current_year_str in p:
                    current_idx = idx
                if next_year_str in p:
                    next_idx = idx

            for af_key, annual_key_c, annual_key_n in [
                ('eps', 'current_eps', 'next_year_eps'),
                ('eps_growth', 'current_growth', 'next_year_growth'),
                ('revenue', 'current_revenue', 'next_year_revenue'),
                ('revenue_growth', 'current_revenue_growth', 'next_year_revenue_growth'),
            ]:
                vals = af.get(af_key, [])
                if annual[annual_key_c] is None and current_idx is not None and current_idx < len(vals):
                    annual[annual_key_c] = vals[current_idx]
                if annual[annual_key_n] is None and next_idx is not None and next_idx < len(vals):
                    annual[annual_key_n] = vals[next_idx]

    def save_ticker_json(self, ticker, forecast_data):
        """Save per-ticker comprehensive forecast JSON."""
        ticker_dir = self.output_dir / "detail"
        ticker_dir.mkdir(parents=True, exist_ok=True)
        path = ticker_dir / f"{ticker.upper()}_forecast_{self.current_date}.json"
        with open(path, 'w') as f:
            json.dump(forecast_data, f, indent=2)
        return path

    def get_company_metrics(self, current_year=2025):
        """Get forecast metrics for all companies in STOCK_LIST"""
        all_companies_forecasts = {}
        for _industry, companies in STOCK_LIST.items():
            for company in companies:
                print(f"Fetching forecast data for {company}...")
                forecast = self.extract_forecast_data(company, current_year)
                if forecast:
                    all_companies_forecasts[company] = forecast
                    self.save_ticker_json(company, forecast)
                time.sleep(random.uniform(10, 30))
        return all_companies_forecasts

    def run(self, current_year=2025):
        """Run the scraper and save results to JSON and database."""
        all_forecasts = self.get_company_metrics(current_year)

        # Save combined file (legacy format for bulk load)
        output_path = self.output_dir / f'stock_list_forecasts_{self.current_date}.json'
        with open(output_path, 'w') as f:
            json.dump(all_forecasts, f, indent=2)
        print(f"Saved forecast data to {output_path}")

        # Save to database
        snapshot_date = date.fromisoformat(self.current_date)
        print(f"Saving forecasts to database for {snapshot_date}...")
        for ticker, forecast in all_forecasts.items():
            save_forecast(ticker, forecast, snapshot_date)
        print(f"Database save complete for {len(all_forecasts)} forecasts")

        return all_forecasts


if __name__ == "__main__":
    scraper = Forecast_Scraper_Working()

    # Test with a single stock
    print("Testing with AAPL...")
    test_result = scraper.extract_forecast_data("AAPL")
    print(f"\nTest result for AAPL:")
    print(json.dumps(test_result, indent=2))

    # Save the test result
    if test_result:
        path = scraper.save_ticker_json("AAPL", test_result)
        print(f"\nSaved to {path}")
