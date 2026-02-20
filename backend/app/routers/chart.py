"""
OHLCV + technical indicators endpoint for stock price charts.
"""
import math
from fastapi import APIRouter, HTTPException, Query
import yfinance as yf
import pandas as pd
import numpy as np

router = APIRouter()


def _safe(val):
    if val is None:
        return None
    try:
        if math.isnan(val) or math.isinf(val):
            return None
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None


def _compute_indicators(df: pd.DataFrame) -> list[dict]:
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    # ── Moving Averages ──────────────────────────────────────────────────
    ma5   = close.rolling(5).mean()
    ma10  = close.rolling(10).mean()
    ma20  = close.rolling(20).mean()
    ma50  = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()

    # ── Bollinger Bands (20, 2) ──────────────────────────────────────────
    bb_mid   = close.rolling(20).mean()
    bb_std   = close.rolling(20).std(ddof=0)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # ── MACD (12, 26, 9) ────────────────────────────────────────────────
    ema12       = close.ewm(span=12, adjust=False).mean()
    ema26       = close.ewm(span=26, adjust=False).mean()
    macd_line   = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist   = macd_line - signal_line

    # ── RSI (14) ─────────────────────────────────────────────────────────
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs    = gain / (loss + 1e-9)
    rsi   = 100 - (100 / (1 + rs))

    # ── KDJ (9, 3, 3) ────────────────────────────────────────────────────
    low9  = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv   = (close - low9) / (high9 - low9 + 1e-9) * 100
    k_kdj = rsv.ewm(com=2, adjust=False).mean()
    d_kdj = k_kdj.ewm(com=2, adjust=False).mean()
    j_kdj = 3 * k_kdj - 2 * d_kdj

    # ── Williams %R (14) ─────────────────────────────────────────────────
    highest14 = high.rolling(14).max()
    lowest14  = low.rolling(14).min()
    willr     = (highest14 - close) / (highest14 - lowest14 + 1e-9) * -100

    # ── OBV ──────────────────────────────────────────────────────────────
    sign = pd.Series(0.0, index=close.index)
    sign[close > close.shift(1)]  =  1.0
    sign[close < close.shift(1)]  = -1.0
    obv = (sign * vol).cumsum()

    rows = []
    for i, (_, row) in enumerate(df.iterrows()):
        ts = int(df.index[i].timestamp())
        rows.append({
            "time":     ts,
            "open":     _safe(row["Open"]),
            "high":     _safe(row["High"]),
            "low":      _safe(row["Low"]),
            "close":    _safe(row["Close"]),
            "volume":   int(row["Volume"]) if not math.isnan(row["Volume"]) else 0,
            # MA
            "ma5":      _safe(ma5.iloc[i]),
            "ma10":     _safe(ma10.iloc[i]),
            "ma20":     _safe(ma20.iloc[i]),
            "ma50":     _safe(ma50.iloc[i]),
            "ma200":    _safe(ma200.iloc[i]),
            # Bollinger Bands
            "bb_upper": _safe(bb_upper.iloc[i]),
            "bb_mid":   _safe(bb_mid.iloc[i]),
            "bb_lower": _safe(bb_lower.iloc[i]),
            # MACD
            "macd":     _safe(macd_line.iloc[i]),
            "signal":   _safe(signal_line.iloc[i]),
            "hist":     _safe(macd_hist.iloc[i]),
            # RSI
            "rsi":      _safe(rsi.iloc[i]),
            # KDJ
            "k":        _safe(k_kdj.iloc[i]),
            "d":        _safe(d_kdj.iloc[i]),
            "j":        _safe(j_kdj.iloc[i]),
            # Williams %R
            "willr":    _safe(willr.iloc[i]),
            # OBV
            "obv":      _safe(obv.iloc[i]),
        })
    return rows


def _find_sr_levels(df: pd.DataFrame, window: int = 20, n_levels: int = 6, tol: float = 0.015) -> dict:
    """
    Detect support & resistance via swing-high / swing-low clustering.
    window  – bars on each side a local extreme must dominate
    tol     – cluster tolerance (1.5 % of price)
    Returns { resistance: [{price, touches, strength}], support: [...] }
    """
    highs   = df["High"].values.astype(float)
    lows    = df["Low"].values.astype(float)
    current = float(df["Close"].values[-1])
    n_bars  = len(highs)

    swing_h, swing_l = [], []
    for i in range(window, n_bars - window):
        win_h = highs[i - window : i + window + 1]
        win_l = lows[i  - window : i + window + 1]
        if highs[i] == win_h.max():
            swing_h.append(highs[i])
        if lows[i] == win_l.min():
            swing_l.append(lows[i])

    def cluster(raw: list, above: bool) -> list:
        prices = [p for p in raw if (p >= current * 0.98 if above else p <= current * 1.02)]
        if not prices:
            return []
        prices = sorted(prices)
        groups, cur = [], [prices[0]]
        for p in prices[1:]:
            if cur and (p - cur[0]) / cur[0] <= tol:
                cur.append(p)
            else:
                groups.append(cur)
                cur = [p]
        groups.append(cur)

        result = []
        for g in groups:
            touches = len(g)
            result.append({
                "price":    round(float(np.median(g)), 2),
                "touches":  touches,
                "strength": min(touches, 5),
            })
        result.sort(key=lambda x: -x["touches"])
        return result[:n_levels]

    resistance = sorted(cluster(swing_h, above=True),  key=lambda x:  x["price"])
    support    = sorted(cluster(swing_l, above=False), key=lambda x: -x["price"])
    return {"resistance": resistance, "support": support}


_PERIOD_MAP = {
    "1m":  ("1mo",  "1d"),
    "3m":  ("3mo",  "1d"),
    "6m":  ("6mo",  "1d"),
    "1y":  ("1y",   "1d"),
    "2y":  ("2y",   "1d"),
    "5y":  ("5y",   "1d"),
    "max": ("max",  "1wk"),
}


@router.get("/{ticker}/chart")
def get_chart(
    ticker: str,
    period: str = Query("5y", description="1m|3m|6m|1y|2y|5y|max"),
):
    yf_period, interval = _PERIOD_MAP.get(period, ("5y", "1d"))
    try:
        tk = yf.Ticker(ticker.upper())
        df = tk.history(period=yf_period, interval=interval, auto_adjust=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No price data for {ticker.upper()}")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    bars = _compute_indicators(df)
    sr   = _find_sr_levels(df)
    return {"ticker": ticker.upper(), "period": period, "interval": interval, "bars": bars, "sr": sr}
