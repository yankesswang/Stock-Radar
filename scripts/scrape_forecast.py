"""
Scrape analyst forecast data from stockanalysis.com

Usage:
    python3 scripts/scrape_forecast.py --ticker AAPL          # single stock
    python3 scripts/scrape_forecast.py --all                   # all stocks from STOCK_LIST
    python3 scripts/scrape_forecast.py --all --workers 3       # parallel (3 threads)
    python3 scripts/scrape_forecast.py --all --skip-existing   # skip already-scraped tickers
"""
import argparse
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import fetch_url, parse_html
from src.config import STOCK_LIST
from src import DATA_DIR
from src.db_service import save_forecast

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    )
}

OUTPUT_DIR = DATA_DIR / "forecast" / "detail"
DATE_STR = datetime.now().strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

def parse_value(value_str):
    """Parse a value string into a numeric value."""
    if not value_str or value_str in ('n/a', '-', '—', 'N/A', ''):
        return None
    if 'Pro' in value_str or 'Upgrade' in value_str:
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


# ---------------------------------------------------------------------------
# Table extractors
# ---------------------------------------------------------------------------

def extract_price_targets(table):
    """Extract price target data from the price target table."""
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
            result['low'] = parse_value(cells[1])
            result['average'] = parse_value(cells[2])
            result['median'] = parse_value(cells[3])
            result['high'] = parse_value(cells[4])
        elif label == 'change':
            result['low_change_pct'] = parse_value(cells[1])
            result['average_change_pct'] = parse_value(cells[2])
            result['median_change_pct'] = parse_value(cells[3])
            result['high_change_pct'] = parse_value(cells[4])

    return result if result else None


def extract_analyst_ratings(table):
    """Extract analyst ratings history."""
    header_row = table.find('thead')
    if not header_row:
        return None

    headers = [th.get_text(strip=True) for th in header_row.find_all('th')]
    periods = headers[1:]

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
        key = rating_map.get(cells[0])
        if key:
            result[key] = [int(c) if c.isdigit() else 0 for c in cells[1:]]

    return result


def extract_analyst_recommendations(table):
    """Extract individual analyst recommendations."""
    body = table.find('tbody')
    if not body:
        return None

    recommendations = []
    for row in body.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) < 8:
            continue

        rec = {
            'firm': cells[1].get_text(strip=True),
            'rating': cells[3].get_text(strip=True),
            'action': cells[4].get_text(strip=True),
            'price_target': cells[5].get_text(strip=True),
            'upside': cells[6].get_text(strip=True),
            'date': cells[7].get_text(strip=True),
        }

        first_cell = cells[0].get_text(strip=True)
        firm = rec['firm']
        if firm and firm in first_cell:
            rec['analyst'] = first_cell.replace(firm, '').strip()
        else:
            rec['analyst'] = first_cell

        recommendations.append(rec)

    return recommendations


def extract_annual_forecast(table):
    """Extract full annual forecast timeline."""
    header_row = table.find('thead')
    if not header_row:
        return None

    headers = [th.get_text(strip=True) for th in header_row.find_all('th')]

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

    result = {'periods': fy_headers, 'period_endings': period_endings}

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
        key = row_map.get(cells[0])
        if key:
            result[key] = [parse_value(c) for c in cells[1:]]

    return result


def extract_range_table(table):
    """Extract High/Avg/Low range data."""
    header_row = table.find('thead')
    if not header_row:
        return None

    headers = [th.get_text(strip=True) for th in header_row.find_all('th')]
    table_type = headers[0]
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
            result[key] = [parse_value(c) for c in cells[1:]]

    return table_type, result


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

def scrape_forecast(ticker, current_year=2025):
    """
    Scrape all forecast data for a given ticker.
    Returns a comprehensive dict or None on failure.
    """
    url = f"https://stockanalysis.com/stocks/{ticker.lower()}/forecast/"

    response = fetch_url(url, HEADERS)
    if not response:
        return None

    soup = parse_html(response.text)
    tables = soup.find_all('table')

    if not tables:
        return None

    forecast_data = {
        'ticker': ticker.upper(),
        'date': DATE_STR,
        'price_target': None,
        'analyst_ratings': None,
        'analyst_recommendations': None,
        'annual_forecast': None,
        'forecast_ranges': {},
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

    range_key_map = {
        'Revenue': 'revenue',
        'Revenue Growth': 'revenue_growth',
        'EPS': 'eps',
        'EPS Growth': 'eps_growth',
    }

    for table in tables:
        header_row = table.find('thead')
        if not header_row:
            continue

        headers = [th.get_text(strip=True) for th in header_row.find_all('th')]
        if len(headers) < 2:
            continue

        first_header = headers[0]

        if first_header == 'Target':
            forecast_data['price_target'] = extract_price_targets(table)
        elif first_header == 'Rating':
            forecast_data['analyst_ratings'] = extract_analyst_ratings(table)
        elif first_header == 'Analyst':
            forecast_data['analyst_recommendations'] = extract_analyst_recommendations(table)
        elif first_header == 'Fiscal Year':
            forecast_data['annual_forecast'] = extract_annual_forecast(table)
        elif first_header in range_key_map:
            result = extract_range_table(table)
            if result:
                table_type, range_data = result
                forecast_data['forecast_ranges'][range_key_map[table_type]] = range_data

    # Populate legacy annual format
    _populate_legacy_annual(forecast_data, current_year)

    return forecast_data


def _populate_legacy_annual(forecast_data, current_year):
    """Fill the legacy 'annual' dict from range tables or annual forecast."""
    annual = forecast_data['annual']
    ranges = forecast_data.get('forecast_ranges', {})

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

    # Fallback to annual_forecast table
    af = forecast_data.get('annual_forecast')
    if af:
        current_year_str = str(current_year + 1)
        next_year_str = str(current_year + 2)
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


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_ticker_json(ticker, forecast_data):
    """Save per-ticker comprehensive forecast JSON. Returns output path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{ticker.upper()}_forecast_{DATE_STR}.json"
    with open(path, 'w') as f:
        json.dump(forecast_data, f, indent=2)
    return path


def save_to_db(ticker, forecast_data):
    """Save the legacy annual portion to the database for valuation computation."""
    try:
        snapshot_date = date.fromisoformat(DATE_STR)
        save_forecast(ticker, forecast_data, snapshot_date)
    except Exception as e:
        print(f"  [DB] Error saving {ticker}: {e}")


def has_existing(ticker):
    """Check if a forecast JSON already exists for this ticker today."""
    return (OUTPUT_DIR / f"{ticker.upper()}_forecast_{DATE_STR}.json").exists()


# ---------------------------------------------------------------------------
# Single-ticker scrape + save
# ---------------------------------------------------------------------------

def scrape_and_save(ticker, delay_range=(5, 15)):
    """Scrape one ticker, save JSON and to database. Returns (ticker, success, summary)."""
    time.sleep(random.uniform(*delay_range))

    data = scrape_forecast(ticker)
    if data is None:
        return (ticker, False, None)

    save_ticker_json(ticker, data)
    save_to_db(ticker, data)

    # Build a short summary
    summary = {
        'price_target': data['price_target'] is not None,
        'analyst_ratings': data['analyst_ratings'] is not None,
        'recommendations': len(data.get('analyst_recommendations') or []),
        'annual_periods': len((data.get('annual_forecast') or {}).get('periods', [])),
        'range_categories': len(data.get('forecast_ranges', {})),
        'eps': data['annual'].get('current_eps'),
    }

    return (ticker, True, summary)


# ---------------------------------------------------------------------------
# Collect all unique tickers from STOCK_LIST
# ---------------------------------------------------------------------------

def get_all_tickers():
    """Return sorted list of unique tickers across all industries."""
    tickers = set()
    for industry_tickers in STOCK_LIST.values():
        tickers.update(t.upper() for t in industry_tickers)
    return sorted(tickers)


def get_ticker_industry(ticker):
    """Look up the industry for a given ticker from STOCK_LIST."""
    ticker_upper = ticker.upper()
    for industry, tickers in STOCK_LIST.items():
        if ticker_upper in [t.upper() for t in tickers]:
            return industry
    return 'Unknown'


# ---------------------------------------------------------------------------
# Batch scraper
# ---------------------------------------------------------------------------

def scrape_all(workers=1, skip_existing=False, delay_range=(5, 15)):
    """Scrape forecast for every stock in STOCK_LIST."""
    all_tickers = get_all_tickers()
    total = len(all_tickers)

    if skip_existing:
        all_tickers = [t for t in all_tickers if not has_existing(t)]
        skipped = total - len(all_tickers)
        if skipped:
            print(f"Skipping {skipped} tickers with existing data for {DATE_STR}")
        total_to_scrape = len(all_tickers)
    else:
        total_to_scrape = total

    if total_to_scrape == 0:
        print("Nothing to scrape - all tickers already have data for today.")
        return

    print(f"Scraping forecast for {total_to_scrape} stocks (workers={workers}, delay={delay_range[0]}-{delay_range[1]}s)")
    print(f"Output: {OUTPUT_DIR}")
    print("-" * 70)

    succeeded = []
    failed = []

    if workers <= 1:
        for i, ticker in enumerate(all_tickers, 1):
            print(f"[{i}/{total_to_scrape}] {ticker} ...", end=" ", flush=True)
            t, ok, summary = scrape_and_save(ticker, delay_range)
            if ok:
                eps_str = f"EPS=${summary['eps']}" if summary['eps'] else "no EPS"
                print(f"OK ({summary['annual_periods']} periods, {summary['recommendations']} recs, {eps_str})")
                succeeded.append(t)
            else:
                print("FAILED")
                failed.append(t)
    else:
        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for ticker in all_tickers:
                fut = pool.submit(scrape_and_save, ticker, delay_range)
                futures[fut] = ticker

            done_count = 0
            for fut in as_completed(futures):
                done_count += 1
                ticker = futures[fut]
                try:
                    t, ok, summary = fut.result()
                    if ok:
                        eps_str = f"EPS=${summary['eps']}" if summary['eps'] else "no EPS"
                        print(f"[{done_count}/{total_to_scrape}] {t} - OK ({summary['annual_periods']} periods, {summary['recommendations']} recs, {eps_str})")
                        succeeded.append(t)
                    else:
                        print(f"[{done_count}/{total_to_scrape}] {t} - FAILED")
                        failed.append(t)
                except Exception as e:
                    print(f"[{done_count}/{total_to_scrape}] {ticker} - ERROR: {e}")
                    failed.append(ticker)

    # Summary
    print("-" * 70)
    print(f"Done. {len(succeeded)} succeeded, {len(failed)} failed out of {total_to_scrape}.")
    if failed:
        print(f"Failed tickers: {', '.join(failed)}")

    # Also save combined legacy JSON for backward compatibility
    if succeeded:
        _save_combined_json(succeeded)


def _save_combined_json(tickers):
    """Read individual ticker JSONs and combine into the legacy bulk format."""
    combined = {}
    for ticker in tickers:
        path = OUTPUT_DIR / f"{ticker.upper()}_forecast_{DATE_STR}.json"
        if path.exists():
            with open(path) as f:
                combined[ticker] = json.load(f)

    output_path = DATA_DIR / "forecast" / f"stock_list_forecasts_{DATE_STR}.json"
    with open(output_path, 'w') as f:
        json.dump(combined, f, indent=2)
    print(f"Saved combined forecast file: {output_path} ({len(combined)} stocks)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape analyst forecast from stockanalysis.com")
    parser.add_argument('--ticker', type=str, default=None, help='Single stock ticker to scrape')
    parser.add_argument('--all', action='store_true', help='Scrape all stocks from STOCK_LIST')
    parser.add_argument('--workers', type=int, default=1, help='Number of parallel workers (default: 1)')
    parser.add_argument('--skip-existing', action='store_true', help='Skip tickers already scraped today')
    parser.add_argument('--delay-min', type=float, default=5, help='Min delay between requests in seconds (default: 5)')
    parser.add_argument('--delay-max', type=float, default=15, help='Max delay between requests in seconds (default: 15)')
    args = parser.parse_args()

    if not args.all and not args.ticker:
        parser.error("Provide --ticker AAPL or --all")

    delay_range = (args.delay_min, args.delay_max)

    if args.all:
        scrape_all(
            workers=args.workers,
            skip_existing=args.skip_existing,
            delay_range=delay_range,
        )
    else:
        ticker = args.ticker.upper()
        print(f"Scraping forecast for {ticker} ...")
        data = scrape_forecast(ticker)
        if data is None:
            print(f"Failed to scrape forecast for {ticker}")
            sys.exit(1)

        path = save_ticker_json(ticker, data)
        save_to_db(ticker, data)

        pt = data.get('price_target')
        annual = data.get('annual', {})
        af = data.get('annual_forecast')
        recs = data.get('analyst_recommendations') or []

        print(f"Saved to {path}")
        print()
        print(f"--- {ticker} Forecast Summary ---")
        if pt:
            print(f"  Price Target:  Low=${pt.get('low')}  Avg=${pt.get('average')}  Median=${pt.get('median')}  High=${pt.get('high')}")
        if annual.get('current_eps'):
            print(f"  EPS (current): ${annual['current_eps']}  Growth: {annual.get('current_growth')}%")
        if annual.get('next_year_eps'):
            print(f"  EPS (next yr): ${annual['next_year_eps']}  Growth: {annual.get('next_year_growth')}%")
        if annual.get('current_revenue'):
            rev = annual['current_revenue']
            rev_str = f"${rev/1e9:.2f}B" if rev >= 1e9 else f"${rev/1e6:.1f}M"
            print(f"  Revenue:       {rev_str}  Growth: {annual.get('current_revenue_growth')}%")
        if af:
            print(f"  Timeline:      {len(af.get('periods', []))} fiscal years")
        print(f"  Analyst Recs:  {len(recs)}")
        print()

        # Show top 5 analyst recommendations
        if recs:
            print("  Recent Recommendations:")
            for rec in recs[:5]:
                print(f"    {rec.get('analyst', '?'):20s}  {rec.get('firm', ''):15s}  {rec.get('rating', ''):20s}  {rec.get('price_target', '')}")


if __name__ == "__main__":
    main()
