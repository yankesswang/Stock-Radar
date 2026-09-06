"""
Serve the scraped metrics-breakdown CSV data (revenue by segment,
gross profit by type, etc.) for a given ticker.

CSV files live at  data/metrics/{TICKER}_metrics_{date}.csv
We always return the most recent file available.
"""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter()

METRICS_DIR: Path = settings.DATA_DIR / "metrics"

# 分部門／分地區／分類型的分類改由 SEC 申報提供（/api/segments），這裡不再重複。
# 兩邊都有分部門的 22 檔實測：這份 CSV 一律只有 8 期，SEC 中位數 37 期，
# 而且 CSV 的 8 期排在期間軸第 18 欄之後，預設視窗根本看不到。
# 留下來的是 SEC 拿不到的：費用拆解、各家自訂的營運指標（活躍用戶、ARPU、
# Take Rate…）——121/149 檔有這類資料，整段砍掉會連它們一起弄丟。
SEGMENT_LIKE = re.compile(r"(by segment|by geography|revenue geography|by type)", re.I)


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

    # 丟掉「整欄沒有任何一個指標有值」的期間。
    #
    # 來源 CSV 的欄位是年度在前、季度在後，而多數分項（分部門營收、分類毛利）
    # 只有季度值——於是前十幾欄全是空的。頁面從第一欄開始顯示，使用者看到的就是
    # 一整片空白，會以為資料沒抓到（AAPL 的 Revenue by Segment 有值的欄位排在
    # 第 18 欄之後）。全庫實測：4,430 個期間欄裡有 2,932 欄（66%）整欄全空，
    # 149 檔裡 145 檔受影響，不是個案。
    # 過濾要逐「分類」做，不能只做一次全域的：那些年度欄對別的分類（營運指標、
    # 費用拆解）是有值的，只有分部門那類沒有。做成全域過濾的話，分部門依然
    # 開頭六欄空白。所以每個分類帶自己的期間軸。
    def _trim(metrics: list[dict]) -> tuple[list[str], list[dict]]:
        keep = [i for i in range(len(periods))
                if any(m["values"][i] is not None for m in metrics)]
        if not keep or len(keep) == len(periods):
            return periods, metrics
        return ([periods[i] for i in keep],
                [{**m, "values": [m["values"][i] for i in keep]} for m in metrics])

    trimmed = {}
    for cat_name, metrics in categories.items():
        if SEGMENT_LIKE.search(cat_name):
            continue                       # 分部門統一走 SEC，見上方說明
        trimmed[cat_name] = _trim(metrics)

    # Extract date from filename: AAPL_metrics_2026-02-01.csv
    date_str = path.stem.rsplit("_", 1)[-1]

    return {
        "ticker": path.stem.split("_")[0].upper(),
        "date": date_str,
        "periods": periods,
        "categories": [
            # periods 是這個分類自己的期間軸；前端有給就用它，沒有才回頭用全域的
            {"name": cat_name, "periods": cat_periods, "metrics": cat_metrics}
            for cat_name, (cat_periods, cat_metrics) in trimmed.items()
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
