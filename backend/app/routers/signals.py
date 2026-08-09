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
import pandas as pd
import yfinance as yf

from ..database import get_db
from ..models import Stock, Industry, Valuation
from ..indicators import compute_indicator_frame
from .chart import _fetch_yahoo_chart_df

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
    """Fetch ~1 year of daily data and return the last n bars with indicators.

    A year (not 3 months) because ADX/Ichimoku/Aroon need the longer lookback,
    and 52-week extremes are part of the screen.
    """
    try:
        df = pd.DataFrame()
        try:
            df = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=True)
        except Exception:
            pass
        # yfinance gets rate-limited routinely; fall back to the raw Yahoo chart
        # API the same way the chart endpoint does, or the whole scan comes back
        # empty and looks like "no signals".
        if df.empty:
            df = _fetch_yahoo_chart_df(ticker, "1y", "1d")
        if df.empty or len(df) < 60:
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

        # MA50 / MA200 for golden & death crosses
        ma50 = close.rolling(50).mean()
        ma200 = close.rolling(200).mean()

        # Advanced set (ADX, SuperTrend, TTM Squeeze, Stoch RSI, MFI, CMF, …)
        try:
            adv = compute_indicator_frame(df)
        except Exception:
            adv = pd.DataFrame(index=df.index)

        adv_fields = ("adx", "di_plus", "di_minus", "st", "st_dir", "sqz_on", "sqz_mom",
                      "stochrsi_k", "mfi", "cmf", "atrp", "rvol", "psar", "cci",
                      "ich_a", "ich_b", "bb_pctb")

        hi52 = float(high.max())
        lo52 = float(low.min())

        bars = []
        for i in range(max(0, len(df) - n), len(df)):
            bar = {
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
                "ma50":     _safe(ma50.iloc[i]),
                "ma200":    _safe(ma200.iloc[i]),
                "high_52w": _safe(hi52),
                "low_52w":  _safe(lo52),
            }
            for f in adv_fields:
                bar[f] = _safe(adv[f].iloc[i]) if f in adv else None
            bars.append(bar)
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
        elif bl is not None and cl <= bl: add("BB", "Lower Band Touch", "bullish", f"{cl:.2f}", f"Price ≤ lower band ({bl:.2f})")

    # ── Advanced indicators ──────────────────────────────────────────────────
    def both(*keys):
        return all(last.get(k) is not None and prev.get(k) is not None for k in keys)

    # SuperTrend flip — the ATR trailing-stop regime change
    if both("st_dir"):
        if prev["st_dir"] < 0 and last["st_dir"] > 0:
            add("SuperTrend", "Flip ↑", "bullish", f"{last.get('st'):.2f}", "SuperTrend turned bullish")
        elif prev["st_dir"] > 0 and last["st_dir"] < 0:
            add("SuperTrend", "Flip ↓", "bearish", f"{last.get('st'):.2f}", "SuperTrend turned bearish")

    # TTM Squeeze release — volatility expansion starting
    if both("sqz_on"):
        mom = last.get("sqz_mom") or 0
        if prev["sqz_on"] == 1 and last["sqz_on"] == 0:
            add("TTM Squeeze", "Fired ↑" if mom >= 0 else "Fired ↓",
                "bullish" if mom >= 0 else "bearish", f"{mom:.2f}", "Squeeze released")
        elif last["sqz_on"] == 1:
            add("TTM Squeeze", "Squeezing", "neutral", f"{mom:.2f}", "BB inside Keltner — coiling")

    # ADX trend regime
    adx_v, dip, dim = last.get("adx"), last.get("di_plus"), last.get("di_minus")
    if adx_v is not None and dip is not None and dim is not None and adx_v >= 25:
        add("ADX", "Strong Uptrend" if dip > dim else "Strong Downtrend",
            "bullish" if dip > dim else "bearish", f"{adx_v:.1f}", f"ADX {adx_v:.1f} — trending")

    # Stochastic RSI / MFI / CCI / CMF
    sk = last.get("stochrsi_k")
    if sk is not None:
        if sk >= 80:   add("Stoch RSI", "Overbought", "bearish", f"{sk:.1f}", "Stoch RSI above 80")
        elif sk <= 20: add("Stoch RSI", "Oversold",   "bullish", f"{sk:.1f}", "Stoch RSI below 20")
    mfi = last.get("mfi")
    if mfi is not None:
        if mfi >= 80:   add("MFI", "Overbought", "bearish", f"{mfi:.1f}", "Money flow overheated")
        elif mfi <= 20: add("MFI", "Oversold",   "bullish", f"{mfi:.1f}", "Money flow washed out")
    cci = last.get("cci")
    if cci is not None:
        if cci >= 100:    add("CCI", "Overbought", "bearish", f"{cci:.0f}", "CCI above +100")
        elif cci <= -100: add("CCI", "Oversold",   "bullish", f"{cci:.0f}", "CCI below -100")
    cmf = last.get("cmf")
    if cmf is not None:
        if cmf >= 0.2:    add("CMF", "Accumulation", "bullish", f"{cmf:.2f}", "Sustained accumulation")
        elif cmf <= -0.2: add("CMF", "Distribution", "bearish", f"{cmf:.2f}", "Sustained distribution")

    # Ichimoku cloud break
    if both("ich_a", "ich_b", "close"):
        top, bot = max(last["ich_a"], last["ich_b"]), min(last["ich_a"], last["ich_b"])
        ptop, pbot = max(prev["ich_a"], prev["ich_b"]), min(prev["ich_a"], prev["ich_b"])
        if prev["close"] <= ptop and last["close"] > top:
            add("Ichimoku", "Cloud Breakout ↑", "bullish", f"{last['close']:.2f}", "Broke above the cloud")
        elif prev["close"] >= pbot and last["close"] < bot:
            add("Ichimoku", "Cloud Breakdown ↓", "bearish", f"{last['close']:.2f}", "Broke below the cloud")

    # Parabolic SAR flip
    if both("psar", "close"):
        if prev["close"] < prev["psar"] and last["close"] > last["psar"]:
            add("Parabolic SAR", "Flip ↑", "bullish", f"{last['psar']:.2f}", "SAR flipped bullish")
        elif prev["close"] > prev["psar"] and last["close"] < last["psar"]:
            add("Parabolic SAR", "Flip ↓", "bearish", f"{last['psar']:.2f}", "SAR flipped bearish")

    # Golden / death cross
    if both("ma50", "ma200"):
        if prev["ma50"] <= prev["ma200"] and last["ma50"] > last["ma200"]:
            add("Moving Average", "Golden Cross", "bullish", f"{last['ma50']:.2f}", "MA50 crossed above MA200")
        elif prev["ma50"] >= prev["ma200"] and last["ma50"] < last["ma200"]:
            add("Moving Average", "Death Cross", "bearish", f"{last['ma50']:.2f}", "MA50 crossed below MA200")

    # 52-week extremes & volume anomaly
    hi52, lo52 = last.get("high_52w"), last.get("low_52w")
    if cl is not None and hi52 and cl >= hi52 * 0.99:
        add("Price Action", "52W High", "bullish", f"{cl:.2f}", "At / near 52-week high")
    if cl is not None and lo52 and cl <= lo52 * 1.01:
        add("Price Action", "52W Low", "bearish", f"{cl:.2f}", "At / near 52-week low")
    rvol = last.get("rvol")
    if rvol is not None and rvol >= 2:
        add("Volume", "Volume Spike", "neutral", f"{rvol:.1f}x", f"Volume {rvol:.1f}x the 20-day average")

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
