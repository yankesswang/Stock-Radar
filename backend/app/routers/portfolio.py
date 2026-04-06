"""
Portfolio management: CRUD for portfolios + holdings.
Each GET /portfolio/{id} enriches holdings with live prices (yfinance)
and valuation data from the DB.

Also provides GET /portfolio/gsheet which reads holdings directly from
a public Google Sheet (CSV export) and enriches with DB valuation data.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import io
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
from ..database import get_db
from ..models import Portfolio, Holding, Stock, Industry, Valuation

router = APIRouter()

# ── Google Sheet config (ID loaded from backend/.env) ────────────────────────
_SKIP = {"CASH", "eToro"}


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
    """Return {portfolio_name: {starting_capital: float, ...}}"""
    import json
    if _META_PATH.exists():
        try:
            return json.loads(_META_PATH.read_text())
        except Exception:
            pass
    return {}


def _write_meta(meta: dict) -> None:
    import json
    _META_PATH.write_text(json.dumps(meta, indent=2))


# ── Deposits (for MWR / XIRR) ─────────────────────────────────────────────────

_DEPOSITS_PATH = settings.DATA_DIR / "portfolio_deposits.json"


def _read_deposits() -> dict:
    """Return {portfolio_name: [{"date": "YYYY-MM-DD", "amount": float}, ...]}"""
    import json
    if _DEPOSITS_PATH.exists():
        try:
            return json.loads(_DEPOSITS_PATH.read_text())
        except Exception:
            pass
    return {}


def _write_deposits(data: dict) -> None:
    import json
    _DEPOSITS_PATH.write_text(json.dumps(data, indent=2))


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

def _calc_beta(ticker: str, market_returns: "pd.Series") -> float | None:
    """Compute beta of a single ticker against pre-fetched market returns."""
    try:
        hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        if len(hist) < 30:
            return None
        r = hist["Close"].pct_change().dropna()
        aligned = r.align(market_returns, join="inner")[0]
        mkt     = market_returns.loc[aligned.index]
        if len(aligned) < 30:
            return None
        cov = float(aligned.cov(mkt))
        var = float(mkt.var())
        return round(cov / var, 3) if var else None
    except Exception:
        return None


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/csv/beta")
def get_portfolio_beta(
    name: str | None = Query(None, description="Portfolio name; omit for all"),
):
    """
    Calculate weighted portfolio beta vs S&P 500 (SPY, 1-year daily returns).
    Returns per-holding betas and the value-weighted portfolio beta.
    """
    try:
        df = _read_csv()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

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

    # Exclude CASH and leveraged ETFs that aren't real equities if desired
    df = df[df["ticker"] != "CASH"]
    tickers = df["ticker"].unique().tolist()

    # Fetch market (SPY) returns once
    try:
        spy_hist = yf.Ticker("SPY").history(period="1y", auto_adjust=True)
        market_returns = spy_hist["Close"].pct_change().dropna()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch SPY data: {exc}")

    # Fetch current prices for weighting
    prices = _fetch_prices(tickers)

    # Calculate beta for each ticker in parallel
    betas: dict[str, float | None] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_calc_beta, t, market_returns): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            betas[t] = fut.result()

    # Build per-portfolio weighted beta
    results: dict[str, dict] = {}
    for pname, group in df.groupby("portfolio", sort=False):
        holdings_out = []
        total_val = 0.0
        weighted_sum = 0.0

        for _, row in group.iterrows():
            t       = row["ticker"]
            price   = prices.get(t) or float(row["avg_cost"])
            mkt_val = float(row["shares"]) * price
            beta    = betas.get(t)
            total_val    += mkt_val
            if beta is not None:
                weighted_sum += beta * mkt_val
            holdings_out.append({"ticker": t, "beta": beta, "market_value": round(mkt_val, 2)})

        portfolio_beta = round(weighted_sum / total_val, 3) if total_val else None
        results[pname] = {
            "portfolio_beta": portfolio_beta,
            "benchmark":      "SPY",
            "period":         "1y",
            "holdings":       holdings_out,
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

        # Use starting_capital as the baseline for gain/return if configured
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
    _write_csv(df)
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
    _write_csv(df)
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
    _write_csv(df)


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
