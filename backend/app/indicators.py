"""
Advanced technical indicators — the set professional platforms (TradingView,
thinkorswim, Bloomberg, TC2000, Sierra Chart) treat as table stakes.

Two entry points:

    compute_indicator_frame(df, bench_close=None, periods_per_year=252)
        -> DataFrame of per-bar series (overlays + oscillators)

    compute_analytics(df, ind, bench_close=None, periods_per_year=252)
        -> dict of non-series analytics (pivots, volume profile, fib levels,
           risk stats, aggregated technical rating)

Everything is pure pandas/numpy so it can be reused by the chart endpoint,
the signal screener, and the earnings/war-room scripts.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

EPS = 1e-9


# ── Primitives ───────────────────────────────────────────────────────────────
def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (RMA) — what ATR/ADX/RSI are actually defined on."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), n)
    loss = wilder(-delta.clip(upper=0), n)
    return 100 - (100 / (1 + gain / (loss + EPS)))


def _linreg_endpoint(series: pd.Series, n: int) -> pd.Series:
    """Value of the least-squares fit at the newest bar (TTM Squeeze momentum)."""
    x = np.arange(n, dtype=float)
    x_mean = x.mean()
    denom = float(((x - x_mean) ** 2).sum()) or EPS

    def f(w):
        y_mean = w.mean()
        slope = float(((x - x_mean) * (w - y_mean)).sum()) / denom
        return (y_mean - slope * x_mean) + slope * (n - 1)

    return series.rolling(n).apply(f, raw=True)


# ── Risk primitives (ported from the gs-quant timeseries conventions) ─────────
def ewma_volatility(close: pd.Series, lam: float = 0.94,
                    periods_per_year: int = 252) -> pd.Series:
    """RiskMetrics EWMA volatility, annualised %.

    A fixed 20-day window weights a shock from 20 days ago exactly as heavily as
    yesterday's, then drops it abruptly. EWMA decays it smoothly, so the estimate
    reacts on the day the regime changes — which is the whole point of watching it.
    """
    r = np.log(close / close.shift(1))
    var = r.pow(2).ewm(alpha=1 - lam, adjust=False).mean()
    vol = np.sqrt(var * periods_per_year) * 100
    # The recursion starts from a single squared return, so the opening bars read
    # near-zero regardless of the real regime. Left in, that burn-in becomes the
    # bottom of the distribution and inflates every percentile rank downstream.
    burn_in = min(len(vol), int(2 / (1 - lam)))
    vol.iloc[:burn_in] = np.nan
    return vol


def rolling_beta_alpha(returns: pd.Series, bench_returns: pd.Series, window: int = 63,
                       periods_per_year: int = 252) -> tuple[pd.Series, pd.Series]:
    """Rolling OLS of asset returns on benchmark returns → (beta, annualised alpha %).

    One whole-window beta hides regime change: a name can average 1.9 over two
    years while running 1.4 in the first half and 3.0 in the second.
    """
    var = bench_returns.rolling(window).var()
    beta = returns.rolling(window).cov(bench_returns) / var.replace(0, np.nan)
    alpha = (returns.rolling(window).mean()
             - beta * bench_returns.rolling(window).mean()) * periods_per_year * 100
    return beta, alpha


def percentile_rank(series: pd.Series, window: int = 252) -> pd.Series:
    """Where each value sits inside its own trailing distribution (0-100).

    "ATR% is 2.85" is unreadable on its own; "ATR% is at the 92nd percentile of
    the last year" is immediately actionable.
    """
    return series.rolling(window).rank(pct=True) * 100


def winsorize(series: pd.Series, n_sigma: float = 2.0) -> pd.Series:
    """Clip to ±n_sigma so two gap days don't set the whole volatility estimate."""
    mu = float(series.mean())
    sd = float(series.std(ddof=0))
    if not np.isfinite(sd) or sd == 0:
        return series
    return series.clip(mu - n_sigma * sd, mu + n_sigma * sd)


def volatility_regime(pct_rank: float | None) -> str | None:
    """Bucket a volatility percentile into a label the LLM briefings can act on."""
    if pct_rank is None or not np.isfinite(pct_rank):
        return None
    if pct_rank >= 90:
        return "extreme"
    if pct_rank >= 70:
        return "elevated"
    if pct_rank <= 10:
        return "compressed"
    return "normal"


# ── Trend / volatility overlays ──────────────────────────────────────────────
def adx_dmi(high, low, close, n: int = 14):
    """Wilder ADX + directional movement. Trend *strength* vs trend direction."""
    up = high.diff()
    dn = -low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index)

    atr_n = wilder(true_range(high, low, close), n)
    di_plus = 100 * wilder(plus_dm, n) / (atr_n + EPS)
    di_minus = 100 * wilder(minus_dm, n) / (atr_n + EPS)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + EPS)
    return wilder(dx, n), di_plus, di_minus


def supertrend(high, low, close, period: int = 10, mult: float = 3.0):
    """ATR trailing-stop trend filter. Returns (line, direction ∈ {1,-1})."""
    a = atr(high, low, close, period)
    hl2 = (high + low) / 2
    upper = (hl2 + mult * a).values
    lower = (hl2 - mult * a).values
    c = close.values
    n = len(c)

    fu = np.full(n, np.nan)
    fl = np.full(n, np.nan)
    line = np.full(n, np.nan)
    direction = np.full(n, np.nan)

    start = int(np.argmax(~np.isnan(upper))) if np.any(~np.isnan(upper)) else n
    if start >= n:
        return pd.Series(line, index=close.index), pd.Series(direction, index=close.index)

    fu[start], fl[start] = upper[start], lower[start]
    line[start], direction[start] = lower[start], 1.0

    for i in range(start + 1, n):
        fu[i] = min(upper[i], fu[i - 1]) if c[i - 1] <= fu[i - 1] else upper[i]
        fl[i] = max(lower[i], fl[i - 1]) if c[i - 1] >= fl[i - 1] else lower[i]
        if direction[i - 1] > 0:
            direction[i] = -1.0 if c[i] < fl[i] else 1.0
        else:
            direction[i] = 1.0 if c[i] > fu[i] else -1.0
        line[i] = fl[i] if direction[i] > 0 else fu[i]

    return pd.Series(line, index=close.index), pd.Series(direction, index=close.index)


def parabolic_sar(high, low, af_step: float = 0.02, af_max: float = 0.2) -> pd.Series:
    h, l = high.values, low.values
    n = len(h)
    out = np.full(n, np.nan)
    if n < 3:
        return pd.Series(out, index=high.index)

    bull = h[1] >= h[0]
    af = af_step
    ep = h[1] if bull else l[1]
    out[1] = l[0] if bull else h[0]

    for i in range(2, n):
        cur = out[i - 1] + af * (ep - out[i - 1])
        if bull:
            cur = min(cur, l[i - 1], l[i - 2])
            if l[i] < cur:
                bull, cur, ep, af = False, ep, l[i], af_step
        else:
            cur = max(cur, h[i - 1], h[i - 2])
            if h[i] > cur:
                bull, cur, ep, af = True, ep, h[i], af_step

        if bull and h[i] > ep:
            ep, af = h[i], min(af + af_step, af_max)
        elif not bull and l[i] < ep:
            ep, af = l[i], min(af + af_step, af_max)
        out[i] = cur

    return pd.Series(out, index=high.index)


def ichimoku(high, low, close, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52):
    """一目均衡表. Cloud spans are shifted forward by `kijun` onto existing bars."""
    conv = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    base = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2
    span_a = ((conv + base) / 2).shift(kijun)
    span_b = ((high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2).shift(kijun)
    lagging = close.shift(-kijun)
    return conv, base, span_a, span_b, lagging


def td_setup(close: pd.Series, length: int = 9, lookback: int = 4) -> pd.Series:
    """神奇九轉 / TD Setup — the 低9 / 高9 exhaustion count.

    A buy setup counts bars closing *below* the close `lookback` bars earlier; a
    sell setup counts the mirror. Any bar that fails the comparison resets the
    run, so bar 1 is always the price flip that starts it. The count is returned
    signed — negative while a buy setup builds, positive for a sell setup — and
    a completed run resets to zero, which makes ±`length` mark exactly the
    signal bars rather than every bar of a long streak.
    """
    c = close.to_numpy(dtype=float)
    n = len(c)
    out = np.zeros(n)
    count = 0
    for i in range(n):
        if i < lookback or not np.isfinite(c[i]) or not np.isfinite(c[i - lookback]):
            count = 0
            continue
        if c[i] < c[i - lookback]:
            count = count - 1 if count < 0 else -1
        elif c[i] > c[i - lookback]:
            count = count + 1 if count > 0 else 1
        else:
            count = 0          # an unchanged close breaks the streak
        out[i] = count
        if abs(count) >= length:
            count = 0
    return pd.Series(out, index=close.index)


def anchored_vwap(close, high, low, volume, anchor_idx: int) -> pd.Series:
    """VWAP anchored to a specific bar — the institutional 'cost basis since X' line."""
    tp = (high + low + close) / 3
    pv = (tp * volume).values
    v = volume.fillna(0).values.astype(float)
    n = len(close)
    out = np.full(n, np.nan)
    if anchor_idx < 0 or anchor_idx >= n:
        return pd.Series(out, index=close.index)
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(anchor_idx, n):
        cum_pv += float(pv[i]) if np.isfinite(pv[i]) else 0.0
        cum_v += v[i]
        out[i] = cum_pv / cum_v if cum_v > 0 else np.nan
    return pd.Series(out, index=close.index)


# ── Master series builder ────────────────────────────────────────────────────
def compute_indicator_frame(
    df: pd.DataFrame,
    bench_close: pd.Series | None = None,
    periods_per_year: int = 252,
) -> pd.DataFrame:
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"].fillna(0)
    open_ = df["Open"]
    out = pd.DataFrame(index=df.index)

    # ── Exponential MAs (pros lean on EMA over SMA for trend) ────────────────
    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    out["ema200"] = close.ewm(span=200, adjust=False).mean()

    # ── VWAP family ──────────────────────────────────────────────────────────
    tp = (high + low + close) / 3
    out["vwap20"] = (tp * vol).rolling(20).sum() / (vol.rolling(20).sum() + EPS)
    # Anchored VWAP from the window's swing high / swing low — the two anchors
    # desks actually use to judge who is underwater.
    hi_idx = int(np.nanargmax(high.values)) if len(high) else 0
    lo_idx = int(np.nanargmin(low.values)) if len(low) else 0
    out["avwap_high"] = anchored_vwap(close, high, low, vol, hi_idx)
    out["avwap_low"] = anchored_vwap(close, high, low, vol, lo_idx)

    # ── ATR & volatility ─────────────────────────────────────────────────────
    atr14 = atr(high, low, close, 14)
    out["atr"] = atr14
    out["atrp"] = atr14 / (close + EPS) * 100
    logret = np.log(close / close.shift(1))
    out["hv20"] = logret.rolling(20).std(ddof=0) * math.sqrt(periods_per_year) * 100
    out["ewma_vol"] = ewma_volatility(close, 0.94, periods_per_year)

    # ── Keltner / Donchian ───────────────────────────────────────────────────
    kc_mid = close.ewm(span=20, adjust=False).mean()
    kc_off = 2.0 * atr(high, low, close, 20)
    out["kc_upper"], out["kc_mid"], out["kc_lower"] = kc_mid + kc_off, kc_mid, kc_mid - kc_off

    dc_upper = high.rolling(20).max()
    dc_lower = low.rolling(20).min()
    out["dc_upper"], out["dc_lower"] = dc_upper, dc_lower
    out["dc_mid"] = (dc_upper + dc_lower) / 2

    # ── Bollinger derivatives (%B + bandwidth) ───────────────────────────────
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std(ddof=0)
    bb_up, bb_lo = bb_mid + 2 * bb_std, bb_mid - 2 * bb_std
    out["bb_pctb"] = (close - bb_lo) / ((bb_up - bb_lo) + EPS) * 100
    out["bb_bw"] = (bb_up - bb_lo) / (bb_mid + EPS) * 100

    # ── TTM Squeeze (BB inside Keltner) + linreg momentum ────────────────────
    out["sqz_on"] = ((bb_up < out["kc_upper"]) & (bb_lo > out["kc_lower"])).astype(float)
    basis = ((dc_upper + dc_lower) / 2 + close.rolling(20).mean()) / 2
    out["sqz_mom"] = _linreg_endpoint(close - basis, 20)

    # ── SuperTrend / PSAR / Chandelier (trailing stops) ──────────────────────
    st_line, st_dir = supertrend(high, low, close, 10, 3.0)
    out["st"], out["st_dir"] = st_line, st_dir
    out["psar"] = parabolic_sar(high, low)
    atr22 = atr(high, low, close, 22)
    out["chand_long"] = high.rolling(22).max() - 3 * atr22
    out["chand_short"] = low.rolling(22).min() + 3 * atr22

    # ── Ichimoku ─────────────────────────────────────────────────────────────
    conv, base, span_a, span_b, lagging = ichimoku(high, low, close)
    out["ich_conv"], out["ich_base"] = conv, base
    out["ich_a"], out["ich_b"], out["ich_lag"] = span_a, span_b, lagging

    # ── ADX / DMI ────────────────────────────────────────────────────────────
    adx_v, di_p, di_m = adx_dmi(high, low, close, 14)
    out["adx"], out["di_plus"], out["di_minus"] = adx_v, di_p, di_m

    # ── Stochastic RSI ───────────────────────────────────────────────────────
    r = rsi(close, 14)
    r_lo, r_hi = r.rolling(14).min(), r.rolling(14).max()
    stoch = (r - r_lo) / ((r_hi - r_lo) + EPS) * 100
    out["stochrsi_k"] = stoch.rolling(3).mean()
    out["stochrsi_d"] = out["stochrsi_k"].rolling(3).mean()

    # ── CCI ──────────────────────────────────────────────────────────────────
    tp_sma = tp.rolling(20).mean()
    md = tp.rolling(20).apply(lambda w: float(np.abs(w - w.mean()).mean()), raw=True)
    out["cci"] = (tp - tp_sma) / (0.015 * md + EPS)

    # ── Money flow: MFI, CMF, A/D line ───────────────────────────────────────
    rmf = tp * vol
    pos_mf = rmf.where(tp > tp.shift(1), 0.0)
    neg_mf = rmf.where(tp < tp.shift(1), 0.0)
    mfr = pos_mf.rolling(14).sum() / (neg_mf.rolling(14).sum() + EPS)
    out["mfi"] = 100 - (100 / (1 + mfr))

    rng = (high - low).replace(0, np.nan)
    mfm = (((close - low) - (high - close)) / rng).fillna(0.0)
    mfv = mfm * vol
    out["cmf"] = mfv.rolling(20).sum() / (vol.rolling(20).sum() + EPS)
    out["adl"] = mfv.cumsum()

    # ── Aroon ────────────────────────────────────────────────────────────────
    out["aroon_up"] = high.rolling(26).apply(lambda w: 100.0 * int(np.argmax(w)) / 25, raw=True)
    out["aroon_dn"] = low.rolling(26).apply(lambda w: 100.0 * int(np.argmin(w)) / 25, raw=True)

    # ── 神奇九轉 / TD Setup (低9 買訊 / 高9 賣訊) ─────────────────────────────
    out["td_setup"] = td_setup(close)

    # ── Relative volume vs 20-bar average ────────────────────────────────────
    out["rvol"] = vol / (vol.rolling(20).mean() + EPS)

    # ── Relative strength vs benchmark (RS line + Mansfield RSI) ─────────────
    if bench_close is not None and len(bench_close) > 0:
        b = bench_close.reindex(df.index).ffill().bfill()
        ratio = close / (b + EPS)
        first = ratio.dropna()
        if len(first) > 0:
            out["rs"] = ratio / float(first.iloc[0]) * 100
            # Mansfield's RSI is a *52-week* concept, so the window follows the
            # timeframe (252d / 52w / 12m) rather than a fixed bar count.
            win = max(20, min(periods_per_year, len(ratio) // 2))
            out["rs_mansfield"] = (ratio / (ratio.rolling(win).mean() + EPS) - 1) * 100

        # Rolling beta / alpha — one quarter of bars, floored so weekly and
        # monthly charts still produce a usable series.
        beta_win = max(20, min(63, len(close) // 3))
        b_roll, a_roll = rolling_beta_alpha(
            close.pct_change(), b.pct_change(), beta_win, periods_per_year)
        out["beta_roll"], out["alpha_roll"] = b_roll, a_roll

    # Elder impulse-style bar colouring input: EMA13 slope + MACD hist slope
    ema13 = close.ewm(span=13, adjust=False).mean()
    macd_h = (close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean())
    macd_h = macd_h - macd_h.ewm(span=9, adjust=False).mean()
    impulse = pd.Series(0.0, index=df.index)
    impulse[(ema13.diff() > 0) & (macd_h.diff() > 0)] = 1.0
    impulse[(ema13.diff() < 0) & (macd_h.diff() < 0)] = -1.0
    out["impulse"] = impulse
    out["gap_pct"] = (open_ - close.shift(1)) / (close.shift(1) + EPS) * 100

    return out


# ── Non-series analytics ─────────────────────────────────────────────────────
def pivot_points(df: pd.DataFrame, rule: str = "ME") -> dict | None:
    """Classic + Fibonacci pivots from the last *completed* higher-timeframe bar."""
    try:
        agg = df.resample(rule).agg({"High": "max", "Low": "min", "Close": "last"}).dropna()
    except Exception:
        return None
    if len(agg) < 2:
        return None
    h, l, c = (float(agg["High"].iloc[-2]), float(agg["Low"].iloc[-2]), float(agg["Close"].iloc[-2]))
    rng = h - l
    if rng <= 0:
        return None
    p = (h + l + c) / 3
    return {
        "period": str(agg.index[-2].date()),
        "classic": {
            "r3": round(h + 2 * (p - l), 2), "r2": round(p + rng, 2), "r1": round(2 * p - l, 2),
            "p": round(p, 2),
            "s1": round(2 * p - h, 2), "s2": round(p - rng, 2), "s3": round(l - 2 * (h - p), 2),
        },
        "fibonacci": {
            "r3": round(p + rng, 2), "r2": round(p + 0.618 * rng, 2), "r1": round(p + 0.382 * rng, 2),
            "p": round(p, 2),
            "s1": round(p - 0.382 * rng, 2), "s2": round(p - 0.618 * rng, 2), "s3": round(p - rng, 2),
        },
    }


def volume_profile(df: pd.DataFrame, bins: int = 48, value_area: float = 0.70,
                   lookback: int | None = None) -> dict | None:
    """Market-profile POC / value-area high & low, volume spread across each bar's range.

    Windowed to `lookback` bars (default one year) the way TradingView's
    visible-range profile is. A profile over a full 5–10 year history puts the
    POC wherever the stock traded years ago, which is accurate and useless — a
    high-flyer would get a POC 90% below the current price.
    """
    sub = df.iloc[-lookback:] if lookback and len(df) > lookback else df
    lows = sub["Low"].astype(float).values
    highs = sub["High"].astype(float).values
    vols = sub["Volume"].fillna(0).astype(float).values
    if len(lows) < 20:
        return None

    lo, hi = float(np.nanmin(lows)), float(np.nanmax(highs))
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        return None

    edges = np.linspace(lo, hi, bins + 1)
    width = (hi - lo) / bins
    hist = np.zeros(bins)

    for i in range(len(lows)):
        bl, bh, v = lows[i], highs[i], vols[i]
        if v <= 0 or not (np.isfinite(bl) and np.isfinite(bh)):
            continue
        i0 = min(bins - 1, max(0, int((bl - lo) / width)))
        i1 = min(bins - 1, max(0, int((bh - lo) / width)))
        if i0 == i1 or bh <= bl:
            hist[i0] += v
            continue
        span = bh - bl
        for b in range(i0, i1 + 1):
            seg = min(bh, edges[b + 1]) - max(bl, edges[b])
            if seg > 0:
                hist[b] += v * seg / span

    total = float(hist.sum())
    if total <= 0:
        return None

    poc_i = int(np.argmax(hist))
    lo_i = hi_i = poc_i
    acc = hist[poc_i]
    while acc < total * value_area and (lo_i > 0 or hi_i < bins - 1):
        down = hist[lo_i - 1] if lo_i > 0 else -1.0
        up = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        if up >= down:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]

    def mid(i):
        return round(float((edges[i] + edges[i + 1]) / 2), 2)

    peak = float(hist.max()) or 1.0
    return {
        "poc": mid(poc_i),
        "vah": round(float(edges[hi_i + 1]), 2),
        "val": round(float(edges[lo_i]), 2),
        "bars": len(lows),
        "bins": [
            {"price": mid(i), "volume": round(float(hist[i]), 0), "pct": round(float(hist[i]) / peak * 100, 1)}
            for i in range(bins)
        ],
    }


def fib_retracement(df: pd.DataFrame, lookback: int = 252) -> dict | None:
    """Auto-fib off the dominant swing in the lookback window."""
    sub = df.iloc[-lookback:] if len(df) > lookback else df
    if len(sub) < 20:
        return None
    highs, lows = sub["High"].astype(float), sub["Low"].astype(float)
    hi_pos, lo_pos = int(np.nanargmax(highs.values)), int(np.nanargmin(lows.values))
    hi, lo = float(highs.iloc[hi_pos]), float(lows.iloc[lo_pos])
    if hi <= lo:
        return None

    up_leg = hi_pos > lo_pos  # low came first → measuring an up-move
    rng = hi - lo
    ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618]
    levels = []
    for r in ratios:
        price = hi - rng * r if up_leg else lo + rng * r
        levels.append({"ratio": r, "label": f"{r * 100:.1f}%", "price": round(price, 2)})
    return {
        "direction": "up" if up_leg else "down",
        "swing_high": round(hi, 2),
        "swing_low": round(lo, 2),
        "levels": levels,
    }


def percentile_snapshot(df: pd.DataFrame, ind: pd.DataFrame,
                        window: int = 252) -> dict:
    """Trailing percentile rank of the metrics worth judging in context.

    Fed to the LLM briefings so `risk_level` can distinguish "volatile stock" from
    "this stock is unusually volatile *right now*".
    """
    candidates = {
        "price":    df["Close"],
        "atrp":     ind.get("atrp"),
        "hv20":     ind.get("hv20"),
        "ewma_vol": ind.get("ewma_vol"),
        "rvol":     ind.get("rvol"),
        "bb_bw":    ind.get("bb_bw"),
        "adx":      ind.get("adx"),
    }
    out: dict = {}
    for key, series in candidates.items():
        if series is None:
            continue
        clean = series.dropna()
        if len(clean) < 20:
            continue
        win = max(20, min(window, len(clean)))
        val = percentile_rank(series, win).iloc[-1]
        if val is not None and np.isfinite(val):
            out[key] = round(float(val), 0)
    if "ewma_vol" in out:
        out["vol_regime"] = volatility_regime(out["ewma_vol"])
    return out


def risk_stats(df: pd.DataFrame, ind: pd.DataFrame, bench_close: pd.Series | None,
               periods_per_year: int = 252) -> dict:
    close = df["Close"].astype(float)
    cur = float(close.iloc[-1])
    win = min(len(close), periods_per_year)
    w = close.iloc[-win:]
    hi52, lo52 = float(w.max()), float(w.min())

    ret = close.pct_change().dropna()
    dd = close / close.cummax() - 1
    stats = {
        "price": round(cur, 2),
        "high_52w": round(hi52, 2),
        "low_52w": round(lo52, 2),
        "pct_from_high": round((cur / hi52 - 1) * 100, 2) if hi52 else None,
        "pct_from_low": round((cur / lo52 - 1) * 100, 2) if lo52 else None,
        "max_drawdown": round(float(dd.min()) * 100, 2) if len(dd) else None,
        "current_drawdown": round(float(dd.iloc[-1]) * 100, 2) if len(dd) else None,
    }

    for key, col in (("atr", "atr"), ("atrp", "atrp"), ("hv20", "hv20"),
                     ("adx", "adx"), ("rvol", "rvol"), ("bb_pctb", "bb_pctb"),
                     ("ewma_vol", "ewma_vol")):
        v = ind[col].iloc[-1] if col in ind else None
        stats[key] = round(float(v), 2) if v is not None and np.isfinite(v) else None

    # ATR-based stop / target sizing (Van Tharp style R-multiples)
    a = stats.get("atr")
    if a:
        stats["stop_2atr"] = round(cur - 2 * a, 2)
        stats["target_3r"] = round(cur + 6 * a, 2)

    if len(ret) > 30:
        stats["annual_vol"] = round(float(ret.std(ddof=0)) * math.sqrt(periods_per_year) * 100, 2)
        # Same figure with returns clipped at ±2σ. A wide gap between the two says
        # the headline volatility is a handful of gap days, not a persistent regime.
        stats["vol_winsorized"] = round(
            float(winsorize(ret, 2.0).std(ddof=0)) * math.sqrt(periods_per_year) * 100, 2)
        downside = ret[ret < 0]
        mean_ann = float(ret.mean()) * periods_per_year
        if len(downside) > 5:
            dstd = float(downside.std(ddof=0)) * math.sqrt(periods_per_year)
            stats["sortino"] = round(mean_ann / dstd, 2) if dstd else None
        sd = float(ret.std(ddof=0)) * math.sqrt(periods_per_year)
        stats["sharpe"] = round(mean_ann / sd, 2) if sd else None

    if bench_close is not None and len(bench_close) > 0:
        b = bench_close.reindex(df.index).ffill().bfill().pct_change()
        joined = pd.concat([ret, b], axis=1).dropna()
        if len(joined) > 30:
            cov = float(joined.cov().iloc[0, 1])
            var = float(joined.iloc[:, 1].var())
            if var:
                beta = cov / var
                stats["beta"] = round(beta, 2)
                stats["alpha"] = round(
                    (float(joined.iloc[:, 0].mean()) - beta * float(joined.iloc[:, 1].mean()))
                    * periods_per_year * 100, 2)
            stats["correlation"] = round(float(joined.corr().iloc[0, 1]), 2)

    if "rs_mansfield" in ind:
        v = ind["rs_mansfield"].iloc[-1]
        stats["rs_mansfield"] = round(float(v), 2) if np.isfinite(v) else None

    # Rolling beta — the range matters as much as the level.
    if "beta_roll" in ind:
        br = ind["beta_roll"].dropna()
        if len(br) > 5:
            back = min(len(br) - 1, max(1, periods_per_year // 4))
            stats["beta_now"] = round(float(br.iloc[-1]), 2)
            stats["beta_prev"] = round(float(br.iloc[-1 - back]), 2)
            stats["beta_min"] = round(float(br.min()), 2)
            stats["beta_max"] = round(float(br.max()), 2)
    if "alpha_roll" in ind:
        ar = ind["alpha_roll"].dropna()
        if len(ar):
            stats["alpha_roll"] = round(float(ar.iloc[-1]), 2)

    stats["percentiles"] = percentile_snapshot(df, ind, periods_per_year)

    return stats


def technical_rating(df: pd.DataFrame, ind: pd.DataFrame, ma: dict) -> dict:
    """TradingView-style aggregate: separate MA and oscillator votes, then a blend."""
    close = float(df["Close"].iloc[-1])

    def last(col, frame=None):
        f = frame if frame is not None else ind
        if col not in f:
            return None
        v = f[col].iloc[-1]
        return float(v) if v is not None and np.isfinite(v) else None

    ma_votes: list[tuple[str, int]] = []
    for name, series in ma.items():
        v = series.iloc[-1] if len(series) else None
        if v is None or not np.isfinite(v):
            continue
        ma_votes.append((name, 1 if close > float(v) else -1))
    for name in ("ema20", "ema50", "ema200", "vwap20"):
        v = last(name)
        if v is not None:
            ma_votes.append((name.upper(), 1 if close > v else -1))
    st_dir = last("st_dir")
    if st_dir is not None:
        ma_votes.append(("SuperTrend", 1 if st_dir > 0 else -1))
    ich_a, ich_b = last("ich_a"), last("ich_b")
    if ich_a is not None and ich_b is not None:
        cloud_top, cloud_bot = max(ich_a, ich_b), min(ich_a, ich_b)
        ma_votes.append(("Ichimoku", 1 if close > cloud_top else (-1 if close < cloud_bot else 0)))

    osc_votes: list[tuple[str, int]] = []

    def bounded(name, v, buy, sell, invert=False):
        if v is None:
            return
        vote = 0
        if v <= buy:
            vote = 1
        elif v >= sell:
            vote = -1
        osc_votes.append((name, -vote if invert else vote))

    bounded("RSI(14)", last("_rsi"), 30, 70)
    bounded("Stoch RSI", last("stochrsi_k"), 20, 80)
    bounded("CCI(20)", last("cci"), -100, 100)
    bounded("MFI(14)", last("mfi"), 20, 80)
    bounded("Williams %R", last("_willr"), -80, -20)

    cmf = last("cmf")
    if cmf is not None:
        osc_votes.append(("CMF(20)", 1 if cmf > 0.05 else (-1 if cmf < -0.05 else 0)))
    macd_v, sig_v = last("_macd"), last("_signal")
    if macd_v is not None and sig_v is not None:
        osc_votes.append(("MACD", 1 if macd_v > sig_v else -1))
    adx_v, dip, dim = last("adx"), last("di_plus"), last("di_minus")
    if adx_v is not None and dip is not None and dim is not None:
        osc_votes.append(("ADX/DMI", (1 if dip > dim else -1) if adx_v >= 20 else 0))
    au, ad = last("aroon_up"), last("aroon_dn")
    if au is not None and ad is not None:
        osc_votes.append(("Aroon(25)", 1 if au > 70 and ad < 30 else (-1 if ad > 70 and au < 30 else 0)))
    mom = last("sqz_mom")
    if mom is not None:
        osc_votes.append(("Squeeze Mom", 1 if mom > 0 else -1))

    def score(votes):
        if not votes:
            return 0.0, 0, 0, 0
        buy = sum(1 for _, v in votes if v > 0)
        sell = sum(1 for _, v in votes if v < 0)
        neutral = len(votes) - buy - sell
        return (buy - sell) / len(votes), buy, neutral, sell

    ma_score, ma_b, ma_n, ma_s = score(ma_votes)
    osc_score, osc_b, osc_n, osc_s = score(osc_votes)
    overall = (ma_score + osc_score) / 2

    def label(s):
        if s >= 0.5:
            return "Strong Buy"
        if s >= 0.15:
            return "Buy"
        if s <= -0.5:
            return "Strong Sell"
        if s <= -0.15:
            return "Sell"
        return "Neutral"

    return {
        "overall": {"score": round(overall, 3), "label": label(overall)},
        "moving_averages": {"score": round(ma_score, 3), "label": label(ma_score),
                            "buy": ma_b, "neutral": ma_n, "sell": ma_s},
        "oscillators": {"score": round(osc_score, 3), "label": label(osc_score),
                        "buy": osc_b, "neutral": osc_n, "sell": osc_s},
        "detail": [{"name": n, "vote": v} for n, v in (ma_votes + osc_votes)],
    }


def compute_analytics(df: pd.DataFrame, ind: pd.DataFrame,
                      bench_close: pd.Series | None = None,
                      periods_per_year: int = 252,
                      ma: dict | None = None) -> dict:
    # One year of bars, but never so few that the profile/swing is noise —
    # a monthly chart only has 12 bars per year.
    window = max(periods_per_year, 60)
    return {
        "pivots": {
            "monthly": pivot_points(df, "ME"),
            "weekly": pivot_points(df, "W"),
        },
        "volume_profile": volume_profile(df, lookback=window),
        "fib": fib_retracement(df, lookback=window),
        "stats": risk_stats(df, ind, bench_close, periods_per_year),
        "rating": technical_rating(df, ind, ma or {}),
    }
