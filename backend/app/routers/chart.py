"""
OHLCV + technical indicators endpoint for stock price charts.
"""
import math
import time
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Query
import yfinance as yf
import pandas as pd
import numpy as np
import requests

from ..indicators import compute_indicator_frame, compute_analytics

router = APIRouter()

BENCHMARK = "SPY"
_PERIODS_PER_YEAR = {"1d": 252, "1wk": 52, "1mo": 12}

# In-memory cache: key -> {"data": ..., "ts": float}
_CACHE: dict = {}
_CACHE_TTL = 600  # seconds (10 min)


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    # Evict stale entries to prevent unbounded growth
    now = time.time()
    stale = [k for k, v in _CACHE.items() if now - v["ts"] >= _CACHE_TTL]
    for k in stale:
        del _CACHE[k]
    _CACHE[key] = {"data": data, "ts": now}



def _compute_indicators(df: pd.DataFrame, extra: pd.DataFrame | None = None) -> list[dict]:
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    # ── Moving Averages ──────────────────────────────────────────────────
    ma5   = close.rolling(5).mean()
    ma10  = close.rolling(10).mean()
    ma20  = close.rolling(20).mean()
    ma50  = close.rolling(50).mean()
    ma250 = close.rolling(250).mean()

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

    out = pd.DataFrame({
        "time":     df.index.astype("int64") // 10**9,
        "open":     df["Open"],
        "high":     df["High"],
        "low":      df["Low"],
        "close":    df["Close"],
        "volume":   df["Volume"].fillna(0).astype("int64"),
        "ma5":      ma5,   "ma10": ma10, "ma20": ma20, "ma50": ma50, "ma250": ma250,
        "bb_upper": bb_upper, "bb_mid": bb_mid, "bb_lower": bb_lower,
        "macd":     macd_line, "signal": signal_line, "hist": macd_hist,
        "rsi":      rsi,
        "k":        k_kdj, "d": d_kdj, "j": j_kdj,
        "willr":    willr,
        "obv":      obv,
    })

    if extra is not None and not extra.empty:
        out = out.join(extra.reindex(out.index), how="left")

    def _clean(v):
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return None
        return round(float(v), 4)

    # Fields kept as integers in the payload.
    int_fields = {"time", "volume"}
    rows = []
    for rec in out.to_dict("records"):
        row = {}
        for k, v in rec.items():
            if k in int_fields:
                row[k] = int(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else 0
            else:
                row[k] = _clean(v)
        rows.append(row)
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
        # Newest bar ≈1.0, oldest ≈0.29 — exponential decay so multi-year-old
        # levels can't out-accumulate recent ones purely by age/touch count.
        age_ratio = (n_bars - 1 - i) / max(n_bars - 1, 1)
        return 0.25 + 0.75 * math.exp(-3.0 * age_ratio)

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
            # Proximity decay: a level 30%+ away from the current price is rarely
            # actionable. Without this, a near-ATH stock surfaces multi-year-old
            # lows 40-60% below price as its "top" support (see ADI).
            dist = abs(price - current) / max(current, 1e-9)
            proximity = float(np.clip(1.0 - dist / 0.30, 0.15, 1.0))
            rank_score = wsum * proximity
            # Log scaling avoids huge outliers dominating strength
            strength_raw = math.log1p(max(wsum, 0.0)) * 2.2
            strength = int(np.clip(round(strength_raw), 1, 5))
            levels.append({
                "price": round(price, 2),
                "touches": touches,
                "strength": strength,
                "score": round(wsum, 2),
                "rank_score": rank_score,
                "gap_hits": gap_hits,
                "volume_score": round(vol_score, 2),
            })

        # Rank by proximity-adjusted score so nearby levels win the limited slots.
        levels.sort(key=lambda x: (-x["rank_score"], -x["touches"], -x["gap_hits"]))

        # Drop far-away noise (>35% from price) once we have enough nearby levels,
        # so a near-ATH stock doesn't draw support lines 46-62% below price. If too
        # few are nearby (parabolic move with a big gap), keep only the nearest 3
        # rather than dumping every multi-year-old low.
        near = [lv for lv in levels if abs(lv["price"] - current) / max(current, 1e-9) <= 0.35]
        levels = near if len(near) >= 2 else sorted(
            levels, key=lambda x: abs(x["price"] - current)
        )[:3]

        levels = levels[:n_levels]
        for lv in levels:
            lv.pop("rank_score", None)
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
_MAX_BARS_BY_INTERVAL = {
    "1d": 2500,
    "1wk": 1200,
    "1mo": 600,
}
_YAHOO_RANGE_BY_PERIOD = {
    "5d": "5d",
    "1m": "1mo",
    "3m": "3mo",
    "6m": "6mo",
    "1y": "1y",
    "2y": "2y",
    "5y": "5y",
    "10y": "10y",
    "max": "max",
}


def _fetch_yahoo_chart_df(ticker: str, period: str, interval: str) -> pd.DataFrame:
    response = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker.upper(), safe='')}",
        params={
            "range": _YAHOO_RANGE_BY_PERIOD.get(period, "5y"),
            "interval": interval,
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=12,
    )
    response.raise_for_status()
    result = ((response.json().get("chart") or {}).get("result") or [None])[0]
    if not result:
        return pd.DataFrame()

    timestamps = result.get("timestamp") or []
    quote_data = (((result.get("indicators") or {}).get("quote") or [{}])[0])
    if not timestamps or not quote_data:
        return pd.DataFrame()

    df = pd.DataFrame({
        "Open": quote_data.get("open") or [],
        "High": quote_data.get("high") or [],
        "Low": quote_data.get("low") or [],
        "Close": quote_data.get("close") or [],
        "Volume": quote_data.get("volume") or [],
    }, index=pd.to_datetime(timestamps, unit="s", utc=True))
    return df.dropna(subset=["Open", "High", "Low", "Close"])


def _date_index(index) -> pd.DatetimeIndex:
    """Timezone-naive, midnight-normalised dates.

    The stock frame and the benchmark frame can come from different sources
    (yfinance stamps bars at midnight local, the raw Yahoo chart API at 13:30
    UTC). Aligning on the calendar date is the only way an ffill reindex lines
    the two up — otherwise the benchmark silently shifts a day and beta /
    correlation collapse toward zero.
    """
    idx = pd.to_datetime(index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def _fetch_benchmark_close(period: str, interval: str) -> pd.Series | None:
    """SPY closes for relative-strength / beta. Cached like any other chart pull."""
    key = f"__BENCH__{BENCHMARK}_{period}_{interval}"
    cached = _cache_get(key)
    if cached is not None:
        return cached if isinstance(cached, pd.Series) else None

    yf_period = _PERIOD_MAP.get(period, ("5y", "1d"))[0]
    df = pd.DataFrame()
    try:
        df = yf.Ticker(BENCHMARK).history(period=yf_period, interval=interval, auto_adjust=True)
    except Exception:
        pass
    if df.empty:
        try:
            df = _fetch_yahoo_chart_df(BENCHMARK, period, interval)
        except Exception:
            return None
    if df.empty:
        return None

    s = df["Close"].astype(float)
    s.index = _date_index(s.index)
    s = s[~s.index.duplicated(keep="last")]
    _cache_set(key, s)
    return s


@router.get("/{ticker}/chart")
def get_chart(
    ticker: str,
    period: str = Query("5y", description="1m|3m|6m|1y|2y|5y|10y|max"),
    interval: str = Query(None, description="day|week|month — overrides default interval for the period"),
):
    yf_period, default_interval = _PERIOD_MAP.get(period, ("5y", "1d"))
    yf_interval = _INTERVAL_MAP.get(interval, default_interval)

    # Guardrail: long lookback + daily bars is heavy and slow to render.
    if yf_period in {"10y", "max"} and yf_interval == "1d":
        yf_interval = "1wk"

    cache_key = f"{ticker.upper()}_{period}_{yf_interval}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        tk = yf.Ticker(ticker.upper())
        df = tk.history(period=yf_period, interval=yf_interval, auto_adjust=True)
    except Exception as e:
        try:
            df = _fetch_yahoo_chart_df(ticker, period, yf_interval)
        except Exception as fallback_exc:
            raise HTTPException(
                status_code=502,
                detail=f"price chart fetch failed: yfinance={e}; yahoo_chart={fallback_exc}",
            )

    if df.empty:
        try:
            df = _fetch_yahoo_chart_df(ticker, period, yf_interval)
        except Exception:
            pass
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No price data for {ticker.upper()}")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    # Limit payload and chart rendering cost.
    max_bars = _MAX_BARS_BY_INTERVAL.get(yf_interval, 2000)
    if len(df) > max_bars:
        df = df.iloc[-max_bars:]

    ppy = _PERIODS_PER_YEAR.get(yf_interval, 252)

    # Benchmark for relative strength / beta. Aligned on a tz-naive index so the
    # reindex inside the indicator module actually matches.
    bench = None
    if ticker.upper() != BENCHMARK:
        raw_bench = _fetch_benchmark_close(period, yf_interval)
        if raw_bench is not None:
            bench = raw_bench.reindex(_date_index(df.index), method="ffill")
            bench.index = df.index

    try:
        extra = compute_indicator_frame(df, bench_close=bench, periods_per_year=ppy)
    except Exception:
        extra = pd.DataFrame(index=df.index)

    bars = _compute_indicators(df, extra)
    sr   = _find_sr_levels(df)

    # Analytics need the legacy oscillators too so the aggregate rating can vote
    # on RSI / MACD / Williams %R alongside the new indicators.
    analytics = {}
    try:
        legacy = pd.DataFrame({
            "_rsi":    [b.get("rsi") for b in bars],
            "_willr":  [b.get("willr") for b in bars],
            "_macd":   [b.get("macd") for b in bars],
            "_signal": [b.get("signal") for b in bars],
        }, index=df.index).astype(float)
        combined = extra.join(legacy, how="left")
        ma_series = {
            "MA20": df["Close"].rolling(20).mean(),
            "MA50": df["Close"].rolling(50).mean(),
            "MA250": df["Close"].rolling(250).mean(),
        }
        analytics = compute_analytics(df, combined, bench_close=bench,
                                      periods_per_year=ppy, ma=ma_series)
    except Exception as exc:  # analytics are additive — never break the chart
        analytics = {"error": str(exc)}

    result = {
        "ticker": ticker.upper(), "period": period, "interval": yf_interval,
        "bars": bars, "sr": sr, "analytics": analytics,
    }
    _cache_set(cache_key, result)
    return result
