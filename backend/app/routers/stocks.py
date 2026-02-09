from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
import yfinance as yf

from ..database import get_db
from ..models import Stock, Industry, Metric, Forecast, Valuation, PeMedian
from ..schemas import StockBrief, StockDetail, MetricOut, ForecastOut, ValuationOut

router = APIRouter()


@router.get("", response_model=dict)
def list_stocks(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    industry: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Stock).join(Industry)
    if industry:
        q = q.filter(Industry.name == industry)
    total = q.count()
    stocks = q.order_by(Stock.ticker).offset((page - 1) * per_page).limit(per_page).all()

    # Get latest valuation for each stock
    result = []
    for s in stocks:
        latest_val = (
            db.query(Valuation)
            .filter_by(stock_id=s.id)
            .order_by(Valuation.snapshot_date.desc())
            .first()
        )
        result.append(
            StockBrief(
                ticker=s.ticker,
                company_name=s.company_name,
                industry=s.industry.name,
                current_price=latest_val.current_price if latest_val else None,
                valuation_status=latest_val.valuation_status if latest_val else None,
                valuation_diff_pct=latest_val.valuation_diff_pct if latest_val else None,
            )
        )

    return {
        "data": result,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page,
        },
    }


@router.get("/{ticker}", response_model=StockDetail)
def get_stock(ticker: str, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter(func.upper(Stock.ticker) == ticker.upper()).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock {ticker} not found")

    latest_metric = (
        db.query(Metric)
        .filter_by(stock_id=stock.id)
        .order_by(Metric.snapshot_date.desc())
        .first()
    )
    latest_forecast = (
        db.query(Forecast)
        .filter_by(stock_id=stock.id)
        .order_by(Forecast.snapshot_date.desc())
        .first()
    )
    latest_valuation = (
        db.query(Valuation)
        .filter_by(stock_id=stock.id)
        .order_by(Valuation.snapshot_date.desc())
        .first()
    )

    return StockDetail(
        ticker=stock.ticker,
        company_name=stock.company_name,
        industry=stock.industry.name,
        latest_metric=MetricOut.model_validate(latest_metric) if latest_metric else None,
        latest_forecast=ForecastOut.model_validate(latest_forecast) if latest_forecast else None,
        latest_valuation=_valuation_to_out(stock, latest_valuation) if latest_valuation else None,
    )


def _valuation_to_out(stock: Stock, v: Valuation) -> ValuationOut:
    return ValuationOut(
        ticker=stock.ticker,
        company_name=stock.company_name,
        industry=stock.industry.name,
        snapshot_date=v.snapshot_date,
        fiscal_year=v.fiscal_year,
        estimated_pe=v.estimated_pe,
        current_year_eps=v.current_year_eps,
        current_year_fair_price=v.current_year_fair_price,
        next_year_eps=v.next_year_eps,
        next_year_fair_price=v.next_year_fair_price,
        pe_median_5y=v.pe_median_5y,
        pe_median_price=v.pe_median_price,
        next_year_pe_median_price=v.next_year_pe_median_price,
        current_price=v.current_price,
        market_cap_display=v.market_cap_display,
        valuation_status=v.valuation_status,
        valuation_diff_pct=v.valuation_diff_pct,
        next_year_valuation_status=v.next_year_valuation_status,
        next_year_valuation_diff_pct=v.next_year_valuation_diff_pct,
        revenue_growth_5y=v.revenue_growth_5y,
        eps_growth_5y=v.eps_growth_5y,
        eps_growth_past=v.eps_growth_past,
    )


@router.get("/{ticker}/price")
def get_stock_price(ticker: str):
    """Get the latest real-time stock price using yfinance."""
    try:
        stock = yf.Ticker(ticker.upper())
        info = stock.info

        price = info.get("regularMarketPrice") or info.get("currentPrice")

        if price is None:
            raise HTTPException(status_code=404, detail=f"Could not fetch price for {ticker}")

        return {
            "ticker": ticker.upper(),
            "price": round(price, 2),
            "currency": info.get("currency", "USD"),
            "market_state": info.get("marketState", "UNKNOWN"),
            "previous_close": info.get("previousClose"),
            "change": round(price - info.get("previousClose", price), 2) if info.get("previousClose") else None,
            "change_percent": round((price - info.get("previousClose", price)) / info.get("previousClose", 1) * 100, 2) if info.get("previousClose") else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching price: {str(e)}")
