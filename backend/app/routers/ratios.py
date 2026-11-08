"""
Serve the scraped financial-ratios CSV data for a given ticker.

CSV files live at  data/financials/{TICKER}_ratios_{date}.csv
We always return the most recent file available.
"""

import csv
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from ..config import settings
from ..database import SessionLocal

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


# ── 獲利能力 ────────────────────────────────────────────────────────────────
# stockanalysis 的 ratios 頁沒有利潤率（它們在損益表頁），所以這一區另外組，
# 期間對齊 ratios 的欄（"Current"、"FY 2025"…）。每一格依序取：
#   1. 損益表 CSV {T}_income_*.csv（同一個網站，數字口徑一致；statements 排程每天補一批）
#   2. SEC 推算的 financial_derived（只有會計年度欄；比例存小數要 ×100）
#   3. "Current" 還空著就拿 statistics 的 TTM 值（幾乎每檔都有）
PROFIT_SECTION = "Profitability"
PROFIT_METRICS = [
    # (顯示名稱, 損益表 CSV 的 Metric, financial_derived 欄, statistics metric_id)
    ("Gross Margin",     "Gross Margin",     "gross_margin",     "grossMargin"),
    ("Operating Margin", "Operating Margin", "operating_margin", "operatingMargin"),
    ("EBITDA Margin",    "EBITDA Margin",    None,               "ebitdaMargin"),
    ("Pretax Margin",    "Pretax Margin",    "pretax_margin",    "pretaxMargin"),
    ("Profit Margin",    "Profit Margin",    "profit_margin",    "profitMargin"),
    ("FCF Margin",       "FCF Margin",       "fcf_margin",       "fcfMargin"),
]
_FY_RE = re.compile(r"^FY (\d{4})$")


def _income_margins(ticker: str) -> dict[str, dict[str, float]]:
    """{period label → {metric → value%}} from the newest income CSV; TTM is keyed as 'Current'."""
    files = sorted(FINANCIALS_DIR.glob(f"{ticker.upper()}_income_*.csv"), reverse=True)
    if not files:
        return {}
    wanted = {m[1] for m in PROFIT_METRICS}
    out: dict[str, dict[str, float]] = {}
    with open(files[0], newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row.get("Metric")
            if name not in wanted:
                continue
            for period, raw in row.items():
                if period in ("Section", "Metric") or raw in ("", None):
                    continue
                try:
                    val = float(raw)
                except ValueError:
                    continue
                label = "Current" if period == "TTM" else period
                out.setdefault(label, {})[name] = val
    return out


def _fy_label(period_end: str) -> str:
    # 52/53 週制的年度常常結束在一月第一週（算上一個會計年度），其餘照結束那年
    d = date.fromisoformat(period_end)
    year = d.year - 1 if d.month == 1 and d.day <= 7 else d.year
    return f"FY {year}"


def _sec_margins(db, ticker: str) -> dict[str, dict[str, float]]:
    cols = [m[2] for m in PROFIT_METRICS if m[2]]
    rows = db.execute(
        text(f"SELECT period_end, {', '.join(cols)} FROM financial_derived "
             "WHERE ticker = :t AND is_fiscal_year = 1 AND period_months = 12"),
        {"t": ticker.upper()},
    ).mappings().all()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        vals = {name: round(r[col] * 100, 2)
                for name, _, col, _ in PROFIT_METRICS if col and r[col] is not None}
        if vals:
            out[_fy_label(r["period_end"])] = vals
    return out


def _stats_margins(db, ticker: str) -> dict[str, float]:
    ids = [m[3] for m in PROFIT_METRICS]
    rows = db.execute(
        text("SELECT s.metric_id, s.value FROM statistics s JOIN stocks t ON t.id = s.stock_id "
             "WHERE t.ticker = :t AND s.value IS NOT NULL "
             f"AND s.metric_id IN ({', '.join(':m%d' % i for i in range(len(ids)))}) "
             "AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM statistics WHERE stock_id = t.id)"),
        {"t": ticker.upper(), **{f"m{i}": v for i, v in enumerate(ids)}},
    ).all()
    by_id = dict(rows)
    return {name: round(by_id[sid], 2) for name, _, _, sid in PROFIT_METRICS if sid in by_id}


def _profitability_section(ticker: str, periods: list[str]) -> dict | None:
    income = _income_margins(ticker)
    db = SessionLocal()
    try:
        sec = _sec_margins(db, ticker)
        stats = _stats_margins(db, ticker)
    finally:
        db.close()

    metrics = []
    for name, *_ in PROFIT_METRICS:
        values = []
        for p in periods:
            v = income.get(p, {}).get(name)
            if v is None and _FY_RE.match(p):
                v = sec.get(p, {}).get(name)
            if v is None and p == "Current":
                v = stats.get(name)
            values.append(v)
        if any(v is not None for v in values):
            metrics.append({"name": name, "values": values})
    return {"name": PROFIT_SECTION, "metrics": metrics} if metrics else None


@router.get("/{ticker}/ratios")
def get_ratios(ticker: str):
    csv_path = _find_latest_csv(ticker)
    if not csv_path:
        raise HTTPException(
            status_code=404,
            detail=f"No ratios data found for {ticker.upper()}. Run: python3 scripts/scrape_ratios.py --ticker {ticker.upper()}",
        )
    data = _parse_csv(csv_path)
    try:
        profit = _profitability_section(ticker, data["periods"])
    except Exception as exc:  # 補充區塊壞掉不該拖垮整頁
        print(f"[ratios] profitability section failed for {ticker}: {exc}")
        profit = None
    if profit:
        # 放在估值類之後、Returns 之前，獲利能力跟報酬率排在一起
        names = [s["name"] for s in data["sections"]]
        at = names.index("Returns") if "Returns" in names else len(names)
        data["sections"].insert(at, profit)
    return data
