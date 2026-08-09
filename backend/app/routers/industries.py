from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..models import Stock, Industry, Valuation, Metric

router = APIRouter()


class RenameRequest(BaseModel):
    old_name: str
    new_name: str


@router.get("")
def list_industries(db: Session = Depends(get_db)):
    """List all industries with stock count and summary stats."""
    industries = db.query(Industry).order_by(Industry.name).all()

    # Latest valuation subquery
    latest_sub = (
        db.query(
            Valuation.stock_id,
            func.max(Valuation.snapshot_date).label("max_date"),
        )
        .group_by(Valuation.stock_id)
        .subquery()
    )

    result = []
    for ind in industries:
        stock_count = db.query(Stock).filter_by(industry_id=ind.id).count()

        # Get latest valuations for stocks in this industry
        stock_ids = [s.id for s in db.query(Stock).filter_by(industry_id=ind.id).all()]
        if not stock_ids:
            result.append({
                "id": ind.id,
                "name": ind.name,
                "stock_count": 0,
                "avg_pe": None,
                "overvalued_count": 0,
                "undervalued_count": 0,
            })
            continue

        latest_vals = (
            db.query(Valuation)
            .join(
                latest_sub,
                (Valuation.stock_id == latest_sub.c.stock_id)
                & (Valuation.snapshot_date == latest_sub.c.max_date),
            )
            .filter(Valuation.stock_id.in_(stock_ids))
            .all()
        )

        pe_values = [v.estimated_pe for v in latest_vals if v.estimated_pe]
        avg_pe = round(sum(pe_values) / len(pe_values), 2) if pe_values else None
        overvalued = sum(1 for v in latest_vals if v.valuation_status == "overvalued")
        undervalued = sum(1 for v in latest_vals if v.valuation_status == "undervalued")

        result.append({
            "id": ind.id,
            "name": ind.name,
            "stock_count": stock_count,
            "avg_pe": avg_pe,
            "overvalued_count": overvalued,
            "undervalued_count": undervalued,
        })

    return result


@router.patch("/rename")
def rename_industry(req: RenameRequest, db: Session = Depends(get_db)):
    """Rename an industry group. If the target name already exists, merge the
    source group's stocks into it and delete the now-empty source group."""
    old_name = req.old_name.strip()
    new_name = req.new_name.strip()

    if not new_name or len(new_name) > 50 or any(c in new_name for c in "\r\n\t"):
        raise HTTPException(status_code=400, detail="New name must be 1-50 characters.")
    if old_name == new_name:
        raise HTTPException(status_code=400, detail="New name is the same as the current name.")

    source = db.query(Industry).filter(Industry.name == old_name).first()
    if not source:
        raise HTTPException(status_code=404, detail=f"Industry '{old_name}' not found.")

    target = db.query(Industry).filter(Industry.name == new_name).first()
    if target and target.id != source.id:
        # Merge: reassign stocks then drop the source group
        moved = (
            db.query(Stock)
            .filter(Stock.industry_id == source.id)
            .update({Stock.industry_id: target.id}, synchronize_session=False)
        )
        db.delete(source)
        db.commit()
        return {"ok": True, "merged": True, "name": new_name, "moved": moved}

    source.name = new_name
    db.commit()
    return {"ok": True, "merged": False, "name": new_name}


@router.get("/{industry_id}/stocks")
def industry_stocks(industry_id: int, db: Session = Depends(get_db)):
    """Get all stocks in an industry with their latest valuations."""
    industry = db.query(Industry).get(industry_id)
    if not industry:
        raise HTTPException(status_code=404, detail="Industry not found")

    stocks = db.query(Stock).filter_by(industry_id=industry_id).order_by(Stock.ticker).all()

    result = []
    for s in stocks:
        val = (
            db.query(Valuation)
            .filter_by(stock_id=s.id)
            .order_by(Valuation.snapshot_date.desc())
            .first()
        )
        item = {
            "ticker": s.ticker,
            "company_name": s.company_name,
            "industry": industry.name,
        }
        if val:
            item.update({
                "snapshot_date": str(val.snapshot_date),
                "estimated_pe": val.estimated_pe,
                "current_year_eps": val.current_year_eps,
                "current_year_fair_price": val.current_year_fair_price,
                "next_year_eps": val.next_year_eps,
                "next_year_fair_price": val.next_year_fair_price,
                "pe_median_5y": val.pe_median_5y,
                "pe_median_price": val.pe_median_price,
                "current_price": val.current_price,
                "market_cap_display": val.market_cap_display,
                "valuation_status": val.valuation_status,
                "valuation_diff_pct": val.valuation_diff_pct,
                "next_year_valuation_status": val.next_year_valuation_status,
                "next_year_valuation_diff_pct": val.next_year_valuation_diff_pct,
                "eps_growth_5y": val.eps_growth_5y,
            })
        result.append(item)

    return {"industry": industry.name, "stocks": result}
