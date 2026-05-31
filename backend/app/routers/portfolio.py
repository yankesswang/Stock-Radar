"""
Portfolio management: CRUD for portfolios + holdings.
Each GET /portfolio/{id} enriches holdings with live prices (yfinance)
and valuation data from the DB.

Also provides GET /portfolio/gsheet which reads holdings directly from
a public Google Sheet (CSV export) and enriches with DB valuation data.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import io
import json
import re
import ssl
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
import pandas as pd
import yfinance as yf

from ..config import settings
from ..database import get_db, SessionLocal
from ..models import Portfolio, Holding, Stock, Industry, Valuation, PortfolioSnapshot, HoldingSnapshot, PortfolioMeta, PortfolioDeposit
from .chart import _fetch_yahoo_chart_df, _find_sr_levels
try:
    from src.config import STOCK_LIST
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from src.config import STOCK_LIST

router = APIRouter()

# ── Google Sheet config (ID loaded from backend/.env) ────────────────────────
_SKIP = {"CASH", "eToro"}
_TICKER_INDUSTRY_FALLBACK = {
    ticker.upper(): industry
    for industry, tickers in STOCK_LIST.items()
    for ticker in tickers
}


def _gsheet_url(gsheet_id: str, sheet_name: str | None = None) -> str:
    if not gsheet_id:
        raise ValueError("Sheet ID is not set. Add GSHEET_ID / GSHEET_MOM_ID to backend/.env")
    if sheet_name:
        return f"https://docs.google.com/spreadsheets/d/{gsheet_id}/export?format=csv&sheet={urllib.parse.quote(sheet_name)}"
    return f"https://docs.google.com/spreadsheets/d/{gsheet_id}/export?format=csv&gid=0"


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class PortfolioCreate(BaseModel):
    name: str
    description: str = ""


class PortfolioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class HoldingCreate(BaseModel):
    ticker: str
    shares: float
    avg_cost: float
    notes: str = ""


class HoldingUpdate(BaseModel):
    shares: float | None = None
    avg_cost: float | None = None
    notes: str | None = None


class CsvHoldingCreate(BaseModel):
    portfolio: str
    account: str
    ticker: str
    shares: float
    avg_cost: float
    notes: str = ""


class CsvHoldingUpdate(BaseModel):
    portfolio: str
    account: str
    ticker: str
    shares: float | None = None
    avg_cost: float | None = None
    notes: str | None = None


# ── Live price helpers ────────────────────────────────────────────────────────

def _fetch_price(ticker: str) -> float | None:
    try:
        hist = yf.Ticker(ticker.upper()).history(period="5d", auto_adjust=True)
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return None


def _fetch_prices(tickers: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_price, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            p = fut.result()
            if p is not None:
                prices[t.upper()] = p
    return prices


def _fetch_price_info(ticker: str) -> dict | None:
    """Return price + daily change by comparing last two closes."""
    try:
        hist = yf.Ticker(ticker.upper()).history(period="5d", auto_adjust=True)
        closes = hist["Close"].dropna()
        if closes.empty:
            return None
        price = float(closes.iloc[-1])
        if len(closes) >= 2:
            prev  = float(closes.iloc[-2])
            change     = round(price - prev, 4)
            change_pct = round(change / prev * 100, 2)
        else:
            prev = change = change_pct = None
        return {"price": price, "prev_close": prev, "day_change": change, "day_change_pct": change_pct}
    except Exception:
        return None


def _fetch_prices_info(tickers: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_price_info, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            info = fut.result()
            if info is not None:
                result[t.upper()] = info
    return result


def _fetch_technicals(ticker: str) -> dict | None:
    """RSI-14, MA5/20/50, volume ratio, S/R proximity for a single ticker (6mo history)."""
    try:
        hist = yf.Ticker(ticker.upper()).history(period="6mo", auto_adjust=True)
        if hist.empty or len(hist) < 15:
            return None
        closes  = hist["Close"].dropna().astype(float)
        volumes = hist["Volume"].dropna().astype(float)
        price   = float(closes.iloc[-1])

        # Volume
        today_vol  = float(volumes.iloc[-1]) if not volumes.empty else None
        avg_vol_20 = float(volumes.iloc[-21:-1].mean()) if len(volumes) >= 21 else (
                     float(volumes[:-1].mean()) if len(volumes) > 1 else None)
        vol_ratio  = round(today_vol / avg_vol_20, 2) if (today_vol and avg_vol_20 and avg_vol_20 > 0) else None

        # Moving averages
        ma5  = round(float(closes.iloc[-5:].mean()),  2) if len(closes) >= 5  else None
        ma20 = round(float(closes.iloc[-20:].mean()), 2) if len(closes) >= 20 else None
        ma50 = round(float(closes.iloc[-50:].mean()), 2) if len(closes) >= 50 else None
        price_vs_ma20_pct = round((price - ma20) / ma20 * 100, 2) if ma20 else None

        # MA5/MA20 crossover (within most recent close)
        ma_cross = None
        if len(closes) >= 22:
            prev_ma5  = float(closes.iloc[-6:-1].mean())
            prev_ma20 = float(closes.iloc[-21:-1].mean())
            cur_ma5   = float(closes.iloc[-5:].mean())
            cur_ma20  = float(closes.iloc[-20:].mean())
            if prev_ma5 < prev_ma20 and cur_ma5 > cur_ma20:
                ma_cross = "golden"
            elif prev_ma5 > prev_ma20 and cur_ma5 < cur_ma20:
                ma_cross = "death"

        # RSI-14 (simple non-Wilder)
        rsi = None
        if len(closes) >= 15:
            deltas   = closes.diff().dropna()
            gains    = deltas.clip(lower=0)
            losses   = (-deltas).clip(lower=0)
            avg_gain = float(gains.iloc[-14:].mean())
            avg_loss = float(losses.iloc[-14:].mean())
            if avg_loss > 0:
                rsi = round(100 - 100 / (1 + avg_gain / avg_loss), 1)
            elif avg_gain > 0:
                rsi = 100.0
            else:
                rsi = 50.0

        # S/R proximity — reuse the same algorithm as the chart endpoint
        sr = _find_sr_levels(hist, window=10, n_levels=6)
        near_support: list[dict] = []
        near_resistance: list[dict] = []
        _PROXIMITY = 0.03   # 3% threshold for "near"

        for lvl in sr.get("support", []):
            dist_pct = (price - lvl["price"]) / price * 100
            if 0 <= dist_pct <= _PROXIMITY * 100:
                near_support.append({
                    "price":     lvl["price"],
                    "dist_pct":  round(dist_pct, 1),
                    "strength":  lvl["strength"],
                })
        near_support.sort(key=lambda x: x["dist_pct"])

        for lvl in sr.get("resistance", []):
            dist_pct = (lvl["price"] - price) / price * 100
            if 0 <= dist_pct <= _PROXIMITY * 100:
                near_resistance.append({
                    "price":     lvl["price"],
                    "dist_pct":  round(dist_pct, 1),
                    "strength":  lvl["strength"],
                })
        near_resistance.sort(key=lambda x: x["dist_pct"])

        return {
            "price":              round(price, 2),
            "volume":             int(today_vol)  if today_vol  else None,
            "avg_volume_20":      int(avg_vol_20) if avg_vol_20 else None,
            "volume_ratio":       vol_ratio,
            "rsi_14":             rsi,
            "ma5":                ma5,
            "ma20":               ma20,
            "ma50":               ma50,
            "price_vs_ma20_pct":  price_vs_ma20_pct,
            "ma_cross":           ma_cross,  # "golden" | "death" | None
            "near_support":       near_support[:2],
            "near_resistance":    near_resistance[:2],
        }
    except Exception:
        return None


def _fetch_technicals_batch(tickers: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_technicals, t): t for t in tickers}
        for fut in as_completed(futures):
            t    = futures[fut]
            data = fut.result()
            if data is not None:
                result[t.upper()] = data
    return result


# ── Valuation lookup ──────────────────────────────────────────────────────────

def _get_valuations(tickers: list[str], db: Session) -> dict[str, dict]:
    """Return the latest valuation row keyed by ticker (upper-cased)."""
    if not tickers:
        return {}
    upper = [t.upper() for t in tickers]
    subq = (
        db.query(Valuation.stock_id, func.max(Valuation.snapshot_date).label("max_date"))
        .group_by(Valuation.stock_id)
        .subquery()
    )
    rows = (
        db.query(Stock, Industry, Valuation)
        .join(Industry, Stock.industry_id == Industry.id)
        .join(subq, Stock.id == subq.c.stock_id)
        .join(Valuation, (Valuation.stock_id == Stock.id) & (Valuation.snapshot_date == subq.c.max_date))
        .filter(Stock.ticker.in_(upper))
        .all()
    )
    result = {}
    for stock, industry, val in rows:
        result[stock.ticker.upper()] = {
            "company_name":            stock.company_name,
            "industry":                industry.name,
            "pe_median_price":         val.pe_median_price,
            "current_year_fair_price": val.current_year_fair_price,
            "next_year_fair_price":    val.next_year_fair_price,
            "valuation_status":        val.valuation_status,
            "valuation_diff_pct":      val.valuation_diff_pct,
            "pe_median_5y":            val.pe_median_5y,
            "estimated_pe":            val.estimated_pe,
        }
    for ticker in upper:
        if ticker not in result and ticker in _TICKER_INDUSTRY_FALLBACK:
            result[ticker] = {
                "company_name": ticker,
                "industry": _TICKER_INDUSTRY_FALLBACK[ticker],
            }
    return result


# ── Enrich holdings ───────────────────────────────────────────────────────────

def _enrich(holdings: list[Holding], db: Session) -> list[dict]:
    tickers = list({h.ticker.upper() for h in holdings})
    prices  = _fetch_prices(tickers)
    vals    = _get_valuations(tickers, db)

    rows = []
    for h in holdings:
        t        = h.ticker.upper()
        price    = prices.get(t)
        val      = vals.get(t, {})
        cost_val = h.shares * h.avg_cost
        mkt_val  = h.shares * price if price else None
        pnl      = mkt_val - cost_val if mkt_val is not None else None
        pnl_pct  = (pnl / cost_val * 100) if (pnl is not None and cost_val) else None

        # distance to PE fair value
        fair_pe  = val.get("pe_median_price")
        to_fair  = ((fair_pe - price) / price * 100) if (fair_pe and price) else None

        rows.append({
            "id":          h.id,
            "ticker":      t,
            "shares":      h.shares,
            "avg_cost":    h.avg_cost,
            "notes":       h.notes,
            "added_date":  str(h.added_date),
            # live data
            "current_price": price,
            "market_value":  round(mkt_val, 2) if mkt_val else None,
            "cost_basis":    round(cost_val, 2),
            "unrealized_pnl": round(pnl, 2) if pnl is not None else None,
            "return_pct":    round(pnl_pct, 2) if pnl_pct is not None else None,
            # valuation
            "company_name":            val.get("company_name", t),
            "industry":                val.get("industry"),
            "pe_median_price":         fair_pe,
            "current_year_fair_price": val.get("current_year_fair_price"),
            "next_year_fair_price":    val.get("next_year_fair_price"),
            "valuation_status":        val.get("valuation_status"),
            "valuation_diff_pct":      val.get("valuation_diff_pct"),
            "pe_median_5y":            val.get("pe_median_5y"),
            "pct_to_fair_pe":          round(to_fair, 1) if to_fair is not None else None,
        })
    return rows


def _portfolio_summary(enriched: list[dict]) -> dict:
    total_value = sum(r["market_value"] or 0 for r in enriched)
    total_cost  = sum(r["cost_basis"] for r in enriched)
    total_pnl   = total_value - total_cost
    return_pct  = (total_pnl / total_cost * 100) if total_cost else 0

    # sector allocation
    sectors: dict[str, float] = {}
    for r in enriched:
        ind = r.get("industry") or "Unknown"
        sectors[ind] = sectors.get(ind, 0) + (r["market_value"] or r["cost_basis"])

    return {
        "total_value":  round(total_value, 2),
        "total_cost":   round(total_cost,  2),
        "total_pnl":    round(total_pnl,   2),
        "return_pct":   round(return_pct,  2),
        "sectors":      [{"name": k, "value": round(v, 2)} for k, v in sorted(sectors.items(), key=lambda x: -x[1])],
    }


# ── Portfolio CSV loader (Google Sheets → local cache fallback) ───────────────

def _fetch_raw(url: str) -> bytes:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.urlopen(url, context=ctx, timeout=15).read()


def _parse_main_sheet(raw: bytes) -> list[dict]:
    """Main sheet is already in clean CSV format: portfolio,account,ticker,shares,avg_cost,leverage,notes"""
    import math
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = df.columns.str.lower().str.strip()
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df = df[df["ticker"].str.match(r"^[A-Z]{1,6}(-[A-Z])?$")]
    # CASH rows have no shares — treat as 1 share at the cash balance value
    df.loc[df["ticker"] == "CASH", "shares"] = 1.0
    df = df.dropna(subset=["shares", "avg_cost"])
    df["portfolio"] = df["portfolio"].astype(str).str.strip()
    df["account"]   = df["account"].astype(str).str.strip()
    df["notes"]     = df.get("notes", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()
    if "leverage" not in df.columns:
        df["leverage"] = 1.0
    df["leverage"] = pd.to_numeric(df["leverage"], errors="coerce").fillna(1.0)
    return df[["portfolio", "account", "ticker", "shares", "avg_cost", "leverage", "notes"]].to_dict("records")


def _parse_mom_sheet(raw: bytes) -> list[dict]:
    """Mom sheet: positional format — ticker=col0, buy_price=col2, shares=col3"""
    import math

    def _f(val):
        try:
            s = str(val).replace(",", "").strip()
            if s.lower() in ("nan", "inf", "-inf", ""):
                return None
            r = float(s)
            return None if (math.isnan(r) or math.isinf(r)) else r
        except Exception:
            return None

    df = pd.read_csv(io.BytesIO(raw), header=None)
    rows = []
    for i in range(1, len(df)):
        row = df.iloc[i]
        c0 = str(row[0] if pd.notna(row[0]) else "").strip()
        if not c0 or "CASH" in c0.upper() or "台幣" in c0 or "NaN" in c0:
            continue
        shares   = _f(row[3])
        avg_cost = _f(row[2])
        if not shares or not avg_cost:
            continue
        if re.match(r"^[A-Z]{1,5}$", c0):
            rows.append({"portfolio": "Mom", "account": "eToro", "ticker": c0,
                         "shares": shares, "avg_cost": avg_cost, "notes": ""})
        elif re.match(r"^\d{2,4}$", c0):
            rows.append({"portfolio": "Mom", "account": "eToro",
                         "ticker": c0.zfill(4) + ".TW",
                         "shares": shares, "avg_cost": avg_cost,
                         "notes": "TW stock (TWD price)"})
    return rows


_CSV_PATH  = settings.DATA_DIR / "portfolio.csv"
_META_PATH = settings.DATA_DIR / "portfolio_meta.json"


def _read_meta() -> dict:
    """Return {portfolio_name: {starting_capital: float, ...}}. Falls back to DB if JSON missing."""
    if _META_PATH.exists():
        try:
            return json.loads(_META_PATH.read_text())
        except Exception:
            pass
    # JSON missing — load from DB
    db = SessionLocal()
    try:
        rows = db.query(PortfolioMeta).all()
        return {r.portfolio_name: {"starting_capital": r.starting_capital} for r in rows}
    except Exception:
        return {}
    finally:
        db.close()


def _write_meta(meta: dict) -> None:
    _META_PATH.write_text(json.dumps(meta, indent=2))
    # Mirror to DB
    db = SessionLocal()
    try:
        for name, cfg in meta.items():
            sc = cfg.get("starting_capital")
            existing = db.query(PortfolioMeta).filter_by(portfolio_name=name).first()
            if existing:
                existing.starting_capital = sc
            else:
                db.add(PortfolioMeta(portfolio_name=name, starting_capital=sc))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Portfolio DB] Error saving meta: {e}")
    finally:
        db.close()


# ── Deposits (for MWR / XIRR) ─────────────────────────────────────────────────

_DEPOSITS_PATH = settings.DATA_DIR / "portfolio_deposits.json"
_REPORT_SNAPSHOTS_PATH = settings.DATA_DIR / "portfolio_report_snapshots.json"


def _read_deposits() -> dict:
    """Return {portfolio_name: [{"date": "YYYY-MM-DD", "amount": float}, ...]}. Falls back to DB if JSON missing."""
    if _DEPOSITS_PATH.exists():
        try:
            return json.loads(_DEPOSITS_PATH.read_text())
        except Exception:
            pass
    # JSON missing — load from DB
    db = SessionLocal()
    try:
        rows = db.query(PortfolioDeposit).order_by(PortfolioDeposit.deposit_date).all()
        result: dict = {}
        for r in rows:
            result.setdefault(r.portfolio_name, []).append(
                {"date": r.deposit_date.isoformat(), "amount": r.amount}
            )
        return result
    except Exception:
        return {}
    finally:
        db.close()


def _write_deposits(data: dict) -> None:
    _DEPOSITS_PATH.write_text(json.dumps(data, indent=2))
    # Mirror to DB: full replace per portfolio
    db = SessionLocal()
    try:
        from datetime import date as _date
        for name, entries in data.items():
            db.query(PortfolioDeposit).filter_by(portfolio_name=name).delete()
            for entry in entries:
                try:
                    d = _date.fromisoformat(entry["date"])
                except (ValueError, KeyError):
                    continue
                db.add(PortfolioDeposit(
                    portfolio_name=name,
                    deposit_date=d,
                    amount=float(entry.get("amount", 0)),
                ))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Portfolio DB] Error saving deposits: {e}")
    finally:
        db.close()


def _read_report_snapshots() -> dict:
    """Return {portfolio_name: [daily snapshot, ...]}."""
    if _REPORT_SNAPSHOTS_PATH.exists():
        try:
            return json.loads(_REPORT_SNAPSHOTS_PATH.read_text())
        except Exception:
            pass
    return {}


def _write_report_snapshots(data: dict) -> None:
    _REPORT_SNAPSHOTS_PATH.write_text(json.dumps(data, indent=2))


def _build_daily_snapshot(portfolio: str, summary: dict, holdings: list[dict], as_of: date) -> dict:
    total_value = float(summary.get("total_value") or 0.0)
    items = []
    for h in holdings:
        mv = float(h.get("market_value") or h.get("cost_basis") or 0.0)
        weight = (mv / total_value * 100.0) if total_value > 0 else 0.0
        items.append({
            "ticker": str(h.get("ticker") or "").upper(),
            "account": str(h.get("account") or ""),
            "shares": float(h.get("shares") or 0.0),
            "avg_cost": float(h.get("avg_cost") or 0.0),
            "market_value": round(mv, 2),
            "weight_pct": round(weight, 2),
            "return_pct": h.get("return_pct"),
        })
    return {
        "date": as_of.isoformat(),
        "portfolio": portfolio,
        "summary": {
            "total_value": round(total_value, 2),
            "total_cash": round(float(summary.get("total_cash") or 0.0), 2),
            "cash_pct": round(float(summary.get("cash_pct") or 0.0), 2),
            "net_gain": round(float(summary.get("net_gain") or 0.0), 2),
            "net_return_pct": round(float(summary.get("net_return_pct") or 0.0), 2),
            "mwr": summary.get("mwr"),
            "position_count": len([h for h in holdings if str(h.get("ticker") or "").upper() != "CASH"]),
        },
        "holdings": items,
    }


def _upsert_db_snapshot(snapshot: dict) -> None:
    """Persist a daily snapshot dict into portfolio_snapshots + holding_snapshots tables."""
    from datetime import date as _date
    portfolio_name = snapshot.get("portfolio", "")
    date_str = snapshot.get("date", "")
    if not portfolio_name or not date_str:
        return
    try:
        snap_date = _date.fromisoformat(date_str)
    except ValueError:
        return

    summary = snapshot.get("summary", {})
    holdings = snapshot.get("holdings", [])

    db = SessionLocal()
    try:
        existing = (
            db.query(PortfolioSnapshot)
            .filter_by(portfolio_name=portfolio_name, snapshot_date=snap_date)
            .first()
        )
        if existing:
            existing.total_value    = summary.get("total_value")
            existing.total_cash     = summary.get("total_cash")
            existing.cash_pct       = summary.get("cash_pct")
            existing.net_gain       = summary.get("net_gain")
            existing.net_return_pct = summary.get("net_return_pct")
            existing.mwr            = summary.get("mwr")
            existing.position_count = summary.get("position_count")
            # replace child rows
            db.query(HoldingSnapshot).filter_by(snapshot_id=existing.id).delete()
            snap_id = existing.id
        else:
            ps = PortfolioSnapshot(
                portfolio_name = portfolio_name,
                snapshot_date  = snap_date,
                total_value    = summary.get("total_value"),
                total_cash     = summary.get("total_cash"),
                cash_pct       = summary.get("cash_pct"),
                net_gain       = summary.get("net_gain"),
                net_return_pct = summary.get("net_return_pct"),
                mwr            = summary.get("mwr"),
                position_count = summary.get("position_count"),
            )
            db.add(ps)
            db.flush()
            snap_id = ps.id

        for h in holdings:
            db.add(HoldingSnapshot(
                snapshot_id  = snap_id,
                ticker       = str(h.get("ticker") or "").upper(),
                account      = str(h.get("account") or ""),
                shares       = h.get("shares"),
                avg_cost     = h.get("avg_cost"),
                market_value = h.get("market_value"),
                weight_pct   = h.get("weight_pct"),
                return_pct   = h.get("return_pct"),
            ))

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Portfolio DB] Error saving snapshot for {portfolio_name} {date_str}: {e}")
    finally:
        db.close()


def _upsert_daily_snapshot(portfolio: str, snapshot: dict) -> dict:
    data = _read_report_snapshots()
    rows = data.get(portfolio, [])
    rows = [r for r in rows if r.get("date") != snapshot["date"]]
    rows.append(snapshot)
    rows.sort(key=lambda r: r.get("date", ""))
    # Keep the latest 400 snapshots (~ >1 year trading days)
    data[portfolio] = rows[-400:]
    _write_report_snapshots(data)
    _upsert_db_snapshot(snapshot)
    return data


def _aggregate_by_ticker(holdings: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for h in holdings:
        ticker = str(h.get("ticker") or "").upper()
        if not ticker:
            continue
        row = out.setdefault(ticker, {"ticker": ticker, "shares": 0.0, "market_value": 0.0, "weight_pct": 0.0})
        row["shares"] += float(h.get("shares") or 0.0)
        row["market_value"] += float(h.get("market_value") or 0.0)
        row["weight_pct"] += float(h.get("weight_pct") or 0.0)
    for v in out.values():
        v["shares"] = round(v["shares"], 6)
        v["market_value"] = round(v["market_value"], 2)
        v["weight_pct"] = round(v["weight_pct"], 2)
    return out


def _pick_baseline(snapshots: list[dict], as_of: date, period: str) -> dict | None:
    older = [s for s in snapshots if s.get("date") and s["date"] < as_of.isoformat()]
    if not older:
        return None
    if period == "weekly":
        target = (as_of - timedelta(days=7)).isoformat()
        candidates = [s for s in older if s["date"] <= target]
        if candidates:
            return candidates[-1]
    if period == "monthly":
        target = (as_of - timedelta(days=30)).isoformat()
        candidates = [s for s in older if s["date"] <= target]
        if candidates:
            return candidates[-1]
    if period == "quarterly":
        target = (as_of - timedelta(days=90)).isoformat()
        candidates = [s for s in older if s["date"] <= target]
        if candidates:
            return candidates[-1]
    return older[-1]


def _bucket_key_from_iso(datestr: str, interval: str) -> str | None:
    try:
        d = date.fromisoformat(datestr)
    except Exception:
        return None
    if interval == "quarterly":
        q = (d.month - 1) // 3 + 1
        return f"{d.year}-Q{q}"
    return f"{d.year}-{d.month:02d}"


def _bucket_label(bucket_key: str, interval: str) -> str:
    if interval == "quarterly":
        y, q = bucket_key.split("-Q")
        return f"{y} Q{q}"
    return bucket_key


def _build_risk_flags(current_snapshot: dict, baseline_snapshot: dict | None) -> list[dict]:
    flags: list[dict] = []
    cur_sum = current_snapshot.get("summary", {})
    cur_holdings = [h for h in current_snapshot.get("holdings", []) if h.get("ticker") != "CASH"]
    cur_holdings.sort(key=lambda h: float(h.get("weight_pct") or 0.0), reverse=True)

    top1 = float(cur_holdings[0].get("weight_pct") or 0.0) if cur_holdings else 0.0
    top3 = sum(float(h.get("weight_pct") or 0.0) for h in cur_holdings[:3])
    cash_pct = float(cur_sum.get("cash_pct") or 0.0)

    if top1 >= 35:
        flags.append({"level": "high", "message": f"單一持股集中度偏高（Top1 {top1:.1f}%）"})
    elif top1 >= 25:
        flags.append({"level": "medium", "message": f"單一持股集中度偏高（Top1 {top1:.1f}%）"})

    if top3 >= 65:
        flags.append({"level": "medium", "message": f"前三大持股占比 {top3:.1f}% ，組合分散度可再提升"})

    if cash_pct >= 35:
        flags.append({"level": "info", "message": f"現金比重 {cash_pct:.1f}% ，可留意資金效率"})

    if baseline_snapshot:
        base_val = float((baseline_snapshot.get("summary") or {}).get("total_value") or 0.0)
        cur_val = float(cur_sum.get("total_value") or 0.0)
        if base_val > 0:
            delta_pct = (cur_val - base_val) / base_val * 100.0
            if abs(delta_pct) >= 5:
                flags.append({"level": "info", "message": f"本期組合市值波動 {delta_pct:+.2f}% ，建議檢查主要貢獻部位"})

    return flags


def _calc_mwr(deposits: list[dict], current_value: float) -> float | None:
    """
    Annualised Money-Weighted Return (XIRR).
    deposits: [{"date": "YYYY-MM-DD", "amount": float}, ...]
    Returns MWR as a percentage (e.g. 15.3), or None if not computable.
    Requires the earliest deposit to be at least 30 days ago for a meaningful result.
    """
    from datetime import date as _date
    import numpy as np

    if not deposits or current_value <= 0:
        return None

    today = _date.today()
    # Build (cash_flow, date) pairs — deposits are outflows (negative)
    cf_dates = [(_date.fromisoformat(d["date"]), -float(d["amount"])) for d in deposits]
    cf_dates.append((today, current_value))
    cf_dates.sort(key=lambda x: x[0])

    d0 = cf_dates[0][0]
    times = np.array([(d - d0).days / 365.25 for d, _ in cf_dates])
    cfs   = np.array([cf for _, cf in cf_dates])

    def npv(r):
        return np.sum(cfs / (1.0 + r) ** times)

    def dnpv(r):
        return np.sum(-times * cfs / (1.0 + r) ** (times + 1))

    # Newton-Raphson with fallback bracket search
    r = 0.1
    for _ in range(200):
        f  = npv(r)
        df = dnpv(r)
        if abs(df) < 1e-14:
            break
        step = f / df
        r -= step
        r = max(r, -0.9999)
        if abs(step) < 1e-9:
            break

    # Guard against non-convergence or absurd values (> ±10000%)
    if not np.isfinite(r) or r <= -1 or abs(r) > 100:
        return None
    return round(float(r) * 100, 2)


def _read_csv() -> pd.DataFrame:
    """Read the local portfolio.csv (source of truth for edits)."""
    if not _CSV_PATH.exists():
        raise FileNotFoundError("portfolio.csv not found — click 'Sync from Sheet' first")
    return pd.read_csv(_CSV_PATH)


def _write_csv(df: pd.DataFrame) -> None:
    df.to_csv(_CSV_PATH, index=False)


def _writeback_to_google_sheet(df: pd.DataFrame) -> None:
    """
    Write local CSV holdings back to Google Sheet (optional bi-directional sync).
    Requires service-account credentials when GSHEET_WRITEBACK_ENABLED=true.
    """
    if not settings.GSHEET_WRITEBACK_ENABLED:
        return

    spreadsheet_id = settings.GSHEET_WRITEBACK_SPREADSHEET_ID or settings.GSHEET_ID
    if not spreadsheet_id:
        raise ValueError("GSHEET_WRITEBACK_SPREADSHEET_ID/GSHEET_ID is not set")

    try:
        import gspread  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "gspread is required for writeback. Install: pip install gspread google-auth"
        ) from exc

    creds_dict = None
    creds_file = (settings.GSHEET_SERVICE_ACCOUNT_FILE or "").strip()
    creds_json = (settings.GSHEET_SERVICE_ACCOUNT_JSON or "").strip()

    if creds_json:
        try:
            creds_dict = json.loads(creds_json)
        except Exception as exc:
            raise ValueError("GSHEET_SERVICE_ACCOUNT_JSON is invalid JSON") from exc

    if creds_dict:
        gc = gspread.service_account_from_dict(creds_dict)
    elif creds_file:
        gc = gspread.service_account(filename=creds_file)
    else:
        raise ValueError(
            "Google writeback enabled but no credentials configured. "
            "Set GSHEET_SERVICE_ACCOUNT_FILE or GSHEET_SERVICE_ACCOUNT_JSON."
        )

    sh = gc.open_by_key(spreadsheet_id)
    if settings.GSHEET_WRITEBACK_WORKSHEET:
        ws = sh.worksheet(settings.GSHEET_WRITEBACK_WORKSHEET)
    else:
        ws = sh.get_worksheet(0)
        if ws is None:
            raise ValueError("No worksheet found in target Google Sheet")

    cols = ["portfolio", "account", "ticker", "shares", "avg_cost", "leverage", "notes"]
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = ""
    out = out[cols]

    out["portfolio"] = out["portfolio"].fillna("").astype(str).str.strip()
    out["account"] = out["account"].fillna("").astype(str).str.strip()
    out["ticker"] = out["ticker"].fillna("").astype(str).str.upper().str.strip()
    out["shares"] = pd.to_numeric(out["shares"], errors="coerce").fillna(0)
    out["avg_cost"] = pd.to_numeric(out["avg_cost"], errors="coerce").fillna(0)
    out["leverage"] = pd.to_numeric(out["leverage"], errors="coerce").fillna(1)
    out["notes"] = out["notes"].fillna("").astype(str).str.strip()

    values = [cols] + out.values.tolist()
    ws.clear()
    ws.update("A1", values, value_input_option="USER_ENTERED")


def _persist_csv(df: pd.DataFrame) -> None:
    """
    Persist to local CSV and optionally write back to Google Sheet.
    Writeback happens first so failures are visible and local file won't silently diverge.
    """
    if settings.GSHEET_WRITEBACK_ENABLED:
        _writeback_to_google_sheet(df)
    _write_csv(df)


def _sync_from_sheets() -> pd.DataFrame:
    """Fetch fresh data from Google Sheets and overwrite local portfolio.csv."""
    if not settings.GSHEET_ID:
        raise ValueError("GSHEET_ID is not set in backend/.env")
    main_url = f"https://docs.google.com/spreadsheets/d/{settings.GSHEET_ID}/export?format=csv&gid=0"
    rows = _parse_main_sheet(_fetch_raw(main_url))
    if settings.GSHEET_MOM_ID:
        mom_url = f"https://docs.google.com/spreadsheets/d/{settings.GSHEET_MOM_ID}/export?format=csv&gid=0"
        mom_rows = _parse_main_sheet(_fetch_raw(mom_url))
        for r in mom_rows:
            r["portfolio"] = "Mom"
        rows += mom_rows
    df = pd.DataFrame(rows)
    _write_csv(df)
    return df


# ── Google Sheet loader ──────────────────────────────────────────────────────

def _load_gsheet(gsheet_id: str, sheet_name: str | None = None) -> list[dict]:
    """
    Download the public Google Sheet as CSV and parse holdings.
    Actual sheet column layout (row 0 = header, skipped):
      0: account marker ("etoro" / "firstrade") — may appear on same row as first ticker
      1: ticker (matches ^[A-Z]{1,5}$)
      2: current price (股價)
      4: shares (股數)
      8: position value (價值)
      9: P&L % (損益%)
      10: portfolio weight % (佔比)
    """
    import math

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    response = urllib.request.urlopen(_gsheet_url(gsheet_id, sheet_name), context=ctx, timeout=15)
    df = pd.read_csv(io.BytesIO(response.read()), header=None)

    positions: list[dict] = []
    acct = ""
    total_val = 0.0
    overall_return: float | None = None

    def _float(val, strip=""):
        """Parse a cell to float; return None for missing/nan/inf values."""
        try:
            s = str(val).replace(",", "").replace(strip, "").strip()
            if s.lower() in ("nan", "inf", "-inf", ""):
                return None
            result = float(s)
            return None if (math.isnan(result) or math.isinf(result)) else result
        except (ValueError, TypeError):
            return None

    for i in range(1, len(df)):
        row = df.iloc[i]
        c0 = str(row[0] if pd.notna(row[0]) else "").strip()
        c1 = str(row[1] if pd.notna(row[1]) else "").strip()

        # Account marker — may share a row with the first ticker (e.g. "firstrade" + "VRT")
        if "etoro" in c0.lower():
            acct = "eToro"
            continue          # eToro marker row never has a ticker
        if "firstrade" in c0.lower():
            acct = "Firstrade"
            # fall through — this row also contains the first Firstrade ticker

        if "績效" in c0 or "報酬率" in c1:
            if "報酬率" in c1:
                v = _float(row[2], "%")
                if v is not None:
                    overall_return = v
            continue

        # eToro aggregate row (no ticker, has value + weight)
        if acct == "eToro" and not c1:
            v = _float(row[8])
            p = _float(row[10], "%")
            if v and v > 0 and p and 0 < p < 50:
                positions.insert(0, {
                    "account": "eToro", "ticker": "eToro",
                    "price": None, "shares": None,
                    "value": v, "pnl_pct": None, "weight": p,
                })
            continue

        # Individual stock row
        if re.match(r"^[A-Z]{1,5}$", c1):
            positions.append({
                "account": acct or "Unknown",
                "ticker":  c1,
                "price":   _float(row[2]),
                "shares":  _float(row[4]),
                "value":   _float(row[8]) or 0.0,
                "pnl_pct": _float(row[9], "%"),
                "weight":  _float(row[10], "%"),
            })
            continue

        # Firstrade portfolio total row
        if acct == "Firstrade" and not c1 and not c0:
            v = _float(row[8])
            if v and 30_000 < v < 500_000:
                total_val = v

    if not total_val:
        total_val = sum(p["value"] for p in positions)

    return positions, round(total_val, 2), overall_return


# ── Beta calculation ──────────────────────────────────────────────────────────

def _price_history(ticker: str, period: str = "1y") -> "pd.DataFrame":
    try:
        hist = yf.Ticker(ticker.upper()).history(period=period, auto_adjust=True)
        if hist is not None and not hist.empty:
            return hist
    except Exception:
        pass
    try:
        return _fetch_yahoo_chart_df(ticker.upper(), period, "1d")
    except Exception:
        return pd.DataFrame()


def _calc_ticker_stats(ticker: str, market_returns: "pd.Series") -> dict:
    """Compute beta, annualized vol, max drawdown, 1y return and daily returns series."""
    result: dict = {"beta": None, "returns": None, "vol_pct": None, "max_drawdown_pct": None, "return_1y_pct": None}
    try:
        hist = _price_history(ticker, "1y")
        if len(hist) < 30:
            return result
        closes = hist["Close"].dropna()
        r = closes.pct_change().dropna()
        r.index = pd.to_datetime(r.index).date

        aligned_r, aligned_m = r.align(market_returns, join="inner")
        if len(aligned_r) >= 30:
            cov = float(aligned_r.cov(aligned_m))
            var = float(aligned_m.var())
            result["beta"] = round(cov / var, 3) if var else None

        if len(r) >= 30:
            result["vol_pct"] = round(float(r.std()) * (252 ** 0.5) * 100, 2)
            cum = (1 + r).cumprod()
            dd = (cum - cum.cummax()) / cum.cummax()
            result["max_drawdown_pct"] = round(float(dd.min()) * 100, 2)
            if len(closes) >= 2:
                result["return_1y_pct"] = round((float(closes.iloc[-1]) / float(closes.iloc[0]) - 1) * 100, 2)
            result["returns"] = r
    except Exception:
        pass
    return result


def _compute_portfolio_stats(
    weight_map: "dict[str, float]",
    ticker_stats: "dict[str, dict]",
    risk_free_rate: float,
    capm_expected_return: float | None,
) -> dict:
    """Build synthetic portfolio return series from current weights, then derive stats."""
    result: dict = {"volatility_pct": None, "max_drawdown_pct": None, "sharpe_ratio": None, "return_1y_pct": None, "alpha_pct": None}
    try:
        series_pairs: list[tuple[float, "pd.Series"]] = []
        for ticker, weight in weight_map.items():
            if weight <= 0:
                continue
            r = ticker_stats.get(ticker, {}).get("returns")
            if r is not None and len(r) >= 30:
                series_pairs.append((weight, r))

        if not series_pairs:
            return result

        common_idx = set(series_pairs[0][1].index)
        for _, r in series_pairs[1:]:
            common_idx &= set(r.index)
        if len(common_idx) < 30:
            return result

        dates = sorted(common_idx)
        port_r: "pd.Series" = sum(w * r.loc[dates] for w, r in series_pairs)  # type: ignore[assignment]

        vol = float(port_r.std()) * (252 ** 0.5) * 100
        result["volatility_pct"] = round(vol, 2)

        cum = (1 + port_r).cumprod()
        dd = (cum - cum.cummax()) / cum.cummax()
        result["max_drawdown_pct"] = round(float(dd.min()) * 100, 2)

        actual_return = (float((1 + port_r).prod()) - 1) * 100
        result["return_1y_pct"] = round(actual_return, 2)

        if vol > 0:
            result["sharpe_ratio"] = round((actual_return - risk_free_rate) / vol, 3)

        if capm_expected_return is not None:
            result["alpha_pct"] = round(actual_return - capm_expected_return, 2)
    except Exception:
        pass
    return result


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/csv/beta")
def get_portfolio_beta(
    name: str | None = Query(None, description="Portfolio name; omit for all"),
    risk_free_rate: float = Query(4.5, description="Annual risk-free rate in percent"),
):
    """
    Calculate weighted portfolio beta vs S&P 500 (SPY, 1-year daily returns).
    Returns per-holding betas and the value-weighted portfolio beta.
    """
    try:
        df = _read_csv()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if not isinstance(risk_free_rate, (int, float)):
        risk_free_rate = 4.5

    df.columns = df.columns.str.lower().str.strip()
    df["ticker"]    = df["ticker"].astype(str).str.upper().str.strip()
    df["portfolio"] = df["portfolio"].astype(str).str.strip()
    df["shares"]    = pd.to_numeric(df["shares"],   errors="coerce")
    df["avg_cost"]  = pd.to_numeric(df["avg_cost"], errors="coerce")
    df = df.dropna(subset=["shares", "avg_cost"])

    if name:
        df = df[df["portfolio"].str.lower() == name.strip().lower()]
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Portfolio '{name}' not found")

    if "leverage" not in df.columns:
        df["leverage"] = 1.0
    df["leverage"] = pd.to_numeric(df["leverage"], errors="coerce").fillna(1.0)

    non_cash_df = df[df["ticker"] != "CASH"]
    tickers = non_cash_df["ticker"].unique().tolist()

    # Fetch market (SPY) returns once
    try:
        spy_hist = _price_history("SPY", "1y")
        if spy_hist.empty:
            raise ValueError("No SPY price history")
        market_returns = spy_hist["Close"].pct_change().dropna()
        market_returns.index = pd.to_datetime(market_returns.index).date
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch SPY data: {exc}")
    market_return_pct = (
        round((float(spy_hist["Close"].dropna().iloc[-1]) / float(spy_hist["Close"].dropna().iloc[0]) - 1) * 100, 2)
        if len(spy_hist["Close"].dropna()) >= 2
        else None
    )

    # Fetch current prices for weighting
    prices = _fetch_prices(tickers)

    # Calculate stats for each ticker in parallel
    ticker_stats: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_calc_ticker_stats, t, market_returns): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            ticker_stats[t] = fut.result()

    # Build per-portfolio weighted beta + stats
    results: dict[str, dict] = {}
    for pname, group in df.groupby("portfolio", sort=False):
        holdings_out = []
        total_val = 0.0
        beta_exposure = 0.0

        for _, row in group.iterrows():
            t        = row["ticker"]
            shares   = float(row["shares"])
            leverage = float(row.get("leverage") or 1.0)
            if t == "CASH":
                price    = float(row["avg_cost"])
                beta     = 0.0
                stats    = {}
            else:
                price = prices.get(t) or float(row["avg_cost"])
                stats = ticker_stats.get(t, {})
                beta  = stats.get("beta")

            mkt_val           = shares * price
            total_val        += mkt_val
            effective_val     = mkt_val * (leverage if t != "CASH" else 0.0)
            beta_equiv        = (beta * effective_val) if beta is not None else None
            if beta_equiv is not None:
                beta_exposure += beta_equiv

            capm_holding = (
                round(risk_free_rate + beta * ((market_return_pct or 0) - risk_free_rate), 2)
                if beta is not None and market_return_pct is not None else None
            )
            holdings_out.append({
                "ticker":                  t,
                "beta":                    beta,
                "vol_pct":                 stats.get("vol_pct"),
                "max_drawdown_pct":        stats.get("max_drawdown_pct"),
                "return_1y_pct":           stats.get("return_1y_pct"),
                "shares":                  round(shares, 6),
                "price":                   round(price, 2),
                "leverage":                leverage,
                "market_value":            round(mkt_val, 2),
                "effective_exposure":      round(effective_val, 2),
                "market_equivalent_value": round(beta_equiv, 2) if beta_equiv is not None else None,
                "capm_expected_return_pct": capm_holding,
            })

        portfolio_beta = round(beta_exposure / total_val, 3) if total_val else None

        for item in holdings_out:
            mv  = float(item["market_value"] or 0)
            bev = item.get("market_equivalent_value")
            item["weight_pct"]           = round(mv / total_val * 100, 2) if total_val else None
            item["effective_weight_pct"] = round(float(item["effective_exposure"] or 0) / total_val * 100, 2) if total_val else None
            item["beta_contribution"]    = round(bev / total_val, 4)      if bev is not None and total_val else None
            item["beta_contribution_pct"]= round(bev / total_val * 100, 2) if bev is not None and total_val else None
        holdings_out.sort(key=lambda x: abs(x.get("beta_contribution") or 0), reverse=True)

        portfolio_capm_return = (
            round(risk_free_rate + portfolio_beta * ((market_return_pct or 0) - risk_free_rate), 2)
            if portfolio_beta is not None and market_return_pct is not None else None
        )

        # Build weight_map for portfolio stats (non-cash weights, normalised)
        weight_map: dict[str, float] = {}
        for item in holdings_out:
            t = item["ticker"]
            if t != "CASH" and total_val:
                weight_map[t] = (item["market_value"] or 0) / total_val

        port_stats = _compute_portfolio_stats(weight_map, ticker_stats, risk_free_rate, portfolio_capm_return)

        results[pname] = {
            "portfolio_beta":             portfolio_beta,
            "total_market_value":         round(total_val, 2),
            "benchmark":                  "SPY",
            "period":                     "1y",
            "risk_free_rate_pct":         risk_free_rate,
            "benchmark_return_pct":       market_return_pct,
            "capm_expected_return_pct":   portfolio_capm_return,
            "volatility_pct":             port_stats.get("volatility_pct"),
            "max_drawdown_pct":           port_stats.get("max_drawdown_pct"),
            "sharpe_ratio":               port_stats.get("sharpe_ratio"),
            "return_1y_pct":              port_stats.get("return_1y_pct"),
            "alpha_pct":                  port_stats.get("alpha_pct"),
            "holdings":                   holdings_out,
        }

    return {"portfolios": results}


@router.get("/csv")
def get_csv_portfolio(
    name: str | None = Query(None, description="Filter to one portfolio by name"),
    db: Session = Depends(get_db),
):
    """
    Read holdings from data/portfolio.csv and enrich with live prices + DB valuations.

    CSV must have columns: portfolio, account, ticker, shares, avg_cost, notes (notes optional)
    Optional ?name=Main to return only one portfolio.

    Response:
      {
        "portfolio_names": [...],
        "portfolios": {
          "Main": { "summary": {...}, "holdings": [...] },
          ...
        }
      }
    """
    try:
        df = _read_csv()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to load portfolio data: {exc}")

    df.columns = df.columns.str.lower().str.strip()
    required = {"portfolio", "account", "ticker", "shares", "avg_cost"}
    missing = required - set(df.columns)
    if missing:
        raise HTTPException(status_code=422, detail=f"portfolio.csv is missing columns: {sorted(missing)}")

    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["portfolio"] = df["portfolio"].astype(str).str.strip()
    df["account"] = df["account"].astype(str).str.strip()
    df["notes"] = df.get("notes", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()
    if "leverage" not in df.columns:
        df["leverage"] = 1.0
    df["leverage"] = pd.to_numeric(df["leverage"], errors="coerce").fillna(1.0)

    portfolio_names = df["portfolio"].unique().tolist()

    # When called internally (not via FastAPI), guard against accidental Query objects.
    if name is not None and not isinstance(name, str):
        name = None

    if name:
        df = df[df["portfolio"].str.lower() == name.strip().lower()]
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Portfolio '{name}' not found in portfolio.csv")

    # Fetch live prices + valuations once across all portfolios
    all_tickers = df["ticker"].unique().tolist()
    price_infos = _fetch_prices_info(all_tickers)
    prices = {t: info["price"] for t, info in price_infos.items()}
    vals = _get_valuations(all_tickers, db)

    # USD → TWD exchange rate (TWD=X gives units of TWD per 1 USD)
    usd_twd_rate = _fetch_price("TWD=X")

    meta     = _read_meta()
    deposits = _read_deposits()
    portfolios: dict[str, dict] = {}
    for pname, group in df.groupby("portfolio", sort=False):
        holdings = []
        for _, row in group.iterrows():
            t = row["ticker"]
            pinfo = price_infos.get(t, {})
            price = pinfo.get("price")
            val = vals.get(t, {})
            shares = float(row["shares"])
            avg_cost = float(row["avg_cost"])
            leverage = float(row.get("leverage") or 1)
            cost_basis = round(shares * avg_cost, 2)
            market_value = round(shares * price, 2) if price else None
            raw_pnl = round(market_value - cost_basis, 2) if market_value is not None else None
            pnl = round(raw_pnl * leverage, 2) if raw_pnl is not None else None
            pnl_pct = round(raw_pnl / cost_basis * 100 * leverage, 2) if (raw_pnl is not None and cost_basis) else None

            # Cash positions: value = cost_basis, no price/P&L
            if t == "CASH":
                market_value = cost_basis
                pnl          = 0.0
                pnl_pct      = 0.0
                price        = None

            fair_pe = val.get("pe_median_price")
            to_fair = round((fair_pe - price) / price * 100, 1) if (fair_pe and price) else None

            holdings.append({
                "account":                 row["account"] or "Unknown",
                "ticker":                  t,
                "shares":                  shares,
                "avg_cost":                avg_cost,
                "leverage":                leverage,
                "notes":                   row["notes"],
                "current_price":           price,
                "day_change":              pinfo.get("day_change"),
                "day_change_pct":          pinfo.get("day_change_pct"),
                "cost_basis":              cost_basis,
                "market_value":            market_value,
                "unrealized_pnl":          pnl,
                "return_pct":              pnl_pct,
                "company_name":            val.get("company_name", t),
                "industry":                val.get("industry"),
                "pe_median_price":         fair_pe,
                "current_year_fair_price": val.get("current_year_fair_price"),
                "next_year_fair_price":    val.get("next_year_fair_price"),
                "valuation_status":        val.get("valuation_status"),
                "valuation_diff_pct":      val.get("valuation_diff_pct"),
                "pe_median_5y":            val.get("pe_median_5y"),
                "pct_to_fair_pe":          to_fair,
            })

        total_cost  = sum(h["cost_basis"] for h in holdings)
        total_value = sum(h["market_value"] or h["cost_basis"] for h in holdings)

        # Legacy P&L baseline (kept for backward compatibility)
        pmeta            = meta.get(str(pname), {})
        starting_capital = pmeta.get("starting_capital")
        base             = starting_capital if starting_capital else total_cost
        total_pnl        = round(total_value - base, 2)
        return_pct       = round(total_pnl / base * 100, 2) if base else 0

        total_cash = sum(h["cost_basis"] for h in holdings if h["ticker"] == "CASH")
        cash_pct   = round(total_cash / total_value * 100, 1) if total_value else 0

        sectors: dict[str, float] = {}
        for h in holdings:
            if h["ticker"] == "CASH":
                ind = "Cash"
            else:
                ind = h.get("industry") or "Unknown"
            sectors[ind] = sectors.get(ind, 0) + (h["market_value"] or h["cost_basis"])

        pdeposits = deposits.get(str(pname), [])
        total_deposited = sum(float(d["amount"]) for d in pdeposits)
        # Net gain should not include principal deposits.
        # Priority for net baseline:
        # 1) explicit deposits total (best)
        # 2) starting capital (if no deposits entries)
        # 3) total_cost fallback
        net_base = total_deposited if total_deposited > 0 else (starting_capital if starting_capital else total_cost)
        net_gain = round(total_value - net_base, 2)
        net_return_pct = round(net_gain / net_base * 100, 2) if net_base else 0
        # Warn when deposits cover < 50% of portfolio value — likely missing entries
        deposits_incomplete = bool(pdeposits) and total_value > 0 and (total_deposited / total_value) < 0.5
        mwr = _calc_mwr(pdeposits, total_value)

        portfolios[pname] = {
            "summary": {
                "total_value":      round(total_value, 2),
                "total_cost":       round(total_cost, 2),
                "starting_capital": starting_capital,
                "total_pnl":        total_pnl,
                "return_pct":       return_pct,
                "total_deposited":  round(total_deposited, 2),
                "net_base":         round(net_base, 2) if net_base else 0,
                "net_gain":         net_gain,
                "net_return_pct":   net_return_pct,
                "mwr":              mwr,
                "deposits_incomplete": deposits_incomplete,
                "total_cash":       round(total_cash, 2),
                "cash_pct":         cash_pct,
                "usd_twd_rate":     round(usd_twd_rate, 2) if usd_twd_rate else None,
                "total_value_twd":  round(total_value * usd_twd_rate, 0) if usd_twd_rate else None,
                "sectors":          sorted(
                    [{"name": k, "value": round(v, 2)} for k, v in sectors.items()],
                    key=lambda x: -x["value"],
                ),
            },
            "holdings": holdings,
            "deposits": pdeposits,
        }

    return {"portfolio_names": portfolio_names, "portfolios": portfolios}


@router.get("/csv/report")
def get_csv_portfolio_report(
    portfolio: str = Query(..., description="Portfolio name from /api/portfolio/csv"),
    period: str = Query("daily", pattern="^(daily|weekly|monthly|quarterly)$"),
    db: Session = Depends(get_db),
):
    """
    Daily/weekly diagnosis report for one CSV portfolio.
    - Saves/updates one snapshot per day.
    - Compares current snapshot vs previous day (daily), ~7d (weekly), ~30d (monthly), or ~90d (quarterly).
    """
    return _generate_csv_portfolio_report(portfolio=portfolio, period=period, db=db)


@router.get("/csv/performance-history")
def get_csv_performance_history(
    portfolio: str = Query(..., description="Portfolio name from /api/portfolio/csv"),
    interval: str = Query("monthly", pattern="^(monthly|quarterly)$"),
    limit: int = Query(12, ge=2, le=48),
    db: Session = Depends(get_db),
):
    """
    Return portfolio performance history grouped by month or quarter.
    Uses the latest daily snapshot in each period bucket.
    """
    data = get_csv_portfolio(name=portfolio, db=db)
    if not data.get("portfolios"):
        raise HTTPException(status_code=404, detail=f"Portfolio '{portfolio}' not found")

    target_name = next(iter(data["portfolios"].keys()))
    current = data["portfolios"][target_name]
    summary = current.get("summary", {})
    holdings = current.get("holdings", [])
    today = date.today()
    current_snapshot = _build_daily_snapshot(target_name, summary, holdings, as_of=today)
    snapshots_data = _upsert_daily_snapshot(target_name, current_snapshot)
    snapshots = snapshots_data.get(target_name, [])

    # Keep latest snapshot per month/quarter bucket
    latest_by_bucket: dict[str, dict] = {}
    for snap in snapshots:
        d = snap.get("date")
        if not d:
            continue
        key = _bucket_key_from_iso(d, interval)
        if not key:
            continue
        prev = latest_by_bucket.get(key)
        if (prev is None) or (str(snap.get("date")) > str(prev.get("date"))):
            latest_by_bucket[key] = snap

    bucket_keys = sorted(latest_by_bucket.keys())
    if not bucket_keys:
        return {
            "portfolio": target_name,
            "interval": interval,
            "rows": [],
        }

    selected_keys = bucket_keys[-limit:]
    rows: list[dict] = []
    prev_value: float | None = None
    for key in selected_keys:
        snap = latest_by_bucket[key]
        ssum = snap.get("summary", {})
        total_value = float(ssum.get("total_value") or 0.0)
        period_change = (total_value - prev_value) if prev_value is not None else None
        period_change_pct = ((total_value - prev_value) / prev_value * 100.0) if (prev_value is not None and prev_value > 0) else None
        rows.append({
            "period": key,
            "period_label": _bucket_label(key, interval),
            "snapshot_date": snap.get("date"),
            "total_value": round(total_value, 2),
            "period_change": round(period_change, 2) if period_change is not None else None,
            "period_change_pct": round(period_change_pct, 2) if period_change_pct is not None else None,
            "net_gain": round(float(ssum.get("net_gain") or 0.0), 2),
            "net_return_pct": round(float(ssum.get("net_return_pct") or 0.0), 2),
            "mwr": ssum.get("mwr"),
            "cash_pct": round(float(ssum.get("cash_pct") or 0.0), 2),
            "position_count": int(ssum.get("position_count") or 0),
        })
        prev_value = total_value

    return {
        "portfolio": target_name,
        "interval": interval,
        "rows": rows,
    }


def _generate_csv_portfolio_report(portfolio: str, period: str, db: Session) -> dict:
    data = get_csv_portfolio(name=portfolio, db=db)
    if not data.get("portfolios"):
        raise HTTPException(status_code=404, detail=f"Portfolio '{portfolio}' not found")

    # Resolve actual portfolio key (preserve original casing from CSV).
    target_name = next(iter(data["portfolios"].keys()))
    current = data["portfolios"][target_name]
    summary = current.get("summary", {})
    holdings = current.get("holdings", [])

    # Today's P&L: sum(day_change * shares) across non-CASH holdings
    daily_pnl_items = [
        float(h.get("day_change") or 0) * float(h.get("shares") or 0)
        for h in holdings
        if h.get("ticker") != "CASH" and h.get("day_change") is not None
    ]
    daily_pnl = round(sum(daily_pnl_items), 2) if daily_pnl_items else None
    total_mktval = float(summary.get("total_value") or 0)
    daily_pnl_pct = (
        round(daily_pnl / (total_mktval - daily_pnl) * 100, 2)
        if daily_pnl is not None and total_mktval and (total_mktval - daily_pnl) > 0
        else None
    )

    today = date.today()
    current_snapshot = _build_daily_snapshot(target_name, summary, holdings, as_of=today)
    snapshots_data = _upsert_daily_snapshot(target_name, current_snapshot)
    snapshots = snapshots_data.get(target_name, [])
    baseline = _pick_baseline(snapshots, today, period)

    cur_sum = current_snapshot["summary"]
    cur_map = _aggregate_by_ticker(current_snapshot["holdings"])
    base_map = _aggregate_by_ticker(baseline.get("holdings", [])) if baseline else {}
    cur_tickers = set(cur_map.keys()) - {"CASH"}
    base_tickers = set(base_map.keys()) - {"CASH"}

    new_positions = sorted(cur_tickers - base_tickers)
    removed_positions = sorted(base_tickers - cur_tickers)

    increased = []
    reduced = []
    weight_up = []
    weight_down = []
    for t in sorted(cur_tickers & base_tickers):
        c = cur_map[t]
        b = base_map[t]
        share_diff = round(float(c["shares"]) - float(b["shares"]), 6)
        value_diff = round(float(c["market_value"]) - float(b["market_value"]), 2)
        weight_diff = round(float(c["weight_pct"]) - float(b["weight_pct"]), 2)
        item = {
            "ticker": t,
            "share_diff": share_diff,
            "value_diff": value_diff,
            "weight_diff_pct": weight_diff,
            "current_weight_pct": float(c["weight_pct"]),
            "baseline_weight_pct": float(b["weight_pct"]),
        }
        if abs(share_diff) > 1e-6:
            if share_diff > 0:
                increased.append(item)
            else:
                reduced.append(item)
        if weight_diff > 0:
            weight_up.append(item)
        elif weight_diff < 0:
            weight_down.append(item)

    increased.sort(key=lambda x: x["value_diff"], reverse=True)
    reduced.sort(key=lambda x: x["value_diff"])
    weight_up.sort(key=lambda x: x["weight_diff_pct"], reverse=True)
    weight_down.sort(key=lambda x: x["weight_diff_pct"])

    base_sum = (baseline or {}).get("summary", {})
    base_value = float(base_sum.get("total_value") or 0.0)
    cur_value = float(cur_sum.get("total_value") or 0.0)
    value_change = round(cur_value - base_value, 2) if baseline else None
    value_change_pct = round((cur_value - base_value) / base_value * 100, 2) if (baseline and base_value) else None
    cash_pct_change = (
        round(float(cur_sum.get("cash_pct") or 0.0) - float(base_sum.get("cash_pct") or 0.0), 2)
        if baseline else None
    )
    position_count_change = (
        int(cur_sum.get("position_count") or 0) - int(base_sum.get("position_count") or 0)
        if baseline else None
    )

    # ── Technical indicators (parallel fetch for all non-CASH tickers) ────────
    non_cash_tickers = [t for t in cur_tickers if t != "CASH"]
    technicals = _fetch_technicals_batch(non_cash_tickers)

    volume_anomalies: list[dict] = []
    rsi_overbought:   list[dict] = []
    rsi_oversold:     list[dict] = []
    golden_cross:     list[dict] = []
    death_cross:      list[dict] = []
    sr_near_support:     list[dict] = []
    sr_near_resistance:  list[dict] = []

    for ticker in sorted(technicals):
        tech = technicals[ticker]
        vol_ratio = tech.get("volume_ratio")
        if vol_ratio is not None and vol_ratio >= 2.0:
            volume_anomalies.append({"ticker": ticker, "volume_ratio": vol_ratio, "volume": tech.get("volume")})
        rsi = tech.get("rsi_14")
        if rsi is not None:
            if rsi >= 70:
                rsi_overbought.append({"ticker": ticker, "rsi": rsi})
            elif rsi <= 30:
                rsi_oversold.append({"ticker": ticker, "rsi": rsi})
        ma_cross = tech.get("ma_cross")
        if ma_cross == "golden":
            golden_cross.append({"ticker": ticker, "ma5": tech.get("ma5"), "ma20": tech.get("ma20")})
        elif ma_cross == "death":
            death_cross.append({"ticker": ticker, "ma5": tech.get("ma5"), "ma20": tech.get("ma20")})
        for lvl in tech.get("near_support", []):
            sr_near_support.append({"ticker": ticker, **lvl})
        for lvl in tech.get("near_resistance", []):
            sr_near_resistance.append({"ticker": ticker, **lvl})

    volume_anomalies.sort(key=lambda x: x["volume_ratio"], reverse=True)
    rsi_overbought.sort(key=lambda x: x["rsi"], reverse=True)
    rsi_oversold.sort(key=lambda x: x["rsi"])
    sr_near_support.sort(key=lambda x: x["dist_pct"])
    sr_near_resistance.sort(key=lambda x: x["dist_pct"])

    # ── Per-stock insights for notable movers ─────────────────────────────────
    stock_insights = _build_stock_insights(holdings, technicals, cur_map)

    # ── Risk flags (concentration + technical) ────────────────────────────────
    risk_flags = _build_risk_flags(current_snapshot, baseline)

    # Bearish signals → "bear" level (red in UI)
    for item in death_cross:
        hold = cur_map.get(item["ticker"])
        wt = float(hold.get("weight_pct") or 0) if hold else 0
        level = "medium" if wt >= 5 else "bear"
        risk_flags.append({"level": level, "message": f"↓ {item['ticker']} MA5 跌破 MA20（死亡交叉）{'，持倉 ' + str(wt) + '%' if wt >= 5 else ''}"})

    for item in rsi_overbought:
        hold = cur_map.get(item["ticker"])
        wt = float(hold.get("weight_pct") or 0) if hold else 0
        level = "medium" if wt >= 5 else "bear"
        risk_flags.append({"level": level, "message": f"↓ {item['ticker']} RSI {item['rsi']:.0f} 超買，注意賣壓{'，持倉 ' + str(wt) + '%' if wt >= 5 else ''}"})

    # Bullish signals → "bull" level (cyan/green in UI)
    for item in rsi_oversold:
        risk_flags.append({"level": "bull", "message": f"↑ {item['ticker']} RSI {item['rsi']:.0f} 超賣，可留意反彈機會"})

    for item in golden_cross:
        risk_flags.append({"level": "bull", "message": f"↑ {item['ticker']} MA5 突破 MA20（黃金交叉）"})

    # Neutral attention → "info"
    for item in volume_anomalies:
        risk_flags.append({"level": "info", "message": f"〜 {item['ticker']} 今日成交量為均量 {item['volume_ratio']:.1f}×，留意異常動向"})

    # S/R proximity
    for item in sr_near_support:
        risk_flags.append({"level": "bull", "message": f"↑ {item['ticker']} 接近支撐 ${item['price']} (距 {item['dist_pct']:.1f}%，強度 {'★' * item['strength']})"})

    for item in sr_near_resistance:
        risk_flags.append({"level": "bear", "message": f"↓ {item['ticker']} 接近壓力 ${item['price']} (距 {item['dist_pct']:.1f}%，強度 {'★' * item['strength']})"})

    return {
        "portfolio": target_name,
        "period": period,
        "as_of": today.isoformat(),
        "baseline_date": baseline.get("date") if baseline else None,
        "snapshots_available": len(snapshots),
        "headline": {
            "total_value": round(cur_value, 2),
            "total_cash": round(float(cur_sum.get("total_cash") or 0.0), 2),
            "cash_pct": round(float(cur_sum.get("cash_pct") or 0.0), 2),
            "value_change": value_change,
            "value_change_pct": value_change_pct,
            "cash_pct_change": cash_pct_change,
            "position_count": int(cur_sum.get("position_count") or 0),
            "position_count_change": position_count_change,
            "mwr": cur_sum.get("mwr"),
            "net_gain": round(float(cur_sum.get("net_gain") or 0.0), 2),
            "net_return_pct": round(float(cur_sum.get("net_return_pct") or 0.0), 2),
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
        },
        "position_flow": {
            "new_positions": new_positions[:10],
            "removed_positions": removed_positions[:10],
            "increased_positions": increased[:10],
            "reduced_positions": reduced[:10],
        },
        "weight_rotation": {
            "up": weight_up[:10],
            "down": weight_down[:10],
        },
        "technical_signals": {
            "volume_anomalies":  volume_anomalies[:10],
            "rsi_overbought":    rsi_overbought[:10],
            "rsi_oversold":      rsi_oversold[:10],
            "golden_cross":      golden_cross[:10],
            "death_cross":       death_cross[:10],
            "near_support":      sr_near_support[:10],
            "near_resistance":   sr_near_resistance[:10],
            "per_holding":       technicals,
        },
        "risk_flags": risk_flags,
        "stock_insights": stock_insights,
    }


def _build_stock_insights(holdings: list, technicals: dict, cur_map: dict) -> list[dict]:
    """Generate per-stock analysis for notable movers, combining technical signals."""
    holding_map = {h["ticker"]: h for h in holdings if h.get("ticker") and h["ticker"] != "CASH"}
    insights = []

    for ticker, tech in technicals.items():
        h = holding_map.get(ticker, {})
        day_change_pct = h.get("day_change_pct")
        weight_pct = float((cur_map.get(ticker) or {}).get("weight_pct") or 0)
        rsi = tech.get("rsi_14")
        vol_ratio = tech.get("volume_ratio")
        price_vs_ma20 = tech.get("price_vs_ma20_pct")
        ma_cross = tech.get("ma_cross")
        supports = tech.get("near_support", [])
        resistances = tech.get("near_resistance", [])
        price = tech.get("price")

        # Determine if this stock is "notable" enough to include
        is_notable = (
            (day_change_pct is not None and abs(day_change_pct) >= 3)
            or (rsi is not None and (rsi >= 70 or rsi <= 35))
            or (vol_ratio is not None and vol_ratio >= 2.0)
            or (price_vs_ma20 is not None and (price_vs_ma20 >= 15 or price_vs_ma20 <= -10))
            or ma_cross in ("golden", "death")
            or any(s["dist_pct"] <= 2 for s in supports)
            or any(r["dist_pct"] <= 2 for r in resistances)
        )
        if not is_notable:
            continue

        signals = []
        direction_score = 0  # positive=bullish, negative=bearish

        # RSI
        if rsi is not None:
            if rsi >= 85:
                signals.append({"tag": f"RSI {rsi:.0f} 極端超買", "tone": "red"})
                direction_score -= 2
            elif rsi >= 70:
                signals.append({"tag": f"RSI {rsi:.0f} 超買", "tone": "orange"})
                direction_score -= 1
            elif rsi <= 25:
                signals.append({"tag": f"RSI {rsi:.0f} 極端超賣", "tone": "green"})
                direction_score += 2
            elif rsi <= 35:
                signals.append({"tag": f"RSI {rsi:.0f} 超賣", "tone": "green"})
                direction_score += 1

        # MA cross
        if ma_cross == "golden":
            signals.append({"tag": "黃金交叉 MA5>MA20", "tone": "cyan"})
            direction_score += 2
        elif ma_cross == "death":
            signals.append({"tag": "死亡交叉 MA5<MA20", "tone": "red"})
            direction_score -= 2

        # Price vs MA20 stretch
        if price_vs_ma20 is not None:
            if price_vs_ma20 >= 20:
                signals.append({"tag": f"偏離MA20 +{price_vs_ma20:.1f}%", "tone": "orange"})
                direction_score -= 1
            elif price_vs_ma20 >= 12:
                signals.append({"tag": f"偏離MA20 +{price_vs_ma20:.1f}%", "tone": "yellow"})
            elif price_vs_ma20 <= -10:
                signals.append({"tag": f"偏離MA20 {price_vs_ma20:.1f}%", "tone": "yellow"})

        # Support / Resistance
        close_supports = [s for s in supports if s["dist_pct"] <= 3]
        close_resistances = [r for r in resistances if r["dist_pct"] <= 3]
        if close_supports:
            s = min(close_supports, key=lambda x: x["dist_pct"])
            signals.append({"tag": f"近支撐 ${s['price']} ↓{s['dist_pct']:.1f}%", "tone": "green"})
            direction_score += 1
        if close_resistances:
            r = min(close_resistances, key=lambda x: x["dist_pct"])
            signals.append({"tag": f"近壓力 ${r['price']} ↑{r['dist_pct']:.1f}%", "tone": "orange"})
            direction_score -= 1

        # Volume
        if vol_ratio is not None and vol_ratio >= 2.0:
            signals.append({"tag": f"量能 {vol_ratio:.1f}× 均量", "tone": "yellow"})

        # Day change
        if day_change_pct is not None and abs(day_change_pct) >= 3:
            tone = "green" if day_change_pct > 0 else "red"
            signals.append({"tag": f"今日 {'+' if day_change_pct > 0 else ''}{day_change_pct:.1f}%", "tone": tone})
            direction_score += 1 if day_change_pct > 0 else -1

        # Build suggestion text
        suggestion = _compose_insight_suggestion(ticker, rsi, ma_cross, price_vs_ma20, close_supports, close_resistances, vol_ratio, day_change_pct, weight_pct)
        direction = "bull" if direction_score > 0 else "bear" if direction_score < 0 else "neutral"

        insights.append({
            "ticker": ticker,
            "price": price,
            "day_change": h.get("day_change"),
            "day_change_pct": day_change_pct,
            "weight_pct": round(weight_pct, 2),
            "rsi": rsi,
            "vol_ratio": vol_ratio,
            "price_vs_ma20": price_vs_ma20,
            "signals": signals,
            "direction": direction,
            "suggestion": suggestion,
        })

    # Sort: biggest movers first (by abs day change), then by rsi extremeness
    insights.sort(key=lambda x: (
        abs(x.get("day_change_pct") or 0) * -1,
        abs((x.get("rsi") or 50) - 50) * -1,
    ))
    return insights[:12]


def _compose_insight_suggestion(
    ticker: str, rsi, ma_cross, price_vs_ma20, close_supports, close_resistances, vol_ratio, day_change_pct, weight_pct
) -> str:
    parts = []

    # RSI analysis
    if rsi is not None:
        if rsi >= 85:
            parts.append(f"RSI 高達 {rsi:.0f}，極度超買，短期賣壓風險高，建議評估部分獲利了結或設停利點")
        elif rsi >= 70:
            parts.append(f"RSI {rsi:.0f} 進入超買區，追高風險升高，可持倉觀望但不宜加碼")
        elif rsi <= 25:
            parts.append(f"RSI 僅 {rsi:.0f}，極度超賣，技術面有反彈空間，可分批試探性建倉")
        elif rsi <= 35:
            parts.append(f"RSI {rsi:.0f} 接近超賣，若基本面未變可留意反彈機會")

    # MA cross
    if ma_cross == "golden":
        parts.append("MA5 上穿 MA20 形成黃金交叉，短期動能偏多，可配合量能判斷入場時機")
    elif ma_cross == "death":
        parts.append("MA5 跌破 MA20 形成死亡交叉，趨勢偏空，建議減倉控制風險")

    # Price vs MA20
    if price_vs_ma20 is not None and price_vs_ma20 >= 20:
        parts.append(f"股價已大幅偏離 MA20 達 {price_vs_ma20:.1f}%，短期均值回歸風險不可忽視")
    elif price_vs_ma20 is not None and price_vs_ma20 <= -10:
        parts.append(f"股價低於 MA20 {abs(price_vs_ma20):.1f}%，下跌動能較強，等待企穩訊號再介入")

    # Support
    if close_supports:
        s = min(close_supports, key=lambda x: x["dist_pct"])
        stars = "★" * s["strength"]
        parts.append(f"當前價格距支撐 ${s['price']} 僅 {s['dist_pct']:.1f}%（強度 {stars}），守住則可考慮加碼；跌破需留意止損")

    # Resistance
    if close_resistances:
        r = min(close_resistances, key=lambda x: x["dist_pct"])
        stars = "★" * r["strength"]
        parts.append(f"上方壓力 ${r['price']} 距現價 {r['dist_pct']:.1f}%（強度 {stars}），突破前建議控制倉位")

    # Volume
    if vol_ratio is not None and vol_ratio >= 2.0:
        parts.append(f"今日成交量為 20 日均量的 {vol_ratio:.1f} 倍，需確認量能是否配合突破或跌破方向")

    # Day change
    if day_change_pct is not None:
        if day_change_pct <= -5:
            parts.append(f"今日急跌 {day_change_pct:.1f}%，留意是否有基本面消息觸發，急跌不宜盲目追空或抄底")
        elif day_change_pct >= 5:
            parts.append(f"今日急漲 {day_change_pct:.1f}%，追高需謹慎，可等待回測確認支撐後再介入")

    # Weight context
    if weight_pct >= 5:
        parts.append(f"持倉佔比 {weight_pct:.1f}%，屬重倉股，上述訊號對整體投組影響較大，建議優先關注")

    return "。".join(parts) + ("。" if parts else "")


@router.put("/csv/meta")
def update_csv_meta(portfolio: str = Query(...), starting_capital: float = Query(...)):
    """Set the starting capital for a portfolio (used for gain/return calculation)."""
    meta = _read_meta()
    meta.setdefault(portfolio, {})["starting_capital"] = starting_capital
    _write_meta(meta)
    return {"portfolio": portfolio, "starting_capital": starting_capital}


# ── Deposit endpoints ─────────────────────────────────────────────────────────

class DepositCreate(BaseModel):
    portfolio: str
    date: str        # "YYYY-MM-DD"
    amount: float


@router.get("/csv/deposits")
def get_deposits(portfolio: str = Query(...)):
    """Return deposit history for a portfolio, sorted by date."""
    data = _read_deposits()
    entries = sorted(data.get(portfolio, []), key=lambda d: d["date"])
    return {"portfolio": portfolio, "deposits": entries}


@router.post("/csv/deposits", status_code=201)
def add_deposit(body: DepositCreate):
    """Add a deposit entry for a portfolio."""
    from datetime import date as _date
    try:
        _date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    if body.amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be positive")
    data = _read_deposits()
    data.setdefault(body.portfolio, []).append({"date": body.date, "amount": body.amount})
    data[body.portfolio].sort(key=lambda d: d["date"])
    _write_deposits(data)
    return {"portfolio": body.portfolio, "deposits": data[body.portfolio]}


@router.delete("/csv/deposits")
def delete_deposit(portfolio: str = Query(...), date: str = Query(...), amount: float = Query(...)):
    """Remove a deposit entry by (portfolio, date, amount)."""
    data = _read_deposits()
    entries = data.get(portfolio, [])
    new_entries = [e for e in entries if not (e["date"] == date and e["amount"] == amount)]
    if len(new_entries) == len(entries):
        raise HTTPException(status_code=404, detail="Deposit not found")
    data[portfolio] = new_entries
    _write_deposits(data)
    return {"portfolio": portfolio, "deposits": data[portfolio]}


@router.post("/csv/sync")
def sync_csv_portfolio():
    """Re-fetch portfolio data from Google Sheets and overwrite local portfolio.csv."""
    try:
        df = _sync_from_sheets()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Sync failed: {exc}")
    return {"synced_rows": len(df), "portfolio_names": df["portfolio"].unique().tolist()}


@router.post("/csv/holding", status_code=201)
def add_csv_holding(body: CsvHoldingCreate):
    """Add a new row to portfolio.csv."""
    df = _read_csv()
    ticker = body.ticker.upper().strip()
    mask = (df["portfolio"] == body.portfolio) & (df["ticker"] == ticker)
    if mask.any():
        raise HTTPException(status_code=409, detail=f"{ticker} already exists in {body.portfolio}")
    new_row = pd.DataFrame([{
        "portfolio": body.portfolio,
        "account":   body.account,
        "ticker":    ticker,
        "shares":    body.shares,
        "avg_cost":  body.avg_cost,
        "leverage":  1,
        "notes":     body.notes,
    }])
    df = pd.concat([df, new_row], ignore_index=True)
    _persist_csv(df)
    return {"ok": True, "ticker": ticker}


@router.put("/csv/holding")
def update_csv_holding(body: CsvHoldingUpdate):
    """Update shares / avg_cost / notes for an existing row in portfolio.csv."""
    df = _read_csv()
    ticker = body.ticker.upper().strip()
    mask = (df["portfolio"] == body.portfolio) & (df["account"] == body.account) & (df["ticker"] == ticker)
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"{ticker} not found in {body.portfolio}/{body.account}")
    if body.shares   is not None: df.loc[mask, "shares"]   = body.shares
    if body.avg_cost is not None: df.loc[mask, "avg_cost"] = body.avg_cost
    if body.notes    is not None: df.loc[mask, "notes"]    = body.notes
    _persist_csv(df)
    return {"ok": True, "ticker": ticker}


@router.delete("/csv/holding", status_code=204)
def delete_csv_holding(portfolio: str = Query(...), account: str = Query(...), ticker: str = Query(...)):
    """Remove a row from portfolio.csv by (portfolio, account, ticker)."""
    df = _read_csv()
    ticker = ticker.upper().strip()
    mask = (df["portfolio"] == portfolio) & (df["account"] == account) & (df["ticker"] == ticker)
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"{ticker} not found in {portfolio}/{account}")
    df = df[~mask]
    _persist_csv(df)


@router.get("/gsheet")
def get_gsheet_portfolio(
    portfolio: str | None = Query(None),
    gsheet_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Read holdings from Google Sheet and enrich with DB valuation data.
    Optional ?portfolio=mom to read Mom's portfolio sheet.
    Optional ?gsheet_id=<ID> to read an arbitrary public Google Sheet directly.
    """
    if not gsheet_id:
        if portfolio == "mom":
            gsheet_id = settings.GSHEET_MOM_ID
            if not gsheet_id:
                raise HTTPException(status_code=502, detail="GSHEET_MOM_ID is not set in backend/.env")
        else:
            gsheet_id = settings.GSHEET_ID
            if not gsheet_id:
                raise HTTPException(status_code=502, detail="GSHEET_ID is not set in backend/.env")
    try:
        positions, total_val, overall_return = _load_gsheet(gsheet_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to load Google Sheet: {exc}")

    # Collect real tickers (skip eToro aggregate / CASH)
    real_tickers = [p["ticker"] for p in positions if p["ticker"] not in _SKIP]
    vals = _get_valuations(real_tickers, db)

    # Sector breakdown from DB; fall back to yfinance name
    sector_map: dict[str, float] = {}
    enriched = []
    for p in positions:
        t   = p["ticker"]
        val = vals.get(t.upper(), {})
        ind = val.get("industry") or ("eToro (Mixed)" if t == "eToro" else None)
        sector_map[ind or "Unknown"] = sector_map.get(ind or "Unknown", 0) + p["value"]

        enriched.append({
            **p,
            "company_name":            val.get("company_name", t),
            "industry":                ind,
            "pe_median_price":         val.get("pe_median_price"),
            "current_year_fair_price": val.get("current_year_fair_price"),
            "next_year_fair_price":    val.get("next_year_fair_price"),
            "valuation_status":        val.get("valuation_status"),
            "valuation_diff_pct":      val.get("valuation_diff_pct"),
            "pe_median_5y":            val.get("pe_median_5y"),
        })

    sectors = sorted(
        [{"name": k, "value": round(v, 2)} for k, v in sector_map.items()],
        key=lambda x: -x["value"],
    )

    return {
        "total_value":     total_val,
        "overall_return":  overall_return,
        "sectors":         sectors,
        "holdings":        enriched,
    }


@router.get("")
def list_portfolios(db: Session = Depends(get_db)):
    portfolios = db.query(Portfolio).order_by(Portfolio.created_at.desc()).all()
    result = []
    for p in portfolios:
        tickers = list({h.ticker.upper() for h in p.holdings})
        result.append({
            "id":            p.id,
            "name":          p.name,
            "description":   p.description,
            "created_at":    str(p.created_at)[:10],
            "holding_count": len(p.holdings),
            "tickers":       sorted(tickers),
        })
    return result


@router.post("", status_code=201)
def create_portfolio(body: PortfolioCreate, db: Session = Depends(get_db)):
    p = Portfolio(name=body.name.strip(), description=body.description.strip())
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "name": p.name, "description": p.description}


@router.get("/{pid}")
def get_portfolio(pid: int, db: Session = Depends(get_db)):
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    enriched = _enrich(p.holdings, db)
    summary  = _portfolio_summary(enriched)
    return {
        "id":          p.id,
        "name":        p.name,
        "description": p.description,
        "created_at":  str(p.created_at)[:10],
        "summary":     summary,
        "holdings":    enriched,
    }


@router.put("/{pid}")
def update_portfolio(pid: int, body: PortfolioUpdate, db: Session = Depends(get_db)):
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if body.name is not None:
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description.strip()
    db.commit()
    return {"id": p.id, "name": p.name, "description": p.description}


@router.delete("/{pid}", status_code=204)
def delete_portfolio(pid: int, db: Session = Depends(get_db)):
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    db.delete(p)
    db.commit()


@router.post("/{pid}/holdings", status_code=201)
def add_holding(pid: int, body: HoldingCreate, db: Session = Depends(get_db)):
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    h = Holding(
        portfolio_id=pid,
        ticker=body.ticker.upper().strip(),
        shares=body.shares,
        avg_cost=body.avg_cost,
        notes=body.notes,
        added_date=date.today(),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return {"id": h.id, "ticker": h.ticker, "shares": h.shares, "avg_cost": h.avg_cost}


@router.put("/{pid}/holdings/{hid}")
def update_holding(pid: int, hid: int, body: HoldingUpdate, db: Session = Depends(get_db)):
    h = db.query(Holding).filter(Holding.id == hid, Holding.portfolio_id == pid).first()
    if not h:
        raise HTTPException(status_code=404, detail="Holding not found")
    if body.shares is not None:
        h.shares = body.shares
    if body.avg_cost is not None:
        h.avg_cost = body.avg_cost
    if body.notes is not None:
        h.notes = body.notes
    db.commit()
    return {"id": h.id, "ticker": h.ticker, "shares": h.shares, "avg_cost": h.avg_cost}


@router.delete("/{pid}/holdings/{hid}", status_code=204)
def delete_holding(pid: int, hid: int, db: Session = Depends(get_db)):
    h = db.query(Holding).filter(Holding.id == hid, Holding.portfolio_id == pid).first()
    if not h:
        raise HTTPException(status_code=404, detail="Holding not found")
    db.delete(h)
    db.commit()


# ── DB-backed historical snapshot queries ─────────────────────────────────────

@router.post("/csv/migrate-to-db")
def migrate_json_to_db():
    """
    One-time migration: import all existing JSON data (snapshots, meta, deposits) into DB.
    Safe to run multiple times — uses upsert logic.
    """
    results: dict = {"snapshots": 0, "portfolios_meta": 0, "deposit_entries": 0, "errors": []}

    # ── Snapshots ──────────────────────────────────────────────────────────────
    if _REPORT_SNAPSHOTS_PATH.exists():
        try:
            raw = json.loads(_REPORT_SNAPSHOTS_PATH.read_text())
            for portfolio_name, snap_list in raw.items():
                for snap in snap_list:
                    snap.setdefault("portfolio", portfolio_name)
                    try:
                        _upsert_db_snapshot(snap)
                        results["snapshots"] += 1
                    except Exception as e:
                        results["errors"].append(f"snapshot {portfolio_name} {snap.get('date')}: {e}")
        except Exception as e:
            results["errors"].append(f"snapshots file: {e}")

    # ── Meta ───────────────────────────────────────────────────────────────────
    if _META_PATH.exists():
        try:
            meta = json.loads(_META_PATH.read_text())
            _write_meta(meta)   # already mirrors to DB
            results["portfolios_meta"] = len(meta)
        except Exception as e:
            results["errors"].append(f"meta file: {e}")

    # ── Deposits ───────────────────────────────────────────────────────────────
    if _DEPOSITS_PATH.exists():
        try:
            deposits = json.loads(_DEPOSITS_PATH.read_text())
            _write_deposits(deposits)   # already mirrors to DB
            results["deposit_entries"] = sum(len(v) for v in deposits.values())
        except Exception as e:
            results["errors"].append(f"deposits file: {e}")

    return {"status": "done", **results}


@router.get("/csv/db-history")
def get_db_history(
    name: str = Query(..., description="Portfolio name"),
    limit: int = Query(365, ge=1, le=3650, description="Max number of daily snapshots to return"),
    db: Session = Depends(get_db),
):
    """
    Return historical daily portfolio snapshots stored in the DB.
    Each item has summary stats + per-holding breakdown.
    Sorted oldest → newest.
    """
    snapshots = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.portfolio_name == name)
        .order_by(PortfolioSnapshot.snapshot_date.desc())
        .limit(limit)
        .all()
    )
    if not snapshots:
        raise HTTPException(status_code=404, detail=f"No DB history found for portfolio '{name}'")

    snapshots = list(reversed(snapshots))  # oldest first
    rows = []
    for ps in snapshots:
        rows.append({
            "date":           ps.snapshot_date.isoformat(),
            "total_value":    ps.total_value,
            "total_cash":     ps.total_cash,
            "cash_pct":       ps.cash_pct,
            "net_gain":       ps.net_gain,
            "net_return_pct": ps.net_return_pct,
            "mwr":            ps.mwr,
            "position_count": ps.position_count,
            "holdings": [
                {
                    "ticker":       h.ticker,
                    "account":      h.account,
                    "shares":       h.shares,
                    "avg_cost":     h.avg_cost,
                    "market_value": h.market_value,
                    "weight_pct":   h.weight_pct,
                    "return_pct":   h.return_pct,
                }
                for h in ps.holdings
            ],
        })

    return {"portfolio": name, "count": len(rows), "snapshots": rows}


@router.get("/csv/db-history/ticker")
def get_ticker_history(
    name: str = Query(..., description="Portfolio name"),
    ticker: str = Query(..., description="Ticker symbol"),
    limit: int = Query(365, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    """
    Return the daily history for a single ticker within a portfolio:
    date, shares, market_value, weight_pct, return_pct.
    """
    ticker = ticker.upper()
    rows = (
        db.query(PortfolioSnapshot.snapshot_date, HoldingSnapshot)
        .join(HoldingSnapshot, HoldingSnapshot.snapshot_id == PortfolioSnapshot.id)
        .filter(
            PortfolioSnapshot.portfolio_name == name,
            HoldingSnapshot.ticker == ticker,
        )
        .order_by(PortfolioSnapshot.snapshot_date.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No history for ticker '{ticker}' in portfolio '{name}'",
        )

    rows = list(reversed(rows))
    return {
        "portfolio": name,
        "ticker": ticker,
        "count": len(rows),
        "history": [
            {
                "date":         snap_date.isoformat(),
                "shares":       h.shares,
                "avg_cost":     h.avg_cost,
                "market_value": h.market_value,
                "weight_pct":   h.weight_pct,
                "return_pct":   h.return_pct,
            }
            for snap_date, h in rows
        ],
    }
