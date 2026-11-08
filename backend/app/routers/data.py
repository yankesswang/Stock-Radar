import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..config import settings
from ..jobs import job_registry
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


def _reload_data_sync() -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "backend.load_data"],
        cwd=str(settings.PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=15 * 60,
    )
    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:] if result.stdout else "",
        "stderr": result.stderr[-2000:] if result.stderr else "",
    }


@router.post("/reload", status_code=status.HTTP_202_ACCEPTED)
async def reload_data():
    """Queue a data-directory import without blocking an API worker."""
    record = job_registry.submit("data-reload", _reload_data_sync)
    return {"job_id": record["id"], "status": record["status"]}


@router.get("/jobs/{job_id}")
def data_job_status(job_id: str):
    record = job_registry.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return record
