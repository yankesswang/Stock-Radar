from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from ..database import get_db
from ..models import Stock, Industry, Valuation, Metric
from ..schemas import ScreenRequest

router = APIRouter()

# Fields that live on the Valuation model
VALUATION_FIELDS = {
    "estimated_pe", "current_year_eps", "current_year_fair_price",
    "next_year_eps", "next_year_fair_price", "pe_median_5y",
    "pe_median_price", "current_price", "valuation_diff_pct",
    "eps_growth_5y", "revenue_growth_5y",
}

# Fields that live on the Metric model
METRIC_FIELDS = {
    "pe", "pe_forward", "ps", "pb", "peg_ratio",
    "ev_ebitda", "gross_margin", "operating_margin", "profit_margin",
    "roe", "roa", "roic", "current_ratio", "debt_equity",
    "revenue", "net_income", "fcf", "eps",
    "dividend_yield", "beta", "market_cap",
    "earnings_yield", "fcf_yield", "price_target",
}

OP_MAP = {
    "lt": lambda col, val: col < val,
    "lte": lambda col, val: col <= val,
    "gt": lambda col, val: col > val,
    "gte": lambda col, val: col >= val,
    "eq": lambda col, val: col == val,
}


@router.post("")
def screen_stocks(req: ScreenRequest, db: Session = Depends(get_db)):
    """Filter stocks by metric/valuation conditions."""
    # Latest valuation subquery
    latest_sub = (
        db.query(
            Valuation.stock_id,
            func.max(Valuation.snapshot_date).label("max_date"),
        )
        .group_by(Valuation.stock_id)
        .subquery()
    )

    # Check if we need metric filters
    needs_metrics = any(f.field in METRIC_FIELDS for f in req.filters)

    if needs_metrics:
        # Latest metric subquery
        latest_metric_sub = (
            db.query(
                Metric.stock_id,
                func.max(Metric.snapshot_date).label("max_date"),
            )
            .group_by(Metric.stock_id)
            .subquery()
        )

        q = (
            db.query(Valuation, Metric, Stock, Industry)
            .join(
                latest_sub,
                (Valuation.stock_id == latest_sub.c.stock_id)
                & (Valuation.snapshot_date == latest_sub.c.max_date),
            )
            .join(Stock, Valuation.stock_id == Stock.id)
            .join(Industry, Stock.industry_id == Industry.id)
            .join(
                Metric,
                (Metric.stock_id == Stock.id)
                & (Metric.stock_id == latest_metric_sub.c.stock_id)
                & (Metric.snapshot_date == latest_metric_sub.c.max_date),
            )
        )
    else:
        q = (
            db.query(Valuation, Stock, Industry)
            .join(
                latest_sub,
                (Valuation.stock_id == latest_sub.c.stock_id)
                & (Valuation.snapshot_date == latest_sub.c.max_date),
            )
            .join(Stock, Valuation.stock_id == Stock.id)
            .join(Industry, Stock.industry_id == Industry.id)
        )

    # Filter by industries
    if req.industries:
        q = q.filter(Industry.name.in_(req.industries))

    # Filter by valuation status
    if req.valuation_status:
        q = q.filter(Valuation.valuation_status == req.valuation_status)

    # Search by ticker/company name
    if req.search and req.search.strip():
        pattern = f"%{req.search.strip()}%"
        q = q.filter(
            or_(
                Stock.ticker.ilike(pattern),
                Stock.company_name.ilike(pattern),
            )
        )

    # Apply custom filters
    for f in req.filters:
        op_fn = OP_MAP.get(f.op)
        if not op_fn:
            continue

        if f.field in VALUATION_FIELDS:
            col = getattr(Valuation, f.field, None)
            if col is not None:
                q = q.filter(op_fn(col, f.value))
        elif f.field in METRIC_FIELDS and needs_metrics:
            col = getattr(Metric, f.field, None)
            if col is not None:
                q = q.filter(op_fn(col, f.value))

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
    from sqlalchemy import desc, asc
    sort_col = sort_map.get(req.sort_by, Industry.name)
    order_fn = desc if req.sort_dir == "desc" else asc
    q = q.order_by(order_fn(sort_col), Stock.ticker)

    total = q.count()
    rows = q.offset((req.page - 1) * req.per_page).limit(req.per_page).all()

    # Build result - fetch latest metric per stock for display fields
    result = []
    if needs_metrics:
        for val, metric, stock, industry in rows:
            result.append(_build_row(val, stock, industry, metric))
    else:
        # Batch-fetch latest metrics for display
        stock_ids = [row[1].id for row in rows]  # Stock is at index 1
        latest_metrics = {}
        if stock_ids:
            for sid in stock_ids:
                m = (
                    db.query(Metric)
                    .filter_by(stock_id=sid)
                    .order_by(Metric.snapshot_date.desc())
                    .first()
                )
                if m:
                    latest_metrics[sid] = m

        for val, stock, industry in rows:
            metric = latest_metrics.get(stock.id)
            result.append(_build_row(val, stock, industry, metric))

    return {
        "data": result,
        "pagination": {
            "page": req.page,
            "per_page": req.per_page,
            "total": total,
            "total_pages": (total + req.per_page - 1) // req.per_page,
        },
    }


def _build_row(val, stock, industry, metric):
    return {
        "ticker": stock.ticker,
        "company_name": stock.company_name,
        "industry": industry.name,
        "current_price": val.current_price,
        "estimated_pe": val.estimated_pe,
        "pe_median_5y": val.pe_median_5y,
        "pe_median_price": val.pe_median_price,
        "valuation_status": val.valuation_status,
        "valuation_diff_pct": val.valuation_diff_pct,
        "eps_growth_5y": val.eps_growth_5y,
        "pe": metric.pe if metric else None,
        "profit_margin": metric.profit_margin if metric else None,
        "roe": metric.roe if metric else None,
        "market_cap": metric.market_cap if metric else None,
    }
