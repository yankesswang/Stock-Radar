"""
Serve the scraped financial-ratios CSV data for a given ticker.

CSV files live at  data/financials/{TICKER}_ratios_{date}.csv
We always return the most recent file available.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter()

FINANCIALS_DIR: Path = settings.DATA_DIR / "financials"


def _find_latest_csv(ticker: str) -> Path | None:
    """Return the newest *_ratios_*.csv file for *ticker*."""
    pattern = f"{ticker.upper()}_ratios_*.csv"
    files = sorted(FINANCIALS_DIR.glob(pattern), reverse=True)
    if files:
        return files[0]
    pattern_lower = f"{ticker.lower()}_ratios_*.csv"
    files = sorted(FINANCIALS_DIR.glob(pattern_lower), reverse=True)
    return files[0] if files else None


def _parse_csv(path: Path) -> dict:
    """Read a ratios CSV and return structured JSON.

    Returns::
        {
            "ticker": "AAPL",
            "date": "2026-02-01",
            "periods": ["Current", "FY 2025", "FY 2024", ...],
            "sections": [
                {
                    "name": "Valuation",
                    "metrics": [
                        {"name": "PE Ratio", "values": [32.91, 33.85, ...]},
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

    # header: ["Section", "Metric", period1, period2, ...]
    periods = header[2:]

    sections: dict[str, list[dict]] = {}

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            section = row.get("Section") or "General"
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

            if section not in sections:
                sections[section] = []
            sections[section].append({"name": metric_name, "values": values})

    # Extract date from filename: AAPL_ratios_2026-02-01.csv
    date_str = path.stem.rsplit("_", 1)[-1]

    return {
        "ticker": path.stem.split("_")[0].upper(),
        "date": date_str,
        "periods": periods,
        "sections": [
            {"name": name, "metrics": metrics}
            for name, metrics in sections.items()
        ],
    }


@router.get("/{ticker}/ratios")
def get_ratios(ticker: str):
    csv_path = _find_latest_csv(ticker)
    if not csv_path:
        raise HTTPException(
            status_code=404,
            detail=f"No ratios data found for {ticker.upper()}. Run: python3 scripts/scrape_ratios.py --ticker {ticker.upper()}",
        )
    return _parse_csv(csv_path)
