from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, case, func

from ..database import get_db
from ..models import Stock, Industry

router = APIRouter()


@router.get("")
def search_stocks(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Search stocks by ticker or company name. Case-insensitive with autocomplete."""
    search_term = q.strip()
    pattern = f"%{search_term}%"

    # Prioritize exact ticker matches, then ticker starts with, then contains
    stocks = (
        db.query(Stock)
        .join(Industry)
        .filter(
            or_(
                Stock.ticker.ilike(pattern),
                Stock.company_name.ilike(pattern),
            )
        )
        .order_by(
            # Exact ticker match first (case-insensitive)
            case(
                (func.upper(Stock.ticker) == search_term.upper(), 0),
                else_=1
            ),
            # Ticker starts with search term second
            case(
                (Stock.ticker.ilike(f"{search_term}%"), 0),
                else_=1
            ),
            # Then alphabetically
            Stock.ticker
        )
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
