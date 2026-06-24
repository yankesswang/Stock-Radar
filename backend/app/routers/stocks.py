from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
import yfinance as yf

from ..database import get_db
from ..models import Stock, Industry, Metric, Forecast, Valuation, PeMedian
from ..schemas import StockBrief, StockDetail, MetricOut, ForecastOut, ValuationOut

router = APIRouter()


class IndustryUpdate(BaseModel):
    industry: str


@router.patch("/{ticker}/industry")
def update_stock_industry(ticker: str, body: IndustryUpdate, db: Session = Depends(get_db)):
    """Reassign a stock's industry/sector group. Creates the industry if it's new.

    Drives the portfolio sector pie (holdings get their sector from this join),
    so changing it here reclassifies the ticker on the next portfolio load.
    """
    name = (body.industry or "").strip()
    if not name or len(name) > 50 or any(c in name for c in "\r\n\t"):
        raise HTTPException(status_code=400, detail="Industry must be 1-50 characters.")

    stock = db.query(Stock).filter(func.upper(Stock.ticker) == ticker.upper()).first()
    if not stock:
        raise HTTPException(
            status_code=404,
            detail=f"{ticker.upper()} not found — add it first via Add Stock.",
        )

    industry = db.query(Industry).filter(Industry.name == name).first()
    if not industry:
        industry = Industry(name=name)
        db.add(industry)
        db.flush()  # assign industry.id before linking

    stock.industry_id = industry.id
    db.commit()
    return {"ticker": stock.ticker, "industry": name}


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
    """Get the latest real-time stock price using yfinance. Taiwan (.TW) prices are converted TWD→USD."""
    try:
        stock = yf.Ticker(ticker.upper())
        info = stock.info

        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("previousClose")
        currency = info.get("currency", "USD")

        if price is None:
            raise HTTPException(status_code=404, detail=f"Could not fetch price for {ticker}")

        # Convert TWD → USD for Taiwan-listed stocks
        if ticker.upper().endswith(".TW") and currency == "TWD":
            fx = yf.Ticker("TWD=X")
            fx_hist = fx.history(period="1d", auto_adjust=True)
            usd_twd = float(fx_hist["Close"].dropna().iloc[-1]) if not fx_hist.empty else None
            if usd_twd:
                price = price / usd_twd
                if prev_close is not None:
                    prev_close = prev_close / usd_twd
                currency = "USD"

        change = round(price - prev_close, 4) if prev_close else None
        change_pct = round(change / prev_close * 100, 2) if (change and prev_close) else None

        return {
            "ticker": ticker.upper(),
            "price": round(price, 4),
            "currency": currency,
            "market_state": info.get("marketState", "UNKNOWN"),
            "previous_close": round(prev_close, 4) if prev_close else None,
            "change": change,
            "change_percent": change_pct,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching price: {str(e)}")
