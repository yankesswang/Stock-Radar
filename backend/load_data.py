"""
Data loader: imports JSON data files from data/ into SQLite database.
Usage: python -m backend.load_data [--force]
"""
import json
import glob
import os
import re
import sys
from datetime import date
from pathlib import Path

# Add project root to path so we can import src.config
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import settings
from backend.app.database import engine, SessionLocal, Base
from backend.app.models import (
    Industry, Stock, Metric, Forecast, PeMedian, Valuation, DataSnapshot,
)
from src.config import STOCK_LIST, PE_TICKER_TO_COMPANY

# --- Field mapping from JSON keys to DB column names ---
RATIO_KEY_MAP = {
    "marketcap": "market_cap",
    "enterpriseValue": "enterprise_value",
    "currentPrice": "current_price",
    "pe": "pe",
    "peForward": "pe_forward",
    "ps": "ps",
    "psForward": "ps_forward",
    "pb": "pb",
    "pegRatio": "peg_ratio",
    "evEarnings": "ev_earnings",
    "evSales": "ev_sales",
    "evEbitda": "ev_ebitda",
    "evEbit": "ev_ebit",
    "evFcf": "ev_fcf",
    "currentRatio": "current_ratio",
    "quickRatio": "quick_ratio",
    "debtEquity": "debt_equity",
    "debtEbitda": "debt_ebitda",
    "debtFcf": "debt_fcf",
    "interestCoverage": "interest_coverage",
    "grossMargin": "gross_margin",
    "operatingMargin": "operating_margin",
    "pretaxMargin": "pretax_margin",
    "profitMargin": "profit_margin",
    "ebitdaMargin": "ebitda_margin",
    "ebitMargin": "ebit_margin",
    "fcfMargin": "fcf_margin",
    "roe": "roe",
    "roa": "roa",
    "roic": "roic",
    "roce": "roce",
    "revenue": "revenue",
    "gp": "gross_profit",
    "opinc": "operating_income",
    "pretax": "pretax_income",
    "netinc": "net_income",
    "ebitda": "ebitda",
    "ebit": "ebit",
    "eps": "eps",
    "totalcash": "total_cash",
    "debt": "debt",
    "netcash": "net_cash",
    "netcashpershare": "net_cash_per_share",
    "equity": "equity",
    "bvps": "bvps",
    "workingcapital": "working_capital",
    "ncfo": "ncfo",
    "capex": "capex",
    "fcf": "fcf",
    "fcfps": "fcf_per_share",
    "dividendYield": "dividend_yield",
    "dividendGrowth": "dividend_growth",
    "dividendGrowthYears": "dividend_growth_years",
    "payoutRatio": "payout_ratio",
    "buybackYield": "buyback_yield",
    "earningsYield": "earnings_yield",
    "fcfYield": "fcf_yield",
    "priceTarget": "price_target",
    "analystCount": "analyst_count",
    "revenue5y": "revenue_5y",
    "eps5y": "eps_5y",
    "sharesout": "shares_out",
    "sharesgrowthyoy": "shares_growth_yoy",
    "float": "float_shares",
    "beta": "beta",
    "ch1y": "change_1y",
    "sma50": "sma_50",
    "sma200": "sma_200",
    "rsi": "rsi",
    "shortInterest": "short_interest",
    "shortFloat": "short_float",
    "shortRatio": "short_ratio",
}

# All valid DB columns for the Metric model
METRIC_COLUMNS = set(RATIO_KEY_MAP.values())


def extract_date_from_filename(filename: str) -> date | None:
    """Extract YYYY-MM-DD date from a filename."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if m:
        return date.fromisoformat(m.group(1))
    return None


def compute_valuation(
    forecast_annual: dict,
    ratio_data: dict,
    pe_median: float | None,
    fiscal_year: int = 2025,
) -> dict:
    """
    Replicate the valuation logic from Valuation_Analyzer_Pure.calculate_valuations()
    and process_company().
    """
    yr = str(fiscal_year)[-2:]  # e.g. "25"
    yr_next = str(fiscal_year + 1)[-2:]  # e.g. "26"

    # Growth rate: prefer eps_5y from ratio, fallback to current_growth from forecast
    eps_5y = ratio_data.get("eps5y")
    past_growth = forecast_annual.get("current_growth")
    growth = eps_5y or past_growth or 0
    if growth is None:
        growth = 0

    # Estimated PE = growth * multiplier
    estimated_pe = growth
    if 0 < growth < 5:
        estimated_pe *= 0.8
    elif 5 <= growth < 10:
        estimated_pe *= 1.0
    elif 10 <= growth < 15:
        estimated_pe *= 1.1
    elif 15 <= growth < 20:
        estimated_pe *= 1.2
    elif 20 <= growth < 30:
        estimated_pe *= 1.5
    else:
        estimated_pe *= 2.0

    estimated_pe = round(estimated_pe, 2) if estimated_pe else 0.0

    # EPS
    eps_current = forecast_annual.get("current_eps") or 0.0
    eps_next = forecast_annual.get("next_year_eps") or 0.0

    # Fair prices
    current_year_fair = round(eps_current * estimated_pe) if eps_current else 0
    next_year_fair = round(eps_next * estimated_pe) if eps_next else 0

    # PE median calculations
    pe_med = pe_median or 0
    pe_median_price = round(pe_med * eps_current) if eps_current else 0
    next_year_pe_median_price = round(pe_med * eps_next) if eps_next else 0

    # Current price
    current_price = ratio_data.get("currentPrice") or 0

    # Market cap display
    mc = ratio_data.get("marketcap")
    if mc:
        if mc >= 1e12:
            market_cap_display = f"{round(mc / 1e12, 2)}T"
        else:
            market_cap_display = f"{round(mc / 1e9, 2)}B"
    else:
        market_cap_display = "N/A"

    # Valuation status (current year)
    valuation_status = "N/A"
    valuation_diff_pct = None
    if current_price and pe_median_price:
        valuation_status = "overvalued" if current_price > pe_median_price else "undervalued"
        valuation_diff_pct = round(
            (pe_median_price - current_price) / pe_median_price * 100, 1
        )

    # Valuation status (next year)
    next_year_valuation_status = "N/A"
    next_year_valuation_diff_pct = None
    if current_price and next_year_pe_median_price:
        next_year_valuation_status = (
            "overvalued" if current_price > next_year_pe_median_price else "undervalued"
        )
        next_year_valuation_diff_pct = round(
            (next_year_pe_median_price - current_price)
            / next_year_pe_median_price
            * 100,
            1,
        )

    return {
        "estimated_pe": estimated_pe,
        "current_year_eps": eps_current,
        "current_year_fair_price": current_year_fair,
        "next_year_eps": eps_next,
        "next_year_fair_price": next_year_fair,
        "pe_median_5y": pe_med,
        "pe_median_price": pe_median_price,
        "next_year_pe_median_price": next_year_pe_median_price,
        "current_price": current_price,
        "market_cap_display": market_cap_display,
        "valuation_status": valuation_status,
        "valuation_diff_pct": valuation_diff_pct,
        "next_year_valuation_status": next_year_valuation_status,
        "next_year_valuation_diff_pct": next_year_valuation_diff_pct,
        "revenue_growth_5y": ratio_data.get("revenue5y"),
        "eps_growth_5y": eps_5y,
        "eps_growth_past": past_growth,
    }


def load_all(force: bool = False):
    """Main loader: read JSON files and populate SQLite."""
    # Create tables
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        if force:
            print("Force mode: dropping all data...")
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(table.delete())
            db.commit()

        # --- Step 1: Load industries + stocks ---
        print("Loading industries and stocks from config...")

        # Build ticker -> primary industry mapping from ratio data first
        # (use the industry field in the ratio JSON as canonical)
        latest_ratio_files = sorted(
            glob.glob(str(settings.DATA_DIR / "ratio" / "stock_list_metrics_*.json")),
            key=os.path.getmtime,
        )
        ticker_industry_map = {}
        if latest_ratio_files:
            with open(latest_ratio_files[-1], "r") as f:
                latest_ratio = json.load(f)
            for ticker, data in latest_ratio.items():
                ind = data.get("industry")
                if ind:
                    ticker_industry_map[ticker] = ind

        # Also use STOCK_LIST as fallback
        for industry_name, tickers in STOCK_LIST.items():
            for t in tickers:
                if t not in ticker_industry_map:
                    ticker_industry_map[t] = industry_name

        # Create industries
        all_industry_names = set(ticker_industry_map.values())
        industry_objects = {}
        for name in all_industry_names:
            existing = db.query(Industry).filter_by(name=name).first()
            if not existing:
                existing = Industry(name=name)
                db.add(existing)
                db.flush()
            industry_objects[name] = existing

        # Create stocks
        # Build company name lookup from PE_TICKER_TO_COMPANY
        company_names = {}
        for ticker_lower, slug in PE_TICKER_TO_COMPANY.items():
            display = slug.replace("-", " ").title()
            company_names[ticker_lower.upper()] = display

        stock_objects = {}
        all_tickers = set(ticker_industry_map.keys())
        for ticker in all_tickers:
            existing = db.query(Stock).filter_by(ticker=ticker).first()
            if not existing:
                ind_name = ticker_industry_map[ticker]
                existing = Stock(
                    ticker=ticker,
                    company_name=company_names.get(ticker),
                    industry_id=industry_objects[ind_name].id,
                )
                db.add(existing)
                db.flush()
            stock_objects[ticker] = existing

        db.commit()
        print(f"  {len(industry_objects)} industries, {len(stock_objects)} stocks loaded")

        # --- Step 2: Load PE data files ---
        pe_files = sorted(glob.glob(str(settings.DATA_DIR / "pe" / "stock_list_PE_*.json")))
        for filepath in pe_files:
            fname = os.path.basename(filepath)
            # Check if already loaded
            if not force and db.query(DataSnapshot).filter_by(file_name=fname).first():
                print(f"  Skipping (already loaded): {fname}")
                continue

            snap_date = extract_date_from_filename(fname)
            if not snap_date:
                continue

            print(f"  Loading PE data: {fname}")
            with open(filepath, "r") as f:
                pe_data = json.load(f)

            count = 0
            for ticker, pe_val in pe_data.items():
                stock = stock_objects.get(ticker)
                if not stock:
                    continue
                # Check for existing
                existing = (
                    db.query(PeMedian)
                    .filter_by(stock_id=stock.id, snapshot_date=snap_date)
                    .first()
                )
                if existing:
                    existing.pe_median = pe_val
                else:
                    db.add(PeMedian(stock_id=stock.id, snapshot_date=snap_date, pe_median=pe_val))
                count += 1

            db.add(DataSnapshot(data_type="pe", file_name=fname, snapshot_date=snap_date, stock_count=count))
            db.commit()
            print(f"    {count} PE records loaded")

        # --- Step 3: Load ratio/metrics data files ---
        ratio_files = sorted(glob.glob(str(settings.DATA_DIR / "ratio" / "stock_list_metrics_*.json")))
        for filepath in ratio_files:
            fname = os.path.basename(filepath)
            if not force and db.query(DataSnapshot).filter_by(file_name=fname).first():
                print(f"  Skipping (already loaded): {fname}")
                continue

            snap_date = extract_date_from_filename(fname)
            if not snap_date:
                continue

            print(f"  Loading ratio data: {fname}")
            with open(filepath, "r") as f:
                ratio_data = json.load(f)

            count = 0
            for ticker, data in ratio_data.items():
                stock = stock_objects.get(ticker)
                if not stock:
                    continue

                # Map JSON keys to DB columns
                metric_kwargs = {"stock_id": stock.id, "snapshot_date": snap_date}
                for json_key, db_col in RATIO_KEY_MAP.items():
                    val = data.get(json_key)
                    if val is not None and not isinstance(val, str):
                        metric_kwargs[db_col] = val

                existing = (
                    db.query(Metric)
                    .filter_by(stock_id=stock.id, snapshot_date=snap_date)
                    .first()
                )
                if existing:
                    for k, v in metric_kwargs.items():
                        if k not in ("stock_id", "snapshot_date"):
                            setattr(existing, k, v)
                else:
                    db.add(Metric(**metric_kwargs))
                count += 1

            db.add(DataSnapshot(data_type="ratio", file_name=fname, snapshot_date=snap_date, stock_count=count))
            db.commit()
            print(f"    {count} metric records loaded")

        # --- Step 4: Load forecast data files ---
        forecast_files = sorted(glob.glob(str(settings.DATA_DIR / "forecast" / "stock_list_forecasts_*.json")))
        for filepath in forecast_files:
            fname = os.path.basename(filepath)
            if not force and db.query(DataSnapshot).filter_by(file_name=fname).first():
                print(f"  Skipping (already loaded): {fname}")
                continue

            snap_date = extract_date_from_filename(fname)
            if not snap_date:
                continue

            print(f"  Loading forecast data: {fname}")
            with open(filepath, "r") as f:
                forecast_data = json.load(f)

            count = 0
            for ticker, data in forecast_data.items():
                stock = stock_objects.get(ticker)
                if not stock:
                    continue

                annual = data.get("annual", {})
                forecast_kwargs = {
                    "stock_id": stock.id,
                    "snapshot_date": snap_date,
                    "current_eps": annual.get("current_eps"),
                    "current_eps_growth": annual.get("current_growth"),
                    "next_year_eps": annual.get("next_year_eps"),
                    "next_year_eps_growth": annual.get("next_year_growth"),
                    "current_revenue": annual.get("current_revenue"),
                    "current_revenue_growth": annual.get("current_revenue_growth"),
                    "next_year_revenue": annual.get("next_year_revenue"),
                    "next_year_revenue_growth": annual.get("next_year_revenue_growth"),
                }

                existing = (
                    db.query(Forecast)
                    .filter_by(stock_id=stock.id, snapshot_date=snap_date)
                    .first()
                )
                if existing:
                    for k, v in forecast_kwargs.items():
                        if k not in ("stock_id", "snapshot_date"):
                            setattr(existing, k, v)
                else:
                    db.add(Forecast(**forecast_kwargs))
                count += 1

            db.add(DataSnapshot(data_type="forecast", file_name=fname, snapshot_date=snap_date, stock_count=count))
            db.commit()
            print(f"    {count} forecast records loaded")

        # --- Step 5: Compute valuations ---
        print("\nComputing valuations...")
        fiscal_year = settings.CURRENT_FISCAL_YEAR

        # For each ratio snapshot, compute valuations
        ratio_snapshots = (
            db.query(DataSnapshot)
            .filter_by(data_type="ratio")
            .order_by(DataSnapshot.snapshot_date)
            .all()
        )

        for snap in ratio_snapshots:
            snap_date = snap.snapshot_date
            print(f"  Computing valuations for {snap_date}...")

            # Get all stocks with metrics for this date
            metrics_list = db.query(Metric).filter_by(snapshot_date=snap_date).all()

            # Get the closest PE data (at or before this date)
            for metric in metrics_list:
                stock = db.query(Stock).get(metric.stock_id)
                if not stock:
                    continue

                # Get forecast for this date (or closest)
                forecast = (
                    db.query(Forecast)
                    .filter(Forecast.stock_id == stock.id, Forecast.snapshot_date <= snap_date)
                    .order_by(Forecast.snapshot_date.desc())
                    .first()
                )

                # Get PE median (closest at or before)
                pe_rec = (
                    db.query(PeMedian)
                    .filter(PeMedian.stock_id == stock.id, PeMedian.snapshot_date <= snap_date)
                    .order_by(PeMedian.snapshot_date.desc())
                    .first()
                )

                if not forecast:
                    continue

                # Build ratio dict for the compute function
                ratio_dict = {
                    "eps5y": metric.eps_5y,
                    "revenue5y": metric.revenue_5y,
                    "currentPrice": metric.current_price,
                    "marketcap": metric.market_cap,
                }

                forecast_annual = {
                    "current_eps": forecast.current_eps,
                    "current_growth": forecast.current_eps_growth,
                    "next_year_eps": forecast.next_year_eps,
                    "next_year_growth": forecast.next_year_eps_growth,
                }

                pe_median_val = pe_rec.pe_median if pe_rec else None

                val = compute_valuation(forecast_annual, ratio_dict, pe_median_val, fiscal_year)

                existing = (
                    db.query(Valuation)
                    .filter_by(stock_id=stock.id, snapshot_date=snap_date)
                    .first()
                )
                if existing:
                    for k, v in val.items():
                        setattr(existing, k, v)
                    existing.fiscal_year = fiscal_year
                else:
                    db.add(
                        Valuation(
                            stock_id=stock.id,
                            snapshot_date=snap_date,
                            fiscal_year=fiscal_year,
                            **val,
                        )
                    )

            db.commit()

        # Summary
        total_stocks = db.query(Stock).count()
        total_valuations = db.query(Valuation).count()
        total_snapshots = db.query(DataSnapshot).count()
        print(f"\nDone! {total_stocks} stocks, {total_valuations} valuations, {total_snapshots} data snapshots.")

    finally:
        db.close()


if __name__ == "__main__":
    force = "--force" in sys.argv
    load_all(force=force)
