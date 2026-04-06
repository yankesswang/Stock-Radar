"""
Scrape historical PE ratio data from fullratio.com.

URL pattern: https://fullratio.com/stocks/{exchange}-{ticker}/pe-ratio

Uses curl_cffi to impersonate Chrome's TLS fingerprint (bypasses 403/Cloudflare).

Usage:
    python3 scripts/scrape_fullratio_pe.py --ticker ADI
    python3 scripts/scrape_fullratio_pe.py --ticker ADI --exchange nasdaq
    python3 scripts/scrape_fullratio_pe.py --all
    python3 scripts/scrape_fullratio_pe.py --all --workers 2
"""
import argparse
import json
import math
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import numpy as np
from bs4 import BeautifulSoup

# curl_cffi impersonates Chrome's TLS handshake — required to bypass fullratio's bot block.
try:
    from curl_cffi import requests as cffi_requests
    _CFFI = True
except ImportError:
    import requests as cffi_requests  # type: ignore[no-redef]
    _CFFI = False
    print("WARNING: curl_cffi not installed. Install with: pip install curl_cffi")
    print("         Falling back to plain requests (may still get 403).\n")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import STOCK_LIST
from src import DATA_DIR
from src.db_service import save_pe_median
from backend.app.database import SessionLocal
from backend.app.models import Metric, Stock
from src.utils import compute_iqr_statistics, filter_outliers

OUTPUT_DIR = DATA_DIR / "pe" / "fullratio"
DATE_STR = datetime.now().strftime("%Y-%m-%d")

# ---------------------------------------------------------------------------
# Exchange mapping  (yfinance exchangeCode → fullratio URL prefix)
# ---------------------------------------------------------------------------

_YF_TO_FULLRATIO = {
    "NMS": "nasdaq", "NGM": "nasdaq", "NCM": "nasdaq",
    "NasdaqGS": "nasdaq", "NasdaqGM": "nasdaq", "NasdaqCM": "nasdaq",
    "NASDAQ": "nasdaq",
    "NYQ": "nyse", "NYS": "nyse", "ASE": "nyse", "PCX": "nyse",
    "NYSE": "nyse",
}

_EXCHANGE_CANDIDATES = ["nasdaq", "nyse", "nyse-arca", "nyse-mkt"]


# ---------------------------------------------------------------------------
# Exchange detection
# ---------------------------------------------------------------------------

def get_exchange(ticker: str) -> str | None:
    """Return the fullratio exchange prefix for a ticker via yfinance."""
    try:
        import yfinance as yf
        code = yf.Ticker(ticker).info.get("exchange", "")
        return _YF_TO_FULLRATIO.get(code.upper()) or _YF_TO_FULLRATIO.get(code)
    except Exception:
        return None


def get_pe_fallback(ticker: str) -> tuple[float | None, str | None]:
    """
    Fallback PE from yfinance when fullratio has no usable data.
    Priority: trailingPE -> forwardPE.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        trailing = info.get("trailingPE")
        forward = info.get("forwardPE")

        if isinstance(trailing, (int, float)) and trailing > 0:
            return round(float(trailing), 1), "yfinance.trailingPE"
        if isinstance(forward, (int, float)) and forward > 0:
            return round(float(forward), 1), "yfinance.forwardPE"
    except Exception as exc:
        print(f"  [WARN] yfinance fallback failed for {ticker}: {exc}")

    # Second fallback: latest ratios snapshot in local DB
    db = SessionLocal()
    try:
        row = (
            db.query(Metric)
            .join(Stock, Stock.id == Metric.stock_id)
            .filter(Stock.ticker == ticker.upper())
            .order_by(Metric.snapshot_date.desc())
            .first()
        )
        if row:
            pe_forward = row.pe_forward
            pe = row.pe

            if isinstance(pe_forward, (int, float)) and not math.isnan(pe_forward) and pe_forward > 0:
                return round(float(pe_forward), 1), "db.metrics.pe_forward"
            if isinstance(pe, (int, float)) and not math.isnan(pe) and pe > 0:
                return round(float(pe), 1), "db.metrics.pe"
    except Exception as exc:
        print(f"  [WARN] DB fallback failed for {ticker}: {exc}")
    finally:
        db.close()

    return None, None


# ---------------------------------------------------------------------------
# Fetch helper
# ---------------------------------------------------------------------------

def _fetch(url: str) -> str | None:
    """
    Fetch a URL using curl_cffi Chrome impersonation.
    Returns HTML text on 200, None on 404 (wrong exchange), raises on other errors.
    """
    kwargs = dict(timeout=20)
    if _CFFI:
        kwargs["impersonate"] = "chrome"

    for attempt in range(3):
        try:
            resp = cffi_requests.get(url, **kwargs)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 404:
                return None          # Wrong exchange slug — caller tries next
            print(f"  HTTP {resp.status_code} on attempt {attempt + 1}: {url}")
            if attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as exc:
            print(f"  Attempt {attempt + 1} error: {exc}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def build_url(exchange: str, ticker: str) -> str:
    return f"https://fullratio.com/stocks/{exchange}-{ticker.lower()}/pe-ratio"


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def parse_summary_stats(html: str) -> dict:
    """
    Extract the summary metric boxes from the page, e.g.:
      Today, Average, Median, Minimum, Maximum,
      3-year avg, 5-year avg, 10-year avg, 15-year avg
    Returns a dict keyed by label (lowercased, spaces→underscores).
    """
    soup = BeautifulSoup(html, "html.parser")
    stats = {}
    for box in soup.find_all("div", class_="metric-box-square"):
        title = box.find("div", class_="metric-box-title")
        value = box.find("div", class_="metric-box-value")
        if not title or not value:
            continue
        label = title.get_text(strip=True)
        raw   = value.get_text(strip=True).replace(",", "")
        try:
            stats[label] = float(raw)
        except ValueError:
            pass
    return stats


def parse_pe_table(html: str) -> list[dict]:
    """
    Extract PE ratio rows from the page.
    Returns a list of {"date": str, "pe": float} dicts, newest first.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows_data = []

    # Strategy 1 — standard <table> elements
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            date_text = cells[0].get_text(strip=True)
            pe_text = cells[1].get_text(strip=True).replace(",", "")
            try:
                pe_val = float(pe_text)
                if pe_val > 0:
                    rows_data.append({"date": date_text, "pe": pe_val})
            except ValueError:
                continue

    if rows_data:
        return rows_data

    # Strategy 2 — div/span rows (in case site uses a custom layout)
    for row in soup.find_all(["tr", "div"]):
        cls = " ".join(row.get("class", []))
        if "row" not in cls.lower():
            continue
        cells = row.find_all(["td", "th", "span", "div"])
        if len(cells) < 2:
            continue
        date_text = cells[0].get_text(strip=True)
        pe_text = cells[1].get_text(strip=True).replace(",", "")
        try:
            pe_val = float(pe_text)
            if pe_val > 0:
                rows_data.append({"date": date_text, "pe": pe_val})
        except ValueError:
            continue

    return rows_data


# ---------------------------------------------------------------------------
# Median computation (same IQR-filter logic as pe_scraper.py)
# ---------------------------------------------------------------------------

def compute_median(pe_records: list[dict], num_latest: int = 20) -> float | None:
    """Compute IQR-filtered median from the most recent N PE values."""
    values = [r["pe"] for r in pe_records if r["pe"] > 0][:num_latest]
    if not values:
        return None
    arr = np.array(values)
    q1, q3, iqr = compute_iqr_statistics(arr)
    filtered = filter_outliers(arr, q1, q3, iqr)
    return round(float(np.median(filtered)), 1) if filtered.size > 0 else None


# ---------------------------------------------------------------------------
# Core per-ticker scrape
# ---------------------------------------------------------------------------

def scrape_ticker(ticker: str, exchange: str | None = None) -> dict | None:
    """
    Scrape fullratio.com for a ticker.
    Returns {"ticker", "exchange", "url", "pe_median", "records"} or None.
    """
    ticker = ticker.upper()

    if not exchange:
        exchange = get_exchange(ticker)

    # Build candidate list: detected exchange first, then fallbacks
    candidates = []
    if exchange:
        candidates.append(exchange)
    candidates += [e for e in _EXCHANGE_CANDIDATES if e != exchange]

    html = None
    used_exchange = None
    used_url = None

    for ex in candidates:
        url = build_url(ex, ticker)
        print(f"  Trying {url} …")
        html = _fetch(url)
        if html is not None:
            used_exchange = ex
            used_url = url
            break
        time.sleep(1)

    if html is None:
        print(f"  [WARN] Could not fetch fullratio page for {ticker}; trying yfinance fallback")
        pe_fallback, fallback_source = get_pe_fallback(ticker)
        if pe_fallback is None:
            print(f"  [FAIL] Could not fetch data for {ticker}")
            return None
        print(f"  {ticker} → fallback PE = {pe_fallback} ({fallback_source})")
        return {
            "ticker":    ticker,
            "exchange":  None,
            "url":       None,
            "date":      DATE_STR,
            "pe_median": pe_fallback,
            "summary":   {"fallback_source": fallback_source},
            "records":   [],
            "source":    fallback_source,
        }

    summary = parse_summary_stats(html)
    records = parse_pe_table(html)
    if not records and not summary:
        print(f"  [WARN] No PE data found for {ticker} at {used_url}")
        return None

    pe_median = compute_median(records)
    source = "fullratio.iqr_median"

    # If table parsing fails but summary exists, use the fullratio summary median.
    if pe_median is None:
        median_from_summary = summary.get("Median")
        if isinstance(median_from_summary, (int, float)) and median_from_summary > 0:
            pe_median = round(float(median_from_summary), 1)
            source = "fullratio.summary_median"

    # Final fallback: yfinance trailing/forward PE.
    if pe_median is None:
        pe_fallback, fallback_source = get_pe_fallback(ticker)
        if pe_fallback is not None:
            pe_median = pe_fallback
            source = fallback_source or "yfinance"
            print(f"  {ticker} → fallback PE = {pe_median} ({source})")
    print(f"  {ticker} → {len(records)} PE rows, median = {pe_median}")
    if summary:
        print(f"  Summary: {summary}")

    return {
        "ticker":    ticker,
        "exchange":  used_exchange,
        "url":       used_url,
        "date":      DATE_STR,
        "pe_median": pe_median,
        "summary":   summary,
        "records":   records,
        "source":    source,
    }


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_result(result: dict) -> Path:
    """Save JSON output and persist PE median to the database."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ticker = result["ticker"]

    path = OUTPUT_DIR / f"{ticker}_fullratio_pe_{DATE_STR}.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2)

    if result.get("pe_median"):
        try:
            save_pe_median(ticker, result["pe_median"], date.fromisoformat(DATE_STR))
            print(f"  [DB] Saved PE median {result['pe_median']} for {ticker}")
        except Exception as exc:
            print(f"  [DB] Error saving {ticker}: {exc}")

    return path


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def get_all_tickers() -> list[str]:
    tickers = set()
    for group_tickers in STOCK_LIST.values():
        tickers.update(t.upper() for t in group_tickers)
    return sorted(tickers)


def scrape_and_save(ticker: str, exchange: str | None, delay: tuple) -> tuple:
    time.sleep(random.uniform(*delay))
    result = scrape_ticker(ticker, exchange)
    if result is None:
        return ticker, False
    save_result(result)
    return ticker, True


def scrape_all(workers: int = 1, delay: tuple = (5, 15)) -> None:
    tickers = get_all_tickers()
    print(f"Scraping {len(tickers)} tickers from fullratio.com (workers={workers})")
    print("-" * 60)

    succeeded, failed = [], []

    if workers <= 1:
        for i, t in enumerate(tickers, 1):
            print(f"[{i}/{len(tickers)}] {t}")
            _, ok = scrape_and_save(t, None, delay)
            (succeeded if ok else failed).append(t)
    else:
        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for t in tickers:
                futures[pool.submit(scrape_and_save, t, None, delay)] = t
            done = 0
            for fut in as_completed(futures):
                done += 1
                t = futures[fut]
                try:
                    _, ok = fut.result()
                    print(f"[{done}/{len(tickers)}] {t} — {'OK' if ok else 'FAIL'}")
                    (succeeded if ok else failed).append(t)
                except Exception as exc:
                    print(f"[{done}/{len(tickers)}] {t} — ERROR: {exc}")
                    failed.append(t)

    print("-" * 60)
    print(f"Done. {len(succeeded)} OK, {len(failed)} failed.")
    if failed:
        print(f"Failed: {', '.join(failed)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape PE ratio history from fullratio.com")
    parser.add_argument("--ticker",    type=str, help="Single ticker (e.g. ADI)")
    parser.add_argument("--exchange",  type=str, help="Exchange prefix: nasdaq|nyse (auto-detected if omitted)")
    parser.add_argument("--all",       action="store_true", help="Scrape all tickers in STOCK_LIST")
    parser.add_argument("--workers",   type=int,   default=1,  help="Parallel workers (default 1)")
    parser.add_argument("--delay-min", type=float, default=5,  help="Min delay between requests in seconds")
    parser.add_argument("--delay-max", type=float, default=15, help="Max delay between requests in seconds")
    args = parser.parse_args()

    if not args.all and not args.ticker:
        parser.error("Provide --ticker TICKER or --all")

    delay = (args.delay_min, args.delay_max)

    if args.all:
        scrape_all(workers=args.workers, delay=delay)
    else:
        ticker = args.ticker.upper()
        print(f"Scraping {ticker} from fullratio.com …")
        result = scrape_ticker(ticker, exchange=args.exchange)
        if result is None:
            print("Failed — no data returned.")
            sys.exit(1)

        path = save_result(result)
        print(f"\nSaved to {path}")

        if result.get("summary"):
            print("\nSummary stats:")
            for label, val in result["summary"].items():
                print(f"  {label:<15s} {val}")

        print(f"\nPE Median (IQR-filtered, last 20 quarters): {result['pe_median']}")

        if result.get("records"):
            print(f"\nHistorical rows (newest first):")
            for row in result["records"][:10]:
                print(f"  {row['date']:15s}  PE = {row['pe']}")


if __name__ == "__main__":
    main()
