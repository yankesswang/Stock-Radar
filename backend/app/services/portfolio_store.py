"""Durable portfolio metadata storage with JSON and SQL mirrors."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import settings
from ..database import SessionLocal
from ..models import PortfolioDeposit, PortfolioMeta


logger = logging.getLogger(__name__)

META_PATH = settings.DATA_DIR / "portfolio_meta.json"
DEPOSITS_PATH = settings.DATA_DIR / "portfolio_deposits.json"
REPORT_SNAPSHOTS_PATH = settings.DATA_DIR / "portfolio_report_snapshots.json"


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read portfolio data from %s", path)
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_meta() -> dict:
    stored = _read_json(META_PATH)
    if isinstance(stored, dict):
        return stored
    with SessionLocal() as db:
        try:
            rows = db.query(PortfolioMeta).all()
            return {row.portfolio_name: {"starting_capital": row.starting_capital} for row in rows}
        except Exception:
            logger.exception("Could not load portfolio metadata from the database")
            return {}


def write_meta(meta: dict) -> None:
    _write_json(META_PATH, meta)
    with SessionLocal() as db:
        try:
            for name, config in meta.items():
                starting_capital = config.get("starting_capital")
                existing = db.query(PortfolioMeta).filter_by(portfolio_name=name).first()
                if existing:
                    existing.starting_capital = starting_capital
                else:
                    db.add(PortfolioMeta(portfolio_name=name, starting_capital=starting_capital))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not mirror portfolio metadata to the database")


def read_deposits() -> dict:
    stored = _read_json(DEPOSITS_PATH)
    if isinstance(stored, dict):
        return stored
    with SessionLocal() as db:
        try:
            rows = db.query(PortfolioDeposit).order_by(PortfolioDeposit.deposit_date).all()
            result: dict = {}
            for row in rows:
                result.setdefault(row.portfolio_name, []).append(
                    {"date": row.deposit_date.isoformat(), "amount": row.amount}
                )
            return result
        except Exception:
            logger.exception("Could not load portfolio deposits from the database")
            return {}


def write_deposits(data: dict) -> None:
    _write_json(DEPOSITS_PATH, data)
    with SessionLocal() as db:
        try:
            for name, entries in data.items():
                db.query(PortfolioDeposit).filter_by(portfolio_name=name).delete()
                for entry in entries:
                    try:
                        deposit_date = date.fromisoformat(entry["date"])
                    except (ValueError, KeyError):
                        continue
                    db.add(
                        PortfolioDeposit(
                            portfolio_name=name,
                            deposit_date=deposit_date,
                            amount=float(entry.get("amount", 0)),
                        )
                    )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not mirror portfolio deposits to the database")


def read_report_snapshots() -> dict:
    stored = _read_json(REPORT_SNAPSHOTS_PATH)
    return stored if isinstance(stored, dict) else {}


def write_report_snapshots(data: dict) -> None:
    _write_json(REPORT_SNAPSHOTS_PATH, data)
