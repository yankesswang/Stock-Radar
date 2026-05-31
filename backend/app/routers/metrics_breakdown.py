"""
Serve the scraped metrics-breakdown CSV data (revenue by segment,
gross profit by type, etc.) for a given ticker.

CSV files live at  data/metrics/{TICKER}_metrics_{date}.csv
We always return the most recent file available.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter()

METRICS_DIR: Path = settings.DATA_DIR / "metrics"


def _find_latest_csv(ticker: str) -> Path | None:
    """Return the newest *_metrics_*.csv file for *ticker* (case-insensitive)."""
    pattern = f"{ticker.upper()}_metrics_*.csv"
    files = sorted(METRICS_DIR.glob(pattern), reverse=True)
    if files:
        return files[0]
    # Try lowercase fallback
    pattern_lower = f"{ticker.lower()}_metrics_*.csv"
    files = sorted(METRICS_DIR.glob(pattern_lower), reverse=True)
    return files[0] if files else None


def _parse_csv(path: Path) -> dict:
    """Read a metrics CSV and return structured JSON.

    Returns::
        {
            "ticker": "AAPL",
            "date": "2026-02-01",
            "periods": ["Dec 31, 2025", "Sep 30, 2025", ...],
            "categories": [
                {
                    "name": "Apple Revenue by Segment",
                    "metrics": [
                        {"name": "iPhone", "values": [225720000000, ...]},
                        ...
                    ]
                },
                ...
            ]
        }
    """
    import csv

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)

    # header: ["Category", "Metric", period1, period2, ...]
    periods = header[2:]

    categories: dict[str, list[dict]] = {}

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cat = row.get("Category") or "Other"
            metric_name = row.get("Metric", "")

            values = []
            for p in periods:
                raw = row.get(p, "")
                if raw == "" or raw is None:
                    values.append(None)
                else:
                    try:
                        values.append(float(raw))
                    except ValueError:
                        values.append(None)

            if cat not in categories:
                categories[cat] = []
            categories[cat].append({"name": metric_name, "values": values})

    # Extract date from filename: AAPL_metrics_2026-02-01.csv
    date_str = path.stem.rsplit("_", 1)[-1]

    return {
        "ticker": path.stem.split("_")[0].upper(),
        "date": date_str,
        "periods": periods,
        "categories": [
            {"name": cat_name, "metrics": metrics}
            for cat_name, metrics in categories.items()
        ],
    }


@router.get("/{ticker}/metrics-breakdown")
def get_metrics_breakdown(ticker: str):
    csv_path = _find_latest_csv(ticker)
    if not csv_path:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No metrics breakdown data found for {ticker.upper()}. "
                f"This data comes from stockanalysis.com/stocks/{ticker.lower()}/metrics/; "
                "some tickers do not have that page even when ratios, statistics, "
                "forecasts, and valuations are available."
            ),
        )
    return _parse_csv(csv_path)
