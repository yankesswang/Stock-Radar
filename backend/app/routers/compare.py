from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..models import Stock, Metric, Forecast, Valuation
from ..schemas import StockDetail, MetricOut, ForecastOut, ValuationOut

router = APIRouter()


@router.get("")
def compare_stocks(
    tickers: str = Query(..., description="Comma-separated tickers, up to 5"),
    db: Session = Depends(get_db),
):
    """Side-by-side comparison of up to 5 stocks."""
    ticker_list = [t.strip().upper() for t in tickers.split(",")][:5]

    result = []
    for ticker in ticker_list:
        stock = db.query(Stock).filter(func.upper(Stock.ticker) == ticker).first()
        if not stock:
            continue

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
        latest_val = (
            db.query(Valuation)
            .filter_by(stock_id=stock.id)
            .order_by(Valuation.snapshot_date.desc())
            .first()
        )

        detail = StockDetail(
            ticker=stock.ticker,
            company_name=stock.company_name,
            industry=stock.industry.name,
            latest_metric=MetricOut.model_validate(latest_metric) if latest_metric else None,
            latest_forecast=ForecastOut.model_validate(latest_forecast) if latest_forecast else None,
            latest_valuation=_val_out(stock, latest_val) if latest_val else None,
        )
        result.append(detail)

    return {"stocks": result}


@router.get("/peers/{ticker}")
def compare_with_peers(ticker: str, db: Session = Depends(get_db)):
    """Compare a stock against all industry peers tracked in the DB."""
    ticker = ticker.upper()
    stock = db.query(Stock).filter(func.upper(Stock.ticker) == ticker).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"{ticker} not found in database")

    peers = (
        db.query(Stock)
        .filter(Stock.industry_id == stock.industry_id)
        .order_by(Stock.ticker)
        .all()
    )

    result = []
    for s in peers:
        m = (
            db.query(Metric)
            .filter_by(stock_id=s.id)
            .order_by(Metric.snapshot_date.desc())
            .first()
        )
        v = (
            db.query(Valuation)
            .filter_by(stock_id=s.id)
            .order_by(Valuation.snapshot_date.desc())
            .first()
        )
        result.append({
            "ticker": s.ticker,
            "company_name": s.company_name,
            "is_target": s.ticker.upper() == ticker,
            "metrics": {
                "current_price":   m.current_price   if m else None,
                "market_cap":      m.market_cap       if m else None,
                "pe":              m.pe               if m else None,
                "pe_forward":      m.pe_forward       if m else None,
                "ev_ebitda":       m.ev_ebitda        if m else None,
                "ps":              m.ps               if m else None,
                "gross_margin":    m.gross_margin     if m else None,
                "profit_margin":   m.profit_margin    if m else None,
                "revenue":         m.revenue          if m else None,
                "fcf":             m.fcf              if m else None,
                "revenue_5y":      m.revenue_5y       if m else None,
                "eps_5y":          m.eps_5y           if m else None,
            },
            "valuation": {
                "status":                    v.valuation_status           if v else None,
                "current_year_fair_price":   v.current_year_fair_price    if v else None,
                "next_year_fair_price":      v.next_year_fair_price       if v else None,
                "valuation_diff_pct":        v.valuation_diff_pct         if v else None,
                "pe_median_5y":              v.pe_median_5y               if v else None,
            } if v else None,
        })

    return {
        "ticker": ticker,
        "industry": stock.industry.name,
        "peer_count": len(result),
        "peers": result,
    }


def _val_out(stock, v):
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
