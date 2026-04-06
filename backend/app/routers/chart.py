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
    Detect support & resistance levels using:
    1) swing highs/lows
    2) price gaps
    3) volume-weighted scoring
    """
    highs = df["High"].astype(float).values
    lows = df["Low"].astype(float).values
    closes = df["Close"].astype(float).values
    volumes = df["Volume"].fillna(0).astype(float).values

    n_bars = len(highs)
    current = float(closes[-1])
    if n_bars < 30 or current <= 0:
        return {"resistance": [], "support": []}

    # Adaptive pivot window for different chart lengths
    pivot_window = min(window, max(5, n_bars // 20))

    # ATR-based dynamic clustering tolerance
    prev_close = np.roll(closes, 1)
    prev_close[0] = closes[0]
    tr = np.maximum.reduce([
        highs - lows,
        np.abs(highs - prev_close),
        np.abs(lows - prev_close),
    ])
    atr = float(pd.Series(tr).rolling(14).mean().iloc[-1]) if n_bars >= 14 else float(np.mean(tr))
    dyn_tol = max(tol, min(0.04, (atr / current) * 0.6 if current else tol))

    # Volume baseline
    vol_sma20 = pd.Series(volumes).rolling(20).mean().bfill().values

    candidates: list[dict] = []

    def _vol_ratio(i: int) -> float:
        base = vol_sma20[i] if i < len(vol_sma20) else 0.0
        if base <= 0:
            return 1.0
        return float(np.clip(volumes[i] / base, 0.5, 2.5))

    def _recency_boost(i: int) -> float:
        # 1.0 ~ 1.3, newer levels get modestly higher weight
        age_ratio = (n_bars - 1 - i) / max(n_bars - 1, 1)
        return 1.0 + 0.3 * (1.0 - age_ratio)

    # 1) Swing pivots
    for i in range(pivot_window, n_bars - pivot_window):
        win_h = highs[i - pivot_window : i + pivot_window + 1]
        win_l = lows[i - pivot_window : i + pivot_window + 1]
        vr = _vol_ratio(i)
        rr = _recency_boost(i)
        if highs[i] >= win_h.max():
            candidates.append({
                "price": float(highs[i]),
                "side": "resistance",
                "kind": "swing",
                "weight": 1.0 * vr * rr,
                "vol_ratio": vr,
            })
        if lows[i] <= win_l.min():
            candidates.append({
                "price": float(lows[i]),
                "side": "support",
                "kind": "swing",
                "weight": 1.0 * vr * rr,
                "vol_ratio": vr,
            })

    # 2) Gap levels (zone midpoint as key level)
    gap_min_pct = max(0.003, (atr / current) * 0.25 if current else 0.003)  # ~0.3% baseline
    for i in range(1, n_bars):
        prev_h, prev_l = highs[i - 1], lows[i - 1]
        cur_h, cur_l = highs[i], lows[i]
        vr = _vol_ratio(i)
        rr = _recency_boost(i)

        # Gap up -> potential support zone
        if cur_l > prev_h * (1 + gap_min_pct):
            zone_low = prev_h
            zone_high = cur_l
            mid = (zone_low + zone_high) / 2
            candidates.append({
                "price": float(mid),
                "side": "support",
                "kind": "gap",
                "weight": 1.35 * vr * rr,
                "vol_ratio": vr,
                "zone_low": float(zone_low),
                "zone_high": float(zone_high),
            })

        # Gap down -> potential resistance zone
        if cur_h < prev_l * (1 - gap_min_pct):
            zone_low = cur_h
            zone_high = prev_l
            mid = (zone_low + zone_high) / 2
            candidates.append({
                "price": float(mid),
                "side": "resistance",
                "kind": "gap",
                "weight": 1.35 * vr * rr,
                "vol_ratio": vr,
                "zone_low": float(zone_low),
                "zone_high": float(zone_high),
            })

    def _cluster(side: str) -> list[dict]:
        if side == "resistance":
            pts = [c for c in candidates if c["side"] == side and c["price"] >= current * 0.98]
            pts.sort(key=lambda x: x["price"])
        else:
            pts = [c for c in candidates if c["side"] == side and c["price"] <= current * 1.02]
            pts.sort(key=lambda x: x["price"])

        if not pts:
            return []

        groups: list[list[dict]] = [[pts[0]]]
        for p in pts[1:]:
            anchor = groups[-1][0]["price"]
            if abs(p["price"] - anchor) / max(anchor, 1e-9) <= dyn_tol:
                groups[-1].append(p)
            else:
                groups.append([p])

        levels = []
        for g in groups:
            prices = np.array([x["price"] for x in g], dtype=float)
            weights = np.array([x["weight"] for x in g], dtype=float)
            wsum = float(weights.sum()) if weights.size else 1.0
            price = float(np.average(prices, weights=weights)) if wsum > 0 else float(np.median(prices))
            touches = len(g)
            gap_hits = sum(1 for x in g if x["kind"] == "gap")
            vol_score = float(np.mean([x["vol_ratio"] for x in g])) if g else 1.0
            # Log scaling avoids huge outliers dominating strength
            strength_raw = math.log1p(max(wsum, 0.0)) * 2.2
            strength = int(np.clip(round(strength_raw), 1, 5))
            levels.append({
                "price": round(price, 2),
                "touches": touches,
                "strength": strength,
                "score": round(wsum, 2),
                "gap_hits": gap_hits,
                "volume_score": round(vol_score, 2),
            })

        # Pick strongest first, then sort for display order
        levels.sort(key=lambda x: (-x["score"], -x["touches"], -x["gap_hits"]))
        levels = levels[:n_levels]
        if side == "resistance":
            levels.sort(key=lambda x: x["price"])
        else:
            levels.sort(key=lambda x: -x["price"])
        return levels

    return {"resistance": _cluster("resistance"), "support": _cluster("support")}


_PERIOD_MAP = {
    "1m":  ("1mo",  "1d"),
    "3m":  ("3mo",  "1d"),
    "6m":  ("6mo",  "1d"),
    "1y":  ("1y",   "1d"),
    "2y":  ("2y",   "1d"),
    "5y":  ("5y",   "1d"),
    "10y": ("10y",  "1wk"),
    "max": ("max",  "1wk"),
}


_INTERVAL_MAP = {"day": "1d", "week": "1wk", "month": "1mo"}


@router.get("/{ticker}/chart")
def get_chart(
    ticker: str,
    period: str = Query("5y", description="1m|3m|6m|1y|2y|5y|10y|max"),
    interval: str = Query(None, description="day|week|month — overrides default interval for the period"),
):
    yf_period, default_interval = _PERIOD_MAP.get(period, ("5y", "1d"))
    yf_interval = _INTERVAL_MAP.get(interval, default_interval)
    try:
        tk = yf.Ticker(ticker.upper())
        df = tk.history(period=yf_period, interval=yf_interval, auto_adjust=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yfinance error: {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No price data for {ticker.upper()}")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    bars = _compute_indicators(df)
    sr   = _find_sr_levels(df)
    return {"ticker": ticker.upper(), "period": period, "interval": yf_interval, "bars": bars, "sr": sr}
