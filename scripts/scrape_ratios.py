"""
Scrape financial ratios table from stockanalysis.com

Usage:
    python3 scripts/scrape_ratios.py --ticker AAPL          # single stock
    python3 scripts/scrape_ratios.py --all                   # all stocks from STOCK_LIST
    python3 scripts/scrape_ratios.py --all --workers 3       # parallel (3 threads)
    python3 scripts/scrape_ratios.py --all --skip-existing   # skip already-scraped tickers
"""
import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import fetch_url, parse_html
from src.config import STOCK_LIST
from src import DATA_DIR
from src.db_service import save_metrics
import pandas as pd


HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    )
}

OUTPUT_DIR = DATA_DIR / "financials"
DATE_STR = datetime.now().strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

def parse_value(value_str):
    """Parse a value string into a numeric value."""
    if not value_str or value_str in ('n/a', '-', '—', ''):
        return None
    if 'Upgrade' in value_str or 'PRO' in value_str:
        return None

    value_str = value_str.replace('$', '').replace(',', '').strip()

    try:
        if '%' in value_str:
            return float(value_str.replace('%', ''))

        multipliers = {'T': 1e12, 'B': 1e9, 'M': 1e6, 'K': 1e3}
        for suffix, multiplier in multipliers.items():
            if value_str.endswith(suffix):
                return float(value_str[:-1]) * multiplier

        return float(value_str)
    except (ValueError, AttributeError):
        return value_str


# ---------------------------------------------------------------------------
# Section classification
# ---------------------------------------------------------------------------

SECTION_MAP = {
    'Market Capitalization': 'Valuation',
    'Market Cap':            'Valuation',
    'Market Cap Growth':     'Valuation',
    'Enterprise Value':      'Valuation',
    'Last Close Price':      'Valuation',
    'PE Ratio':              'Valuation',
    'Forward PE':            'Valuation',
    'PS Ratio':              'Valuation',
    'PB Ratio':              'Valuation',
    'P/TBV Ratio':           'Valuation',
    'P/FCF Ratio':           'Valuation',
    'P/OCF Ratio':           'Valuation',
    'PEG Ratio':             'Valuation',
    'EV / Sales':            'Enterprise Value',
    'EV / EBITDA':           'Enterprise Value',
    'EV / EBIT':             'Enterprise Value',
    'EV / FCF':              'Enterprise Value',
    'EV/Sales Ratio':        'Enterprise Value',
    'EV/EBITDA Ratio':       'Enterprise Value',
    'EV/EBIT Ratio':         'Enterprise Value',
    'EV/FCF Ratio':          'Enterprise Value',
    'Debt / Equity Ratio':   'Leverage',
    'Debt / EBITDA Ratio':   'Leverage',
    'Debt / FCF Ratio':      'Leverage',
    'Asset Turnover':        'Efficiency',
    'Inventory Turnover':    'Efficiency',
    'Quick Ratio':           'Efficiency',
    'Current Ratio':         'Efficiency',
    'Return on Equity (ROE)':            'Returns',
    'Return on Assets (ROA)':            'Returns',
    'Return on Invested Capital (ROIC)': 'Returns',
    'Return on Capital Employed (ROCE)': 'Returns',
    'Earnings Yield':             'Yield & Shareholder Returns',
    'FCF Yield':                  'Yield & Shareholder Returns',
    'Dividend Yield':             'Yield & Shareholder Returns',
    'Payout Ratio':               'Yield & Shareholder Returns',
    'Buyback Yield / Dilution':   'Yield & Shareholder Returns',
    'Total Shareholder Return':   'Yield & Shareholder Returns',
}


def classify_section(metric_name):
    """Return the section name for a metric, falling back to fuzzy matching."""
    if metric_name in SECTION_MAP:
        return SECTION_MAP[metric_name]
    low = metric_name.lower()
    if 'ev/' in low:
        return 'Enterprise Value'
    if 'debt' in low:
        return 'Leverage'
    if 'return' in low or 'roe' in low or 'roa' in low or 'roic' in low or 'roce' in low:
        return 'Returns'
    if 'yield' in low or 'payout' in low or 'buyback' in low or 'shareholder' in low:
        return 'Yield & Shareholder Returns'
    if 'turnover' in low or 'quick' in low or 'current ratio' in low:
        return 'Efficiency'
    return 'Valuation'


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

def scrape_ratios(ticker):
    """
    Scrape the financial ratios table from stockanalysis.com for a given ticker.
    Returns a pandas DataFrame with metrics as rows and fiscal periods as columns.
    """
    url = f"https://stockanalysis.com/stocks/{ticker.lower()}/financials/ratios/"

    response = fetch_url(url, HEADERS)
    if not response:
        return None

    soup = parse_html(response.text)
    tables = soup.find_all('table')

    if not tables:
        return None

    all_rows = []

    for table in tables:
        thead = table.find('thead')
        if not thead:
            continue

        header_cells = thead.find_all(['th', 'td'])
        if len(header_cells) < 2:
            continue

        periods = [cell.get_text(strip=True) for cell in header_cells[1:]]

        tbody = table.find('tbody')
        if not tbody:
            continue

        for row in tbody.find_all('tr'):
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2:
                continue

            metric_name = cells[0].get_text(strip=True)
            if not metric_name or metric_name in ('Metric', 'Period Ending', ''):
                continue

            section = classify_section(metric_name)
            row_data = {'Section': section, 'Metric': metric_name}
            for i, cell in enumerate(cells[1:]):
                if i < len(periods):
                    raw = cell.get('title') or cell.get_text(strip=True)
                    row_data[periods[i]] = parse_value(raw)

            all_rows.append(row_data)

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows)

    fixed_cols = ['Section', 'Metric']
    period_cols = [c for c in df.columns if c not in fixed_cols]
    df = df[fixed_cols + period_cols]

    return df


# ---------------------------------------------------------------------------
# Save helper
# ---------------------------------------------------------------------------

CSV_METRIC_TO_JSON_KEY = {
    'Market Capitalization': 'marketcap',
    'Market Cap': 'marketcap',
    'Enterprise Value': 'enterpriseValue',
    'Last Close Price': 'currentPrice',
    'PE Ratio': 'pe',
    'Forward PE': 'peForward',
    'PS Ratio': 'ps',
    'PB Ratio': 'pb',
    'PEG Ratio': 'pegRatio',
    'EV / Sales': 'evSales',
    'EV / EBITDA': 'evEbitda',
    'EV / EBIT': 'evEbit',
    'EV / FCF': 'evFcf',
    'EV/Sales Ratio': 'evSales',
    'EV/EBITDA Ratio': 'evEbitda',
    'EV/EBIT Ratio': 'evEbit',
    'EV/FCF Ratio': 'evFcf',
    'Current Ratio': 'currentRatio',
    'Quick Ratio': 'quickRatio',
    'Debt / Equity Ratio': 'debtEquity',
    'Debt / EBITDA Ratio': 'debtEbitda',
    'Debt / FCF Ratio': 'debtFcf',
    'Gross Margin': 'grossMargin',
    'Operating Margin': 'operatingMargin',
    'Pretax Margin': 'pretaxMargin',
    'Profit Margin': 'profitMargin',
    'EBITDA Margin': 'ebitdaMargin',
    'EBIT Margin': 'ebitMargin',
    'FCF Margin': 'fcfMargin',
    'Return on Equity (ROE)': 'roe',
    'Return on Assets (ROA)': 'roa',
    'Return on Invested Capital (ROIC)': 'roic',
    'Return on Capital Employed (ROCE)': 'roce',
    'Earnings Yield': 'earningsYield',
    'FCF Yield': 'fcfYield',
    'Dividend Yield': 'dividendYield',
    'Payout Ratio': 'payoutRatio',
    'Buyback Yield / Dilution': 'buybackYield',
}


def save_ratios(ticker, df, industry=None):
    """Save a single ticker's ratios DataFrame to CSV and database. Returns the output path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{ticker.upper()}_ratios_{DATE_STR}.csv"
    df.to_csv(output_path, index=False)

    # Also save to database - convert df to dict format for db_service
    try:
        from datetime import date
        metrics_dict = {}
        if industry:
            metrics_dict['industry'] = industry

        # Convert DataFrame rows to metrics dictionary using proper key mapping
        period_cols = [c for c in df.columns if c not in ('Section', 'Metric')]
        if period_cols:
            latest_period = period_cols[0]  # "Current" or most recent period
            for _, row in df.iterrows():
                metric_name = row.get('Metric', '')
                value = row.get(latest_period)
                if metric_name and value is not None and not isinstance(value, str):
                    json_key = CSV_METRIC_TO_JSON_KEY.get(metric_name)
                    if json_key:
                        # Convert millions in CSV to raw numbers for market cap/enterprise value
                        if json_key in ('marketcap', 'enterpriseValue') and isinstance(value, (int, float)):
                            value = value * 1e6  # CSV stores in millions
                        metrics_dict[json_key] = value

        # Compute at call time, not from the module-level DATE_STR frozen at
        # import — a long-running backend that crosses midnight would otherwise
        # save under the stale date and break the valuation join.
        snapshot_date = date.today()
        save_metrics(ticker, industry or 'Unknown', metrics_dict, snapshot_date)
    except Exception as e:
        print(f"  [DB] Error saving {ticker} to database: {e}")

    return output_path


def has_existing(ticker):
    """Check if a ratios CSV already exists for this ticker today."""
    return (OUTPUT_DIR / f"{ticker.upper()}_ratios_{DATE_STR}.csv").exists()


# ---------------------------------------------------------------------------
# Single-ticker scrape + save
# ---------------------------------------------------------------------------

def scrape_and_save(ticker, delay_range=(5, 15)):
    """Scrape one ticker, save CSV and to database, return (ticker, success, num_metrics)."""
    # Polite delay before each request
    time.sleep(random.uniform(*delay_range))

    df = scrape_ratios(ticker)
    if df is None:
        return (ticker, False, 0)

    industry = get_ticker_industry(ticker)
    save_ratios(ticker, df, industry=industry)
    return (ticker, True, len(df))


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
    """Scrape ratios for every stock in STOCK_LIST."""
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
        print("Nothing to scrape — all tickers already have data for today.")
        return

    print(f"Scraping ratios for {total_to_scrape} stocks (workers={workers}, delay={delay_range[0]}-{delay_range[1]}s)")
    print("-" * 60)

    succeeded = []
    failed = []

    if workers <= 1:
        # Sequential mode
        for i, ticker in enumerate(all_tickers, 1):
            print(f"[{i}/{total_to_scrape}] {ticker} ...", end=" ", flush=True)
            t, ok, n = scrape_and_save(ticker, delay_range)
            if ok:
                print(f"OK ({n} metrics)")
                succeeded.append(t)
            else:
                print("FAILED")
                failed.append(t)
    else:
        # Parallel mode with ThreadPoolExecutor
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
                    t, ok, n = fut.result()
                    if ok:
                        print(f"[{done_count}/{total_to_scrape}] {t} — OK ({n} metrics)")
                        succeeded.append(t)
                    else:
                        print(f"[{done_count}/{total_to_scrape}] {t} — FAILED")
                        failed.append(t)
                except Exception as e:
                    print(f"[{done_count}/{total_to_scrape}] {ticker} — ERROR: {e}")
                    failed.append(ticker)

    # Summary
    print("-" * 60)
    print(f"Done. {len(succeeded)} succeeded, {len(failed)} failed out of {total_to_scrape}.")
    if failed:
        print(f"Failed tickers: {', '.join(failed)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape financial ratios from stockanalysis.com")
    parser.add_argument('--ticker', type=str, default=None, help='Single stock ticker to scrape')
    parser.add_argument('--all', action='store_true', help='Scrape all stocks from STOCK_LIST')
    parser.add_argument('--workers', type=int, default=1, help='Number of parallel workers (default: 1)')
    parser.add_argument('--skip-existing', action='store_true', help='Skip tickers already scraped today')
    parser.add_argument('--delay-min', type=float, default=5, help='Min delay between requests in seconds (default: 5)')
    parser.add_argument('--delay-max', type=float, default=15, help='Max delay between requests in seconds (default: 15)')
    parser.add_argument('--output', type=str, default=None, help='Output CSV path (single ticker mode only)')
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
        # Single ticker mode
        print(f"Scraping {args.ticker.upper()} ...")
        df = scrape_ratios(args.ticker)
        if df is None:
            print(f"Failed to scrape ratios for {args.ticker.upper()}")
            sys.exit(1)

        industry = get_ticker_industry(args.ticker)
        
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(output_path, index=False)
            # Also save to database
            save_ratios(args.ticker, df, industry=industry)
        else:
            output_path = save_ratios(args.ticker, df, industry=industry)

        print(f"Saved {len(df)} metrics to {output_path}")

        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 200)
        print(f"\n--- Preview ---")
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
