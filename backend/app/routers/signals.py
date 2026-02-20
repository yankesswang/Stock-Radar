"""
Signal screener: scan all DB tickers for technical indicator signals
and pair with latest valuation data.
Results are cached in-memory for 5 minutes to avoid hammering yfinance.
"""
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
import yfinance as yf

from ..database import get_db
from ..models import Stock, Industry, Valuation

router = APIRouter()

# ── Simple TTL cache ─────────────────────────────────────────────────────────
_CACHE: dict = {"data": None, "ts": 0}
CACHE_TTL = 300  # 5 minutes


def _safe(val):
    if val is None:
        return None
    try:
        if math.isnan(val) or math.isinf(val):
            return None
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None


def _compute_last_bars(ticker: str, n: int = 3) -> list[dict] | None:
    """Fetch ~3 months of daily data and return the last n bars with indicators."""
    try:
        df = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=True)
        if df.empty or len(df) < 30:
            return None
        df = df.dropna(subset=["Open", "High", "Low", "Close"])

        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]

        # MACD (12,26,9)
        ema12       = close.ewm(span=12, adjust=False).mean()
        ema26       = close.ewm(span=26, adjust=False).mean()
        macd_line   = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist   = macd_line - signal_line

        # RSI (14)
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
        rsi   = 100 - (100 / (1 + gain / (loss + 1e-9)))

        # KDJ (9,3,3)
        low9  = low.rolling(9).min()
        high9 = high.rolling(9).max()
        rsv   = (close - low9) / (high9 - low9 + 1e-9) * 100
        k     = rsv.ewm(com=2, adjust=False).mean()
        d     = k.ewm(com=2, adjust=False).mean()
        j     = 3 * k - 2 * d

        # Bollinger Bands (20,2)
        bb_mid   = close.rolling(20).mean()
        bb_upper = bb_mid + 2 * close.rolling(20).std(ddof=0)
        bb_lower = bb_mid - 2 * close.rolling(20).std(ddof=0)

        # Williams %R (14)
        willr = (high.rolling(14).max() - close) / (high.rolling(14).max() - low.rolling(14).min() + 1e-9) * -100

        bars = []
        for i in range(max(0, len(df) - n), len(df)):
            bars.append({
                "close":    _safe(close.iloc[i]),
                "macd":     _safe(macd_line.iloc[i]),
                "signal":   _safe(signal_line.iloc[i]),
                "hist":     _safe(macd_hist.iloc[i]),
                "rsi":      _safe(rsi.iloc[i]),
                "k":        _safe(k.iloc[i]),
                "d":        _safe(d.iloc[i]),
                "j":        _safe(j.iloc[i]),
                "bb_upper": _safe(bb_upper.iloc[i]),
                "bb_lower": _safe(bb_lower.iloc[i]),
                "willr":    _safe(willr.iloc[i]),
            })
        return bars
    except Exception:
        return None


def _detect_signals(bars: list[dict]) -> list[dict]:
    if len(bars) < 2:
        return []
    last = bars[-1]
    prev = bars[-2]
    signals = []

    def add(indicator, stype, severity, value, desc):
        signals.append({"indicator": indicator, "type": stype,
                         "severity": severity, "value": value, "desc": desc})

    # RSI
    r = last.get("rsi")
    if r is not None:
        if r >= 70:   add("RSI", "Overbought",  "bearish", f"{r:.1f}", f"RSI {r:.1f} above 70")
        elif r <= 30: add("RSI", "Oversold",     "bullish", f"{r:.1f}", f"RSI {r:.1f} below 30")

    # Williams %R
    w = last.get("willr")
    if w is not None:
        if w >= -20:   add("Williams %R", "Overbought", "bearish", f"{w:.1f}", f"%R {w:.1f} above -20")
        elif w <= -80: add("Williams %R", "Oversold",   "bullish", f"{w:.1f}", f"%R {w:.1f} below -80")

    # KDJ J-line
    jv = last.get("j")
    if jv is not None:
        if jv >= 80:   add("KDJ", "Overbought", "bearish", f"J={jv:.1f}", f"J-line {jv:.1f} above 80")
        elif jv <= 20: add("KDJ", "Oversold",   "bullish", f"J={jv:.1f}", f"J-line {jv:.1f} below 20")

    # KDJ KD crossover
    lk, ld, pk, pd_ = last.get("k"), last.get("d"), prev.get("k"), prev.get("d")
    if all(v is not None for v in [lk, ld, pk, pd_]):
        if pk <= pd_ and lk > ld: add("KDJ", "Golden Cross", "bullish", f"K={lk:.1f}", "K crossed above D")
        elif pk >= pd_ and lk < ld: add("KDJ", "Death Cross", "bearish", f"K={lk:.1f}", "K crossed below D")

    # MACD crossover
    lm, ls, pm, ps = last.get("macd"), last.get("signal"), prev.get("macd"), prev.get("signal")
    if all(v is not None for v in [lm, ls, pm, ps]):
        if pm <= ps and lm > ls: add("MACD", "Bullish Crossover", "bullish", f"{lm:.3f}", "MACD crossed above signal")
        elif pm >= ps and lm < ls: add("MACD", "Bearish Crossover", "bearish", f"{lm:.3f}", "MACD crossed below signal")

    # MACD histogram flip
    lh, ph = last.get("hist"), prev.get("hist")
    if lh is not None and ph is not None:
        if ph < 0 and lh >= 0: add("MACD", "Histogram Flip ↑", "bullish", f"{lh:.3f}", "Histogram turned positive")
        elif ph > 0 and lh <= 0: add("MACD", "Histogram Flip ↓", "bearish", f"{lh:.3f}", "Histogram turned negative")

    # Bollinger Bands
    bu, bl, cl = last.get("bb_upper"), last.get("bb_lower"), last.get("close")
    if bu is not None and cl is not None:
        if cl >= bu: add("BB", "Upper Band Touch", "bearish", f"{cl:.2f}", f"Price ≥ upper band ({bu:.2f})")
        elif cl <= bl: add("BB", "Lower Band Touch", "bullish", f"{cl:.2f}", f"Price ≤ lower band ({bl:.2f})")

    return signals


def _scan_all(stocks_meta: dict) -> list[dict]:
    results = []

    def process(ticker):
        bars = _compute_last_bars(ticker, n=3)
        if not bars:
            return None
        sigs = _detect_signals(bars)
        if not sigs:
            return None
        entry = dict(stocks_meta[ticker])
        entry["signals"]     = sigs
        entry["latest_close"] = bars[-1].get("close")
        return entry

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(process, t): t for t in stocks_meta}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: (-len(x["signals"]), x["ticker"]))
    return results


@router.get("/watchlist")
def get_signal_watchlist(db: Session = Depends(get_db)):
    # Serve from cache if fresh
    if _CACHE["data"] is not None and (time.time() - _CACHE["ts"]) < CACHE_TTL:
        return _CACHE["data"]

    # Build stocks metadata (ticker → info + latest valuation)
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
        .all()
    )

    stocks_meta = {}
    for stock, industry, val in rows:
        stocks_meta[stock.ticker] = {
            "ticker":       stock.ticker,
            "company_name": stock.company_name,
            "industry":     industry.name,
            "valuation": {
                "current_price":          val.current_price,
                "pe_median_price":        val.pe_median_price,
                "current_year_fair_price": val.current_year_fair_price,
                "next_year_fair_price":   val.next_year_fair_price,
                "valuation_status":       val.valuation_status,
                "valuation_diff_pct":     val.valuation_diff_pct,
                "pe_median_5y":           val.pe_median_5y,
                "estimated_pe":           val.estimated_pe,
            },
        }

    data = _scan_all(stocks_meta)
    _CACHE["data"] = data
    _CACHE["ts"]   = time.time()
    return data


@router.delete("/watchlist/cache")
def clear_cache():
    _CACHE["data"] = None
    _CACHE["ts"]   = 0
    return {"cleared": True}
