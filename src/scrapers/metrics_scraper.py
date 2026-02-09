"""
Scrape the /metrics/ page from stockanalysis.com for a given ticker.

Each page contains multiple tables (e.g. Revenue by Segment, Gross Profit
by Type, Gross Margin by Type, Revenue by Geography, Operating Expense
Breakdown). Every table uses quarterly period columns.

Output: one CSV per ticker with columns
    [Category, Metric, <period_1>, <period_2>, …]
saved to  data/metrics/{TICKER}_metrics_{YYYY-MM-DD}.csv
"""

from src.utils import fetch_url, parse_html
from src.config import STOCK_LIST
from src import DATA_DIR
from src.db_service import save_metrics_breakdown

import pandas as pd
import time
import random
from datetime import datetime, date


class MetricsScraper:
    BASE_URL = "https://stockanalysis.com/stocks/{ticker}/metrics/"

    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.output_dir = DATA_DIR / "metrics"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── value parsing ────────────────────────────────────────────────

    @staticmethod
    def parse_value(text):
        """Convert a display string ('12.51%', '225.72B', '$3.41', …) to a
        Python float.  Returns None for missing / paywall data."""
        if not text or text in ("n/a", "-", "\u2014", ""):
            return None
        if "Upgrade" in text or "PRO" in text:
            return None

        text = text.replace("$", "").replace(",", "").strip()

        try:
            if "%" in text:
                return float(text.replace("%", ""))

            multipliers = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}
            for suffix, mul in multipliers.items():
                if text.endswith(suffix):
                    return float(text[:-1]) * mul

            return float(text)
        except (ValueError, AttributeError):
            return text

    # ── single-table parsing ─────────────────────────────────────────

    def _parse_table(self, table, category):
        """Return a list of dicts (one per metric row) for *table*."""
        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        # Header row → period labels (skip the first "Period Ending" cell)
        header_cells = rows[0].find_all(["th", "td"])
        periods = [c.get_text(strip=True) for c in header_cells[1:]]

        data = []
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            metric_name = cells[0].get_text(strip=True)
            if not metric_name or metric_name in ("Metric", "Period Ending"):
                continue

            record = {"Category": category, "Metric": metric_name}
            for idx, cell in enumerate(cells[1:]):
                if idx < len(periods):
                    raw = cell.get("title") or cell.get_text(strip=True)
                    record[periods[idx]] = self.parse_value(raw)
            data.append(record)

        return data

    # ── full-page scrape ─────────────────────────────────────────────

    def scrape_metrics(self, ticker):
        """Scrape all metrics tables for *ticker*.

        Returns a single ``pandas.DataFrame`` with columns
        ``[Category, Metric, <period_1>, …]`` or ``None`` on failure.
        """
        url = self.BASE_URL.format(ticker=ticker.lower())
        print(f"  Fetching {url}")
        response = fetch_url(url, self.headers)

        if not response:
            print(f"  Failed to fetch metrics for {ticker}")
            return None

        soup = parse_html(response.text)
        tables = soup.find_all("table")

        if not tables:
            print(f"  No tables found for {ticker}")
            return None

        all_rows = []
        for table in tables:
            # Resolve section heading – use find_previous (not sibling)
            heading = table.find_previous(["h1", "h2", "h3"])
            category = heading.get_text(strip=True) if heading else "Unknown"

            rows = self._parse_table(table, category)
            all_rows.extend(rows)

        if not all_rows:
            print(f"  No metric rows parsed for {ticker}")
            return None

        df = pd.DataFrame(all_rows)

        # Reorder: Category, Metric first, then periods in chronological order
        meta_cols = ["Category", "Metric"]
        period_cols = [c for c in df.columns if c not in meta_cols]
        df = df[meta_cols + period_cols]

        print(f"  {ticker}: {len(df)} metrics x {len(period_cols)} periods across {len(tables)} tables")
        return df

    # ── persistence ──────────────────────────────────────────────────

    def save(self, ticker, df):
        """Save *df* as a dated CSV and to database, return the file path."""
        path = self.output_dir / f"{ticker.upper()}_metrics_{self.current_date}.csv"
        df.to_csv(path, index=False)
        print(f"  Saved → {path}")
        
        # Save to database
        snapshot_date = date.fromisoformat(self.current_date)
        save_metrics_breakdown(ticker, df, snapshot_date)
        
        return str(path)

    # ── batch run ────────────────────────────────────────────────────

    def run(self, tickers=None):
        """Scrape metrics for every ticker in *tickers* (or STOCK_LIST).

        Returns dict  ``{ticker: filepath}``  of successfully saved CSVs.
        """
        if tickers is None:
            tickers = [t for companies in STOCK_LIST.values() for t in companies]

        results = {}
        for i, ticker in enumerate(tickers, 1):
            print(f"[{i}/{len(tickers)}] {ticker}")
            df = self.scrape_metrics(ticker)
            if df is not None:
                path = self.save(ticker, df)
                results[ticker] = path

            if i < len(tickers):
                delay = random.uniform(10, 30)
                print(f"  Waiting {delay:.0f}s …")
                time.sleep(delay)

        print(f"\nDone – scraped {len(results)}/{len(tickers)} tickers")
        return results


# ── quick test ───────────────────────────────────────────────────────
if __name__ == "__main__":
    scraper = MetricsScraper()

    # Single-ticker test
    ticker = "aapl"
    df = scraper.scrape_metrics(ticker)

    if df is not None:
        print(f"\nShape: {df.shape}")
        print(f"\nCategories: {df['Category'].unique().tolist()}")
        print(f"\nFirst 8 rows:")
        print(df.head(8).to_string(max_cols=6))
        scraper.save(ticker, df)
    else:
        print("Scraping failed.")
