from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, or_, func

from ..database import get_db
from ..models import Stock, Industry, Valuation
from ..schemas import ValuationOut

router = APIRouter()


@router.get("", response_model=dict)
def list_valuations(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    industry: str | None = None,
    status: str | None = None,
    search: str | None = Query(None),
    sort_by: str = "industry",
    sort_dir: str = "asc",
    db: Session = Depends(get_db),
):
    """Main valuation table endpoint. Returns latest valuations for all stocks."""
    # Subquery: latest snapshot_date per stock
    latest_sub = (
        db.query(
            Valuation.stock_id,
            func.max(Valuation.snapshot_date).label("max_date"),
        )
        .group_by(Valuation.stock_id)
        .subquery()
    )

    q = (
        db.query(Valuation)
        .join(
            latest_sub,
            (Valuation.stock_id == latest_sub.c.stock_id)
            & (Valuation.snapshot_date == latest_sub.c.max_date),
        )
        .join(Stock, Valuation.stock_id == Stock.id)
        .join(Industry, Stock.industry_id == Industry.id)
    )

    if industry:
        q = q.filter(Industry.name == industry)
    if status:
        q = q.filter(Valuation.valuation_status == status)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        q = q.filter(
            or_(
                Stock.ticker.ilike(pattern),
                Stock.company_name.ilike(pattern),
            )
        )

    # Sorting
    sort_map = {
        "ticker": Stock.ticker,
        "industry": Industry.name,
        "current_price": Valuation.current_price,
        "estimated_pe": Valuation.estimated_pe,
        "valuation_diff_pct": Valuation.valuation_diff_pct,
        "pe_median_5y": Valuation.pe_median_5y,
        "pe_median_price": Valuation.pe_median_price,
        "eps_growth_5y": Valuation.eps_growth_5y,
    }
    sort_col = sort_map.get(sort_by, Industry.name)
    order_fn = desc if sort_dir == "desc" else asc
    q = q.order_by(order_fn(sort_col), Stock.ticker)

    total = q.count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()

    result = []
    for v in rows:
        stock = db.query(Stock).get(v.stock_id)
        result.append(
            ValuationOut(
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


@router.get("/{ticker}/history", response_model=list[ValuationOut])
def valuation_history(ticker: str, db: Session = Depends(get_db)):
    """All historical valuation snapshots for a stock."""
    from sqlalchemy import func

    stock = db.query(Stock).filter(func.upper(Stock.ticker) == ticker.upper()).first()
    if not stock:
        return []

    vals = (
        db.query(Valuation)
        .filter_by(stock_id=stock.id)
        .order_by(Valuation.snapshot_date)
        .all()
    )
    return [
        ValuationOut(
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
        for v in vals
    ]
