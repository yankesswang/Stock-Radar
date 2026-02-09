"""Parallel scraper runner - executes all three main scrapers concurrently."""

import threading
import json
from datetime import datetime, date
from src.scrapers.ratio_scraper import Ratio_Scraper_Fixed
from src.scrapers.pe_scraper import PERatioScraper
from src.scrapers.forecast_scraper import Forecast_Scraper_Working
from src.db_service import compute_valuations_for_date


def run_ratio_scraper():
    """Run the ratio/metrics scraper."""
    print("[Ratio Scraper] Starting...")
    scraper = Ratio_Scraper_Fixed()
    scraper.run()
    print("[Ratio Scraper] Complete.")


def run_pe_scraper():
    """Run the PE ratio scraper."""
    print("[PE Scraper] Starting...")
    scraper = PERatioScraper()
    scraper.run()
    print("[PE Scraper] Complete.")


def run_forecast_scraper():
    """Run the forecast scraper."""
    print("[Forecast Scraper] Starting...")
    scraper = Forecast_Scraper_Working()
    scraper.run()
    print("[Forecast Scraper] Complete.")


def run_all():
    """Run all three scrapers in parallel, then compute valuations."""
    scrapers = [
        ("Ratio", run_ratio_scraper),
        ("PE", run_pe_scraper),
        ("Forecast", run_forecast_scraper),
    ]

    threads = []
    for name, func in scrapers:
        t = threading.Thread(target=func, name=name)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    print("\nAll scrapers completed.")
    
    # Compute valuations for today's date
    today = date.today()
    print(f"\nComputing valuations for {today}...")
    compute_valuations_for_date(today)
    print("Valuation computation complete.")


if __name__ == "__main__":
    run_all()
