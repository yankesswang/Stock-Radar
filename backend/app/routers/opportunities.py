"""市場機會掃描引擎 — Stage-1 量化預篩 endpoint + Stage-2 機會 journal 讀取。

- `/prescreen` 直接呼叫 src.analysis.opportunity_scanner.prescreen（純量化，零 claude 成本），
  給前端即時用，也給 scripts/market_scan.py 當 stage-2 輸入。
- `/journal*` 讀 stage-2（market_scan.py）產出的機會 journal，日期驗證 + path traversal
  防禦比照 war_room.py。
- `/overview` 打包 latest 機會 journal + market regime 供前端一次載入。
"""
import json
import re

from fastapi import APIRouter, HTTPException, Query

from ..config import settings

router = APIRouter()

OPP_DIR = settings.DATA_DIR / "agent" / "opportunities"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

VALID_SETUPS = {
    "all",
    "oversold_bounce",
    "undervalued_catalyst",
    "momentum_breakout",
    "mimi_entry",
    "breakdown_short",
}


# ── Stage-1 量化預篩 ──────────────────────────────────────────────────────────

@router.get("/prescreen")
def prescreen(
    limit: int = Query(20, ge=1, le=225, description="回傳前 N 檔（預設 20）"),
    setup: str = Query("all", description="all 或單一 setup bucket"),
    refresh: bool = Query(False, description="true 則跳過 10 分鐘快取重算"),
):
    """Stage-1 純量化預篩：掃全 universe，分類到 setup buckets、給 signal_score 排名。

    獨立快速回應（TTL 快取 10 分鐘），零 claude 成本。
    """
    if setup not in VALID_SETUPS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid setup '{setup}'. Allowed: {sorted(VALID_SETUPS)}",
        )
    try:
        from src.analysis.opportunity_scanner import prescreen as _prescreen
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"scanner import failed: {exc}")

    try:
        return _prescreen(limit=limit, setup=setup, use_cache=not refresh)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"prescreen failed: {exc}")


# ── Stage-2 機會 journal 讀取（比照 war_room.py 防禦）──────────────────────────

def _valid_date(d: str) -> bool:
    """YYYY-MM-DD only — 同時擋 path traversal（不含 / . 等）。"""
    return bool(_DATE_RE.match(d))


def _list_dates() -> list[str]:
    if not OPP_DIR.is_dir():
        return []
    dates = [p.stem for p in OPP_DIR.glob("*.json") if _valid_date(p.stem)]
    return sorted(dates, reverse=True)


def _load_journal(d: str) -> dict | None:
    if not _valid_date(d):
        return None
    path = OPP_DIR / f"{d}.json"
    try:
        resolved = path.resolve()
        if OPP_DIR.resolve() not in resolved.parents:
            return None
    except OSError:
        return None
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


@router.get("/journal")
def list_journal():
    """所有機會 journal 日期，新到舊。"""
    return {"dates": _list_dates()}


@router.get("/journal/latest")
def latest_journal():
    """最新一份機會 journal。"""
    dates = _list_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="No opportunity journal found")
    data = _load_journal(dates[0])
    if data is None:
        raise HTTPException(status_code=404, detail="Latest journal could not be read")
    return data


@router.get("/journal/{date}")
def get_journal(date: str):
    """特定 YYYY-MM-DD 的機會 journal。"""
    if not _valid_date(date):
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD")
    data = _load_journal(date)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No opportunity journal for {date}")
    return data


# ── Overview：latest journal + market regime 一次載入 ─────────────────────────

@router.get("/overview")
def overview():
    """打包 latest 機會 journal + market 快照 + sector 輪動，供前端一次載入。

    復用 market / sectors router 內部函式使數字與其他頁一致；各 block degrade
    gracefully（回 None + error），單一上游失敗不整頁空白。
    """
    dates = _list_dates()
    latest_date = dates[0] if dates else None
    journal = _load_journal(latest_date) if latest_date else None

    market = None
    try:
        from . import market as market_router

        market = market_router.get_market_overview(symbols="SPY,QQQ,IWM,^VIX,BTC-USD")
    except Exception as exc:  # noqa: BLE001
        market = {"error": str(exc), "items": []}

    sectors = None
    try:
        from . import sectors as sectors_router

        rs = sectors_router.get_sector_rs()
        sectors = {
            "benchmark": rs.get("benchmark"),
            "fetched_at": rs.get("fetched_at"),
            "items": [
                {
                    "symbol": it.get("symbol"),
                    "label": it.get("label"),
                    "zh": it.get("zh"),
                    "change_pct": it.get("change_pct"),
                    "rs_rating": it.get("rs_rating"),
                }
                for it in rs.get("items", [])
            ],
        }
    except Exception as exc:  # noqa: BLE001
        sectors = {"error": str(exc), "items": []}

    return {
        "date": latest_date,
        "available_dates": dates,
        "journal": journal,
        "market": market,
        "sectors": sectors,
    }
