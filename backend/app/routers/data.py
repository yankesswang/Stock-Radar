from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..models import Stock, Industry, DataSnapshot
from ..schemas import DataStatus

router = APIRouter()


@router.get("/status", response_model=DataStatus)
def data_status(db: Session = Depends(get_db)):
    """Get data freshness info."""
    total_stocks = db.query(Stock).count()
    total_industries = db.query(Industry).count()

    latest_pe = (
        db.query(func.max(DataSnapshot.snapshot_date))
        .filter_by(data_type="pe")
        .scalar()
    )
    latest_ratio = (
        db.query(func.max(DataSnapshot.snapshot_date))
        .filter_by(data_type="ratio")
        .scalar()
    )
    latest_forecast = (
        db.query(func.max(DataSnapshot.snapshot_date))
        .filter_by(data_type="forecast")
        .scalar()
    )
    snapshot_count = db.query(DataSnapshot).count()

    return DataStatus(
        total_stocks=total_stocks,
        total_industries=total_industries,
        latest_pe_date=latest_pe,
        latest_ratio_date=latest_ratio,
        latest_forecast_date=latest_forecast,
        snapshot_count=snapshot_count,
    )


@router.post("/reload")
def reload_data(db: Session = Depends(get_db)):
    """Re-scan data directory and import new files."""
    import subprocess
    import sys
    from ..config import settings

    result = subprocess.run(
        [sys.executable, "-m", "backend.load_data"],
        cwd=str(settings.PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout[-2000:] if result.stdout else "",
        "stderr": result.stderr[-2000:] if result.stderr else "",
    }
