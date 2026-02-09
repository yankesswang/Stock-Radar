"""
Database service for scrapers.
Provides functions to save scraped data directly to SQLite database.
"""
import sys
from datetime import date
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import SessionLocal, engine, Base
from backend.app.models import (
    Industry, Stock, Metric, Forecast, PeMedian, Valuation, DataSnapshot,
    Statistic, MetricsBreakdown,
)
from backend.app.config import settings
from src.config import PE_TICKER_TO_COMPANY

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


def ensure_tables():
    """Ensure all database tables exist."""
    Base.metadata.create_all(bind=engine)


def get_or_create_industry(db, industry_name: str) -> Industry:
    """Get existing industry or create new one."""
    industry = db.query(Industry).filter_by(name=industry_name).first()
    if not industry:
        industry = Industry(name=industry_name)
        db.add(industry)
        db.flush()
    return industry


def get_or_create_stock(db, ticker: str, industry_name: str) -> Stock:
    """Get existing stock or create new one."""
    stock = db.query(Stock).filter_by(ticker=ticker).first()
    if not stock:
        industry = get_or_create_industry(db, industry_name)
        # Get company name from mapping
        ticker_lower = ticker.lower()
        company_name = None
        if ticker_lower in PE_TICKER_TO_COMPANY:
            slug = PE_TICKER_TO_COMPANY[ticker_lower]
            company_name = slug.replace("-", " ").title()
        
        stock = Stock(
            ticker=ticker,
            company_name=company_name,
            industry_id=industry.id,
        )
        db.add(stock)
        db.flush()
    return stock


def save_metrics(ticker: str, industry: str, metrics_dict: dict, snapshot_date: date):
    """
    Save ratio/metrics data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        industry: Industry name
        metrics_dict: Dictionary of scraped metrics (JSON keys)
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = get_or_create_stock(db, ticker, industry)
        
        # Map JSON keys to DB columns
        metric_kwargs = {"stock_id": stock.id, "snapshot_date": snapshot_date}
        for json_key, db_col in RATIO_KEY_MAP.items():
            val = metrics_dict.get(json_key)
            if val is not None and not isinstance(val, str):
                metric_kwargs[db_col] = val
        
        # Check if already exists
        existing = (
            db.query(Metric)
            .filter_by(stock_id=stock.id, snapshot_date=snapshot_date)
            .first()
        )
        if existing:
            for k, v in metric_kwargs.items():
                if k not in ("stock_id", "snapshot_date"):
                    setattr(existing, k, v)
        else:
            db.add(Metric(**metric_kwargs))
        
        db.commit()
        print(f"  [DB] Saved metrics for {ticker}")
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving metrics for {ticker}: {e}")
    finally:
        db.close()


def save_forecast(ticker: str, forecast_dict: dict, snapshot_date: date):
    """
    Save forecast data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        forecast_dict: Dictionary with 'annual' key containing forecast data
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter_by(ticker=ticker).first()
        if not stock:
            print(f"  [DB] Stock {ticker} not found, skipping forecast save")
            return
        
        annual = forecast_dict.get("annual", {})
        forecast_kwargs = {
            "stock_id": stock.id,
            "snapshot_date": snapshot_date,
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
            .filter_by(stock_id=stock.id, snapshot_date=snapshot_date)
            .first()
        )
        if existing:
            for k, v in forecast_kwargs.items():
                if k not in ("stock_id", "snapshot_date"):
                    setattr(existing, k, v)
        else:
            db.add(Forecast(**forecast_kwargs))
        
        db.commit()
        print(f"  [DB] Saved forecast for {ticker}")
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving forecast for {ticker}: {e}")
    finally:
        db.close()


def save_pe_median(ticker: str, pe_median: float, snapshot_date: date):
    """
    Save PE median data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        pe_median: 5-year median PE ratio
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter_by(ticker=ticker).first()
        if not stock:
            print(f"  [DB] Stock {ticker} not found, skipping PE save")
            return
        
        existing = (
            db.query(PeMedian)
            .filter_by(stock_id=stock.id, snapshot_date=snapshot_date)
            .first()
        )
        if existing:
            existing.pe_median = pe_median
        else:
            db.add(PeMedian(stock_id=stock.id, snapshot_date=snapshot_date, pe_median=pe_median))
        
        db.commit()
        print(f"  [DB] Saved PE median for {ticker}")
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving PE median for {ticker}: {e}")
    finally:
        db.close()


def compute_valuation(
    forecast_annual: dict,
    ratio_data: dict,
    pe_median: float | None,
    fiscal_year: int = 2025,
) -> dict:
    """
    Compute valuation metrics based on forecast, ratio, and PE data.
    Replicates logic from Valuation_Analyzer_Pure.
    """
    eps_5y = ratio_data.get("eps_5y")
    past_growth = forecast_annual.get("current_growth")
    growth = eps_5y or past_growth or 0
    if growth is None:
        growth = 0

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

    eps_current = forecast_annual.get("current_eps") or 0.0
    eps_next = forecast_annual.get("next_year_eps") or 0.0

    current_year_fair = round(eps_current * estimated_pe) if eps_current else 0
    next_year_fair = round(eps_next * estimated_pe) if eps_next else 0

    pe_med = pe_median or 0
    pe_median_price = round(pe_med * eps_current) if eps_current else 0
    next_year_pe_median_price = round(pe_med * eps_next) if eps_next else 0

    current_price = ratio_data.get("current_price") or 0

    mc = ratio_data.get("market_cap")
    if mc:
        if mc >= 1e12:
            market_cap_display = f"{round(mc / 1e12, 2)}T"
        else:
            market_cap_display = f"{round(mc / 1e9, 2)}B"
    else:
        market_cap_display = "N/A"

    valuation_status = "N/A"
    valuation_diff_pct = None
    if current_price and pe_median_price:
        valuation_status = "overvalued" if current_price > pe_median_price else "undervalued"
        valuation_diff_pct = round(
            (pe_median_price - current_price) / pe_median_price * 100, 1
        )

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
        "revenue_growth_5y": ratio_data.get("revenue_5y"),
        "eps_growth_5y": eps_5y,
        "eps_growth_past": past_growth,
    }


def compute_valuations_for_date(snapshot_date: date, fiscal_year: int = 2025):
    """
    Compute valuations for all stocks for the given snapshot date.
    Should be called after all scrapers have finished.
    """
    ensure_tables()
    db = SessionLocal()
    try:
        print(f"[DB] Computing valuations for {snapshot_date}...")
        
        # Get all metrics for this date
        metrics_list = db.query(Metric).filter_by(snapshot_date=snapshot_date).all()
        
        count = 0
        for metric in metrics_list:
            stock = db.get(Stock, metric.stock_id)
            if not stock:
                continue
            
            # Get forecast (closest at or before this date)
            forecast = (
                db.query(Forecast)
                .filter(Forecast.stock_id == stock.id, Forecast.snapshot_date <= snapshot_date)
                .order_by(Forecast.snapshot_date.desc())
                .first()
            )
            
            # Get PE median (closest at or before this date)
            pe_rec = (
                db.query(PeMedian)
                .filter(PeMedian.stock_id == stock.id, PeMedian.snapshot_date <= snapshot_date)
                .order_by(PeMedian.snapshot_date.desc())
                .first()
            )
            
            if not forecast:
                continue
            
            ratio_dict = {
                "eps_5y": metric.eps_5y,
                "revenue_5y": metric.revenue_5y,
                "current_price": metric.current_price,
                "market_cap": metric.market_cap,
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
                .filter_by(stock_id=stock.id, snapshot_date=snapshot_date)
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
                        snapshot_date=snapshot_date,
                        fiscal_year=fiscal_year,
                        **val,
                    )
                )
            count += 1
        
        db.commit()
        print(f"[DB] Computed {count} valuations for {snapshot_date}")
    except Exception as e:
        db.rollback()
        print(f"[DB] Error computing valuations: {e}")
    finally:
        db.close()


def save_statistics(ticker: str, data: dict, snapshot_date: date):
    """
    Save statistics data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        data: Dictionary with 'sections' key containing list of sections with items
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter_by(ticker=ticker.upper()).first()
        if not stock:
            print(f"  [DB] Stock {ticker} not found, skipping statistics save")
            return
        
        # Delete existing statistics for this stock and date
        db.query(Statistic).filter_by(
            stock_id=stock.id, snapshot_date=snapshot_date
        ).delete()
        
        # Insert new statistics
        count = 0
        for section in data.get("sections", []):
            section_name = section.get("name", "Unknown")
            for item in section.get("items", []):
                label = item.get("label")
                value = item.get("value")
                display = item.get("display")
                
                if label:
                    # Only save numeric values
                    numeric_value = value if isinstance(value, (int, float)) else None
                    db.add(Statistic(
                        stock_id=stock.id,
                        snapshot_date=snapshot_date,
                        section=section_name,
                        label=label,
                        value=numeric_value,
                        display=display,
                    ))
                    count += 1
        
        db.commit()
        print(f"  [DB] Saved {count} statistics for {ticker}")
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving statistics for {ticker}: {e}")
    finally:
        db.close()


def save_metrics_breakdown(ticker: str, df, snapshot_date: date):
    """
    Save metrics breakdown data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        df: pandas DataFrame with columns [Category, Metric, period_1, period_2, ...]
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter_by(ticker=ticker.upper()).first()
        if not stock:
            print(f"  [DB] Stock {ticker} not found, skipping metrics breakdown save")
            return
        
        # Delete existing metrics breakdown for this stock and date
        db.query(MetricsBreakdown).filter_by(
            stock_id=stock.id, snapshot_date=snapshot_date
        ).delete()
        
        # Get period columns (all columns except Category and Metric)
        meta_cols = ["Category", "Metric"]
        period_cols = [c for c in df.columns if c not in meta_cols]
        
        # Insert new metrics breakdown
        count = 0
        for _, row in df.iterrows():
            category = row.get("Category", "Unknown")
            metric = row.get("Metric", "Unknown")
            
            for period in period_cols:
                value = row.get(period)
                if value is not None and isinstance(value, (int, float)):
                    db.add(MetricsBreakdown(
                        stock_id=stock.id,
                        snapshot_date=snapshot_date,
                        category=category,
                        metric=metric,
                        period=period,
                        value=value,
                    ))
                    count += 1
        
        db.commit()
        print(f"  [DB] Saved {count} metrics breakdown entries for {ticker}")
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving metrics breakdown for {ticker}: {e}")
    finally:
        db.close()

