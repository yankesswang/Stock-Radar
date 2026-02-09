"""
Scrape the /statistics/ page from stockanalysis.com for a given ticker.

The page contains ~104 key-value pairs grouped into ~19 sections
(Total Valuation, Valuation Ratios, Financial Efficiency, etc.).

Output: one JSON per ticker at
    data/statistics/{TICKER}_statistics_{YYYY-MM-DD}.json

Structure:
    {
        "ticker": "AAPL",
        "date": "2026-02-01",
        "sections": [
            {
                "name": "Total Valuation",
                "items": [
                    {"label": "Market Cap", "value": 3809462207200, "display": "3.81T"},
                    ...
                ]
            },
            ...
        ]
    }
"""

from src.utils import fetch_url, parse_html
from src.config import STOCK_LIST
from src import DATA_DIR
from src.db_service import save_statistics

import json
import time
import random
from datetime import datetime, date


class StatisticsScraper:
    BASE_URL = "https://stockanalysis.com/stocks/{ticker}/statistics/"

    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.output_dir = DATA_DIR / "statistics"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── value parsing ────────────────────────────────────────────────

    @staticmethod
    def parse_value(text):
        """Convert a display string to a Python float/str.
        Returns (parsed_value, display_string)."""
        if not text or text in ("n/a", "-", "\u2014", ""):
            return None, text or "—"
        if "Upgrade" in text or "PRO" in text:
            return None, "—"

        display = text.strip()
        cleaned = text.replace("$", "").replace(",", "").strip()

        try:
            if "%" in cleaned:
                return float(cleaned.replace("%", "")), display

            multipliers = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}
            for suffix, mul in multipliers.items():
                if cleaned.endswith(suffix):
                    return float(cleaned[:-1]) * mul, display

            return float(cleaned), display
        except (ValueError, AttributeError):
            return text, display

    # ── page scrape ──────────────────────────────────────────────────

    def scrape_statistics(self, ticker):
        """Scrape all statistics for *ticker*, returning structured dict."""
        url = self.BASE_URL.format(ticker=ticker.lower())
        print(f"  Fetching {url}")
        response = fetch_url(url, self.headers)

        if not response:
            print(f"  Failed to fetch statistics for {ticker}")
            return None

        soup = parse_html(response.text)

        # Strategy: iterate all <table> elements.  For each table, the
        # nearest preceding <h2> is the section name.  Each <tr> with
        # two <td> cells is a label/value pair.
        tables = soup.find_all("table")
        if not tables:
            print(f"  No tables found for {ticker}")
            return None

        sections = []
        seen_sections = set()

        for table in tables:
            heading = table.find_previous("h2")
            section_name = heading.get_text(strip=True) if heading else "Other"

            items = []
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                label = cells[0].get_text(strip=True)
                if not label:
                    continue

                raw = cells[-1].get("title") or cells[-1].get_text(strip=True)
                parsed, display = self.parse_value(raw)

                items.append({
                    "label": label,
                    "value": parsed,
                    "display": display,
                })

            if items:
                # Multiple tables can share the same heading; merge them
                if section_name in seen_sections:
                    for sec in sections:
                        if sec["name"] == section_name:
                            sec["items"].extend(items)
                            break
                else:
                    sections.append({"name": section_name, "items": items})
                    seen_sections.add(section_name)

        total_items = sum(len(s["items"]) for s in sections)
        print(f"  {ticker.upper()}: {total_items} items across {len(sections)} sections")

        return {
            "ticker": ticker.upper(),
            "date": self.current_date,
            "sections": sections,
        }

    # ── persistence ──────────────────────────────────────────────────

    def save(self, ticker, data):
        """Save as dated JSON and to database, return file path."""
        path = self.output_dir / f"{ticker.upper()}_statistics_{self.current_date}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  Saved → {path}")
        
        # Save to database
        snapshot_date = date.fromisoformat(self.current_date)
        save_statistics(ticker, data, snapshot_date)
        
        return str(path)

    # ── batch run ────────────────────────────────────────────────────

    def run(self, tickers=None):
        """Scrape statistics for every ticker in *tickers* (or STOCK_LIST)."""
        if tickers is None:
            tickers = [t for companies in STOCK_LIST.values() for t in companies]

        results = {}
        for i, ticker in enumerate(tickers, 1):
            print(f"[{i}/{len(tickers)}] {ticker}")
            data = self.scrape_statistics(ticker)
            if data:
                path = self.save(ticker, data)
                results[ticker] = path

            if i < len(tickers):
                delay = random.uniform(10, 30)
                print(f"  Waiting {delay:.0f}s …")
                time.sleep(delay)

        print(f"\nDone – scraped {len(results)}/{len(tickers)} tickers")
        return results


# ── quick test ───────────────────────────────────────────────────────
if __name__ == "__main__":
    scraper = StatisticsScraper()

    ticker = "aapl"
    data = scraper.scrape_statistics(ticker)

    if data:
        for sec in data["sections"]:
            print(f"\n{'─'*50}")
            print(f"  {sec['name']} ({len(sec['items'])} items)")
            for item in sec["items"][:4]:
                print(f"    {item['label']}: {item['display']}")
            if len(sec["items"]) > 4:
                print(f"    ... +{len(sec['items']) - 4} more")

        scraper.save(ticker, data)
    else:
        print("Scraping failed.")
