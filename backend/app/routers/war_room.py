"""War Room — 戰情中心 dashboard endpoints.

Serves the AI decision journal (data/agent/journal/{date}.json) plus a packed
`overview` that bundles the latest journal with a market snapshot, sector
moves, live holdings and 30-day sparklines so the frontend can render the
whole ops-center in a single request.
"""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ..config import settings

router = APIRouter()

JOURNAL_DIR = settings.DATA_DIR / "agent" / "journal"
LEDGER_DIR = settings.DATA_DIR / "agent" / "ledger"
OPP_DIR = settings.DATA_DIR / "agent" / "opportunities"
# 05:00 預產的逐檔 AI 決策卡彙整（ai_report.py 寫）—— 復盤的逐檔證據層
AI_REPORT_DIR = settings.DATA_DIR / "ai_reports"
# 完整六區塊復盤的鏡像（war_room.py save_mirror_note 落地處；launchd 讀不了 ~/Documents）
MIRROR_STOCK_DIR = Path(os.environ.get("STOCK_DIGEST_DIR", "~/.arthur-digest/stock")).expanduser()
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ── Sparkline cache (10 min TTL) — avoids hammering yfinance on re-fetch ────────
_SPARK_CACHE: dict[str, dict] = {}
_SPARK_TTL = 600  # seconds
_SPARK_MAX_TICKERS = 50
_SPARK_POINTS = 30


def _spark_get(ticker: str):
    entry = _SPARK_CACHE.get(ticker)
    if entry and (time.time() - entry["ts"]) < _SPARK_TTL:
        return entry["data"]
    return None


def _spark_set(ticker: str, data) -> None:
    now = time.time()
    stale = [k for k, v in _SPARK_CACHE.items() if now - v["ts"] >= _SPARK_TTL]
    for k in stale:
        del _SPARK_CACHE[k]
    _SPARK_CACHE[ticker] = {"data": data, "ts": now}


def _fetch_sparkline(ticker: str) -> list[float]:
    """Last ~30 daily closes for one ticker. Empty list on failure (frontend
    draws a flat placeholder). Tries yfinance, falls back to the Yahoo chart API."""
    cached = _spark_get(ticker)
    if cached is not None:
        return cached

    closes: list[float] = []
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker.upper()).history(period="2mo", auto_adjust=True)
        c = hist["Close"].dropna() if hist is not None and not hist.empty else None
        if c is not None and not c.empty:
            closes = [round(float(x), 4) for x in c.tolist()]
    except Exception:
        closes = []

    if not closes:
        try:
            from .chart import _fetch_yahoo_chart_df

            df = _fetch_yahoo_chart_df(ticker, "3m", "1d")
            c = df["Close"].dropna() if df is not None and not df.empty else None
            if c is not None and not c.empty:
                closes = [round(float(x), 4) for x in c.tolist()]
        except Exception:
            closes = []

    closes = closes[-_SPARK_POINTS:]
    _spark_set(ticker, closes)
    return closes


def _fetch_sparklines(tickers: list[str]) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    if not tickers:
        return result
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_sparkline, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                result[t.upper()] = fut.result()
            except Exception:
                result[t.upper()] = []
    return result


def _load_holdings() -> list[dict]:
    """Flatten all CSV portfolios into a single list of live-priced positions.

    Reuses portfolio.get_csv_portfolio so numbers match the Portfolio page.
    Skips CASH rows. Degrades to [] on any failure."""
    try:
        from ..database import SessionLocal
        from . import portfolio as portfolio_router

        db = SessionLocal()
        try:
            data = portfolio_router.get_csv_portfolio(name=None, db=db)
        finally:
            db.close()
    except Exception:
        return []

    out: list[dict] = []
    for pname, block in (data.get("portfolios") or {}).items():
        summary = block.get("summary") or {}
        total_value = summary.get("total_value") or 0
        for h in block.get("holdings") or []:
            ticker = str(h.get("ticker") or "").upper()
            if not ticker or ticker == "CASH":
                continue
            mv = h.get("market_value")
            weight = round(mv / total_value * 100, 2) if (mv and total_value) else None
            out.append({
                "ticker": ticker,
                "company": h.get("company_name") or ticker,
                "portfolio": pname,
                "account": h.get("account"),
                "weight_pct": weight,
                "market_value": mv,
                "day_change_pct": h.get("day_change_pct"),
                "return_pct": h.get("return_pct"),
                "current_price": h.get("current_price"),
            })
    # Heaviest positions first so the matrix reads by conviction.
    out.sort(key=lambda x: (x.get("market_value") or 0), reverse=True)
    return out


def _valid_date(date: str) -> bool:
    """YYYY-MM-DD only — also blocks path traversal (no `/`, `.` etc.)."""
    return bool(_DATE_RE.match(date))


def _list_dates() -> list[str]:
    """Journal dates, newest first. Empty list when dir missing/empty."""
    if not JOURNAL_DIR.is_dir():
        return []
    dates = [
        p.stem
        for p in JOURNAL_DIR.glob("*.json")
        if _valid_date(p.stem)
    ]
    return sorted(dates, reverse=True)


def _load_journal(date: str) -> dict | None:
    if not _valid_date(date):
        return None
    path = JOURNAL_DIR / f"{date}.json"
    # Resolve and confirm it stays inside JOURNAL_DIR (defence in depth).
    try:
        resolved = path.resolve()
        if JOURNAL_DIR.resolve() not in resolved.parents:
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
    """All journal dates, newest first."""
    return {"dates": _list_dates()}


@router.get("/journal/latest")
def latest_journal():
    """Most recent journal document."""
    dates = _list_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="No journal entries found")
    data = _load_journal(dates[0])
    if data is None:
        raise HTTPException(status_code=404, detail="Latest journal could not be read")
    return data


@router.get("/journal/{date}")
def get_journal(date: str):
    """Journal for a specific YYYY-MM-DD date."""
    if not _valid_date(date):
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD")
    data = _load_journal(date)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No journal for {date}")
    return data


@router.get("/holdings")
def holdings():
    """All live-priced portfolio positions (CASH excluded), heaviest first."""
    return {"holdings": _load_holdings()}


@router.get("/sparklines")
def sparklines(tickers: str = Query(..., description="Comma-separated tickers, max 50")):
    """Batch last-30-close arrays keyed by ticker. Missing/failed → [] (flat line)."""
    raw = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    # De-dupe, cap at 50 to bound yfinance load.
    seen: list[str] = []
    for t in raw:
        if t not in seen:
            seen.append(t)
    seen = seen[:_SPARK_MAX_TICKERS]
    return {"sparklines": _fetch_sparklines(seen)}


@router.get("/overview")
def overview():
    """Packed dashboard payload: latest journal + market snapshot + sector moves.

    Reuses the existing market/sectors router internals so the numbers match the
    rest of the app. Each block degrades gracefully (returns None + error) so a
    single slow/failed upstream never blanks the whole dashboard.
    """
    dates = _list_dates()
    latest_date = dates[0] if dates else None
    journal = _load_journal(latest_date) if latest_date else None

    # ── Market snapshot (SPY / QQQ / VIX + tone) ──────────────────────────────
    market = None
    try:
        from . import market as market_router

        snap = market_router.get_market_overview(symbols="SPY,QQQ,IWM,^VIX,BTC-USD")
        market = snap
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        market = {"error": str(exc), "items": []}

    # ── Sector moves (11 SPDR sectors, today %) ───────────────────────────────
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

    # ── Live holdings (bundled so the frontend skips a round-trip) ────────────
    holdings_list = _load_holdings()

    # ── Sparklines for every held ticker (batch, cached) ──────────────────────
    spark = {}
    try:
        tickers = [h["ticker"] for h in holdings_list][:_SPARK_MAX_TICKERS]
        spark = _fetch_sparklines(tickers)
    except Exception:
        spark = {}

    return {
        "date": latest_date,
        "available_dates": dates,
        "journal": journal,
        "market": market,
        "sectors": sectors,
        "holdings": holdings_list,
        "sparklines": spark,
    }


# ── 盤後復盤（review）— 純讀檔，零 yfinance，給前端圖表化復盤頁 ──────────────────

def _read_json_for(date: str, base: Path) -> dict | None:
    if not _valid_date(date):
        return None
    path = base / f"{date}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_report_md(date: str) -> str | None:
    """war_room.py 鏡像落地的六區塊完整復盤 markdown。"""
    if not _valid_date(date):
        return None
    path = MIRROR_STOCK_DIR / date / "🎖️ War Room.md"
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError:
        return None


# 每張卡只帶前端會畫的欄位。整份 ai_reports/{date}.json 還包含 markdown 全文、
# 每檔 10 則原始新聞與 analysis 長文 —— 那些是 war_room 簡報的素材，不是復盤頁要讀的。
_AI_CARD_FIELDS = (
    "ticker", "company_name", "generated_at", "price", "score", "rating", "action",
    "trend", "summary", "buy_zone", "target", "stop_loss", "risks", "catalysts",
    "analysis_bullets",
)


def _read_ai_cards(date: str) -> dict | None:
    """05:00 預產的逐檔 AI 決策卡（data/ai_reports/{date}.json）—— 復盤的「逐檔證據層」。

    2026-09-03 起 AI 卡不再有自己的日報分頁：它是 war_room 的備料，讀者在復盤頁
    對照「War Room 決策 vs AI 卡看法」，分歧的才值得多看一眼。
    """
    raw = _read_json_for(date, AI_REPORT_DIR)
    if not raw:
        return None
    cards = [
        {k: c.get(k) for k in _AI_CARD_FIELDS}
        for c in (raw.get("cards") or []) if isinstance(c, dict) and c.get("ticker")
    ]
    return {
        "generated_at": raw.get("generated_at"),
        "total": raw.get("total"),
        "ok": raw.get("ok", len(cards)),
        "errors": raw.get("errors") or [],
        "holdings": raw.get("holdings") or [],
        "cards": cards,
    }


def _review_dates() -> list[str]:
    """有任何復盤產物（journal / 完整報告 / 帳本 / AI 卡）的日期，新到舊。

    只有 AI 卡、復盤本身沒跑的日子也要列 —— 那天不是「沒事」，是復盤缺件；
    前端會把它寫明，而不是讓那天從日曆上消失。
    """
    dates: set[str] = set(_list_dates())
    for base in (LEDGER_DIR, AI_REPORT_DIR):
        if base.is_dir():
            dates |= {p.stem for p in base.glob("*.json") if _valid_date(p.stem)}
    try:
        if MIRROR_STOCK_DIR.is_dir():
            dates |= {
                p.name for p in MIRROR_STOCK_DIR.iterdir()
                if p.is_dir() and _valid_date(p.name) and (p / "🎖️ War Room.md").is_file()
            }
    except OSError:
        pass
    return sorted(dates, reverse=True)


@router.get("/review")
def review_index():
    """與 /review/dates 相同；每日報告面板對每個分頁都用 GET {api} 取日期清單。"""
    return {"dates": _review_dates()}


@router.get("/review/dates")
def review_dates():
    return {"dates": _review_dates()}


@router.get("/review/trend")
def review_trend():
    """跨日決策品質曲線：每份 journal 的 scoring_of_previous 聚合成時間序列。

    欄位：n_decisions / triggered / trigger_rate（%）/ avg_change_pct（決策期間標的平均漲跌）
    ＋當日 market_regime。前端畫「復盤品質曲線」用。
    """
    rows: list[dict] = []
    for d in sorted(_list_dates()):
        j = _load_journal(d) or {}
        sc = j.get("scoring_of_previous") or {}
        results = [r for r in (sc.get("results") or []) if isinstance(r, dict)]
        row: dict = {
            "date": d,
            "market_regime": j.get("market_regime"),
            "n_decisions": len(j.get("decisions") or []),
        }
        if results:
            trig = sum(1 for r in results if r.get("status") == "triggered")
            scored = [r["change_pct"] for r in results
                      if isinstance(r.get("change_pct"), (int, float))]
            row["scored_n"] = len(results)
            row["triggered"] = trig
            row["trigger_rate"] = round(trig / len(results) * 100, 1)
            row["avg_change_pct"] = (
                round(sum(scored) / len(scored), 2) if scored else None
            )
        rows.append(row)
    return {"trend": rows}


@router.get("/review/{date}")
def review(date: str):
    """單日復盤完整包：journal 決策 + 決策帳本 + 六區塊報告 md + 當日機會掃描 + 逐檔 AI 卡。"""
    if not _valid_date(date):
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD")
    journal = _load_journal(date)
    ledger = _read_json_for(date, LEDGER_DIR)
    report_md = _read_report_md(date)
    opportunities = _read_json_for(date, OPP_DIR)
    ai_cards = _read_ai_cards(date)
    if not any([journal, ledger, report_md, opportunities, ai_cards]):
        raise HTTPException(status_code=404, detail=f"No review artifacts for {date}")
    # 面板頂端「產生於」：復盤本身的時間優先；復盤沒跑就退回 AI 卡的時間
    generated_at = (journal or {}).get("generated_at") or (ai_cards or {}).get("generated_at")
    return {
        "date": date,
        "generated_at": generated_at,
        "journal": journal,
        "ledger": ledger,
        "report_md": report_md,
        "opportunities": opportunities,
        "ai_cards": ai_cards,
    }
