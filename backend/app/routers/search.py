from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from ..database import get_db
from ..models import Stock, Industry

router = APIRouter()


@router.get("")
def search_stocks(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Search stocks by ticker or company name."""
    pattern = f"%{q.upper()}%"
    stocks = (
        db.query(Stock)
        .join(Industry)
        .filter(
            or_(
                Stock.ticker.ilike(pattern),
                Stock.company_name.ilike(pattern),
            )
        )
        .order_by(Stock.ticker)
        .limit(limit)
        .all()
    )

    return [
        {
            "ticker": s.ticker,
            "company_name": s.company_name,
            "industry": s.industry.name,
        }
        for s in stocks
    ]
