#!/usr/bin/env python3
"""Technical read + price chart for one ticker (deterministic part of /earnings-note).

Everything here is measured off the price series, not judged: trend state from
the moving-average stack, momentum from RSI/MACD, volatility from ATR, and
support/resistance from clustered swing pivots plus the levels that actually act
like levels (52-week extremes, the 50/250-day averages, unfilled earnings gaps).

Indicator formulas mirror the stock-radar chart endpoint
(backend/app/routers/chart.py) bar for bar, so the note and the app's price
panel never quote different numbers for the same day.

Also renders the chart the note embeds, laid out like that price panel:
daily candles with the MA stack and Bollinger envelope, labelled
support/resistance lines, earnings-day markers, then volume, MACD and RSI
sub-panels underneath.

    price_technicals.py NVDA --report-date 2026-02-25 \
        --outdir /path/to/workdir --label "NVDA FY2026Q4"

Prints JSON to stdout; writes <outdir>/<TICKER>_price_chart.png.
"""
import argparse
import datetime as dt
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import registry

# Validated categorical palette (dataviz skill, light surface #fcfcfb):
# blue/orange/violet pass the lightness band, chroma floor and CVD separation,
# and every line carries a direct label so identity never rides on colour.
# Candles are the one deliberate exception: green-up/red-down is the domain
# convention, and direction is redundantly encoded by body position anyway.
C_UP, C_DOWN = "#1f9d63", "#d1473c"
C_MA20, C_MA50, C_MA250 = "#2a78d6", "#eb6834", "#7a5af5"
C_BB = "#8d8dd8"
C_INK, C_MUTED, C_GRID, C_SURFACE = "#1c1c1a", "#6b6b66", "#e4e4e0", "#fcfcfb"
C_SUPPORT, C_RESIST = "#0f8f63", "#c0392b"


def radar_ticker(ticker):
    company = registry.get_company(ticker)
    return (company or {}).get("radar_ticker") or ticker.upper()


def known_earnings_dates(ticker):
    """Report dates already recorded in the registry, newest first."""
    try:
        with registry.connect() as con:
            rows = con.execute(
                "SELECT quarter, report_date, reaction_pct FROM earnings_events "
                "WHERE ticker=? AND report_date IS NOT NULL ORDER BY report_date DESC",
                (ticker.upper(),),
            ).fetchall()
        return [{"quarter": r["quarter"], "date": r["report_date"],
                 "reaction_pct": r["reaction_pct"]} for r in rows]
    except Exception:
        return []


CACHE = pathlib.Path.home() / ".arthur-digest" / "price_cache"


def _fetch_chart_api(ticker, years=2):
    """Yahoo's public chart endpoint — the way in when yfinance is rate-limited.

    yfinance needs a cookie+crumb handshake that Yahoo throttles hard once a
    batch run has quoted a few dozen tickers; this endpoint needs neither, so it
    usually still answers when the library has been locked out. It returns raw
    OHLC plus adjclose, so back-adjust to match yfinance's auto_adjust=True and
    keep cached and freshly fetched bars on the same basis.
    """
    import pandas as pd
    import requests
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}",
        params={"range": f"{years}y", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    res = ((r.json().get("chart") or {}).get("result") or [None])[0]
    if not res:
        return None
    ts = res.get("timestamp") or []
    q = (((res.get("indicators") or {}).get("quote") or [{}])[0])
    if not ts or not q:
        return None
    df = pd.DataFrame({
        "Open": q.get("open") or [], "High": q.get("high") or [],
        "Low": q.get("low") or [], "Close": q.get("close") or [],
        "Volume": q.get("volume") or [],
    }, index=pd.to_datetime(ts, unit="s", utc=True))
    adj = (((res.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose")
    if adj:
        ratio = pd.Series(adj, index=df.index) / df["Close"]
        for col in ("Open", "High", "Low", "Close"):
            df[col] = df[col] * ratio
    return df.dropna(subset=["Open", "High", "Low", "Close"])


def fetch_history(ticker, years=2):
    """Daily OHLCV, cached on disk for the day.

    Batch runs over the whole note archive quote dozens of tickers back to back
    and Yahoo rate-limits that, so a same-day re-run must not re-fetch.
    """
    import pandas as pd
    CACHE.mkdir(parents=True, exist_ok=True)
    # Backfilling an old quarter needs bars from before the default window,
    # so a non-default span gets its own cache file instead of clobbering the
    # 2-year one every other run reads.
    span = "" if years == 2 else f"_{years}y"
    f = CACHE / f"{ticker}_{dt.date.today().isoformat()}{span}.csv"
    if f.exists():
        df = pd.read_csv(f, index_col=0, parse_dates=True)
    else:
        import time

        import yfinance as yf
        start = dt.date.today() - dt.timedelta(days=int(365.25 * years) + 30)
        df = None
        for attempt in range(3):  # Yahoo rate-limits batch runs; back off and retry
            try:
                df = yf.Ticker(ticker).history(start=str(start), interval="1d",
                                               auto_adjust=True)
                if len(df):
                    break
            except Exception:
                pass
            time.sleep(3 * (attempt + 1))
        if df is None or not len(df):
            df = _fetch_chart_api(ticker, years)
        if df is not None and len(df):
            df.to_csv(f)
    if df is None or not len(df):
        return pd.DataFrame()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    # A cached CSV round-trips the index as tz-aware strings, which pandas hands
    # back as a plain object Index — enough for arithmetic, but not a
    # DatetimeIndex, so date-aware plotting breaks only on cache hits. Normalise
    # here so disk and Yahoo produce the same tz-naive daily index.
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None).floor("D")
    return df


def indicators(df):
    """Every series here uses the same formula as the stock-radar chart endpoint
    (backend/app/routers/chart.py::_compute_indicators), so a level or a reading
    quoted in the note matches what the app's price panel draws."""
    import pandas as pd
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    out = {}
    for n in (20, 50, 250):
        out[f"sma{n}"] = close.rolling(n).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    out["rsi"] = 100 - (100 / (1 + gain / (loss + 1e-9)))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    prev_close = close.shift(1)
    tr = (high - low).combine((high - prev_close).abs(), max).combine(
        (low - prev_close).abs(), max)
    out["atr"] = tr.rolling(14).mean()

    # Bollinger (20, 2)
    mid = close.rolling(20).mean()
    sd = close.rolling(20).std(ddof=0)
    out["bb_mid"], out["bb_upper"], out["bb_lower"] = mid, mid + 2 * sd, mid - 2 * sd

    # KDJ (9, 3, 3)
    low9, high9 = low.rolling(9).min(), high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9 + 1e-9) * 100
    out["k"] = rsv.ewm(com=2, adjust=False).mean()
    out["d"] = out["k"].ewm(com=2, adjust=False).mean()
    out["j"] = 3 * out["k"] - 2 * out["d"]

    # Williams %R (14)
    hh14, ll14 = high.rolling(14).max(), low.rolling(14).min()
    out["willr"] = (hh14 - close) / (hh14 - ll14 + 1e-9) * -100

    # OBV + volume baseline
    sign = pd.Series(0.0, index=close.index)
    sign[close > close.shift(1)] = 1.0
    sign[close < close.shift(1)] = -1.0
    out["obv"] = (sign * vol).cumsum()
    out["vol_ma20"] = vol.rolling(20).mean()

    out["td_setup"] = _td_setup(close)
    return out


def _td_setup(close, length=9, lookback=4):
    """神奇九轉 / TD Setup — same rule as backend/app/indicators.py::td_setup.

    Counts bars closing below (buy setup, returned negative) or above (sell
    setup, positive) the close `lookback` bars earlier. A failed comparison
    resets the run, and a completed one resets to zero, so ±`length` lands on
    exactly the 低9 / 高9 signal bars.
    """
    import numpy as np
    import pandas as pd
    c = close.to_numpy(dtype=float)
    out = np.zeros(len(c))
    count = 0
    for i in range(len(c)):
        if i < lookback or not np.isfinite(c[i]) or not np.isfinite(c[i - lookback]):
            count = 0
            continue
        if c[i] < c[i - lookback]:
            count = count - 1 if count < 0 else -1
        elif c[i] > c[i - lookback]:
            count = count + 1 if count > 0 else 1
        else:
            count = 0
        out[i] = count
        if abs(count) >= length:
            count = 0
    return pd.Series(out, index=close.index)


def _f(series, i=-1):
    """Last (or i-th) value as float, None when NaN or out of range."""
    try:
        v = float(series.iloc[i])
    except (IndexError, TypeError, ValueError):
        return None
    return v if v == v else None


def signal_alerts(df, ind):
    """Discrete indicator events on the latest bar.

    Same rule set as the stock-radar price panel's alert engine
    (frontend/src/components/PriceChartPanel.jsx::computeAlerts), so the note's
    technical section cites exactly what the app flags — plus one volume rule
    (OBV divergence) the app doesn't surface but earnings reactions need.
    """
    out = []

    def add(name, kind, side, value, desc):
        out.append({"indicator": name, "type": kind, "side": side,
                    "value": value, "desc": desc})

    close = _f(df["Close"])
    rsi, rsi_p = _f(ind["rsi"]), _f(ind["rsi"], -2)
    if rsi is not None:
        if rsi >= 70:
            add("RSI", "超買", "偏空", round(rsi, 1), f"RSI {rsi:.1f} 高於 70，短線過熱")
        elif rsi <= 30:
            add("RSI", "超賣", "偏多", round(rsi, 1), f"RSI {rsi:.1f} 低於 30，短線超跌")
        elif rsi_p is not None and rsi >= 60 > rsi_p:
            add("RSI", "動能轉強", "中性", round(rsi, 1), "RSI 向上穿過 60")
        elif rsi_p is not None and rsi <= 40 < rsi_p:
            add("RSI", "動能轉弱", "中性", round(rsi, 1), "RSI 向下跌破 40")

    td = _f(ind["td_setup"])
    if td:
        n, buy = abs(int(td)), td < 0
        if n >= 9:
            add("神奇九轉", "低9" if buy else "高9", "偏多" if buy else "偏空", f"{n}/9",
                "連 9 根收盤低於 4 根前，賣壓衰竭，留意反轉" if buy
                else "連 9 根收盤高於 4 根前，追價力竭，留意回檔")
        elif n >= 6:
            add("神奇九轉", "計數中", "中性", f"{n}/9",
                f"{'低' if buy else '高'}九計數 {n}/9，還差 {9 - n} 根成訊")

    wr = _f(ind["willr"])
    if wr is not None:
        if wr >= -20:
            add("Williams %R", "超買", "偏空", round(wr, 1), f"%R {wr:.1f} 高於 -20")
        elif wr <= -80:
            add("Williams %R", "超賣", "偏多", round(wr, 1), f"%R {wr:.1f} 低於 -80")

    k, k_p = _f(ind["k"]), _f(ind["k"], -2)
    d, d_p = _f(ind["d"]), _f(ind["d"], -2)
    j = _f(ind["j"])
    if j is not None:
        if j >= 80:
            add("KDJ", "超買", "偏空", round(j, 1), f"J 值 {j:.1f} 高於 80")
        elif j <= 20:
            add("KDJ", "超賣", "偏多", round(j, 1), f"J 值 {j:.1f} 低於 20")
    if k is not None and k_p is not None and d is not None and d_p is not None:
        if k_p <= d_p and k > d:
            add("KDJ", "黃金交叉", "偏多", round(k, 1), "K 線向上穿過 D 線")
        elif k_p >= d_p and k < d:
            add("KDJ", "死亡交叉", "偏空", round(k, 1), "K 線向下跌破 D 線")

    m, m_p = _f(ind["macd"]), _f(ind["macd"], -2)
    s, s_p = _f(ind["macd_signal"]), _f(ind["macd_signal"], -2)
    h, h_p = _f(ind["macd_hist"]), _f(ind["macd_hist"], -2)
    if m is not None and m_p is not None and s is not None and s_p is not None:
        if m_p <= s_p and m > s:
            add("MACD", "黃金交叉", "偏多", round(m, 3), "MACD 向上穿過訊號線")
        elif m_p >= s_p and m < s:
            add("MACD", "死亡交叉", "偏空", round(m, 3), "MACD 向下跌破訊號線")
    if h is not None and h_p is not None:
        if h_p < 0 <= h:
            add("MACD", "柱狀體翻正", "偏多", round(h, 3), "動能由負轉正")
        elif h_p > 0 >= h:
            add("MACD", "柱狀體翻負", "偏空", round(h, 3), "動能由正轉負")

    up, lo = _f(ind["bb_upper"]), _f(ind["bb_lower"])
    if close is not None and up is not None and lo is not None:
        if close >= up:
            add("布林通道", "觸及上軌", "偏空", round(close, 2),
                f"收盤 {close:.2f} 已達上軌 {up:.2f}")
        elif close <= lo:
            add("布林通道", "觸及下軌", "偏多", round(close, 2),
                f"收盤 {close:.2f} 已達下軌 {lo:.2f}")
        bw = up - lo
        prev_bw = [(_f(ind["bb_upper"], -i) or 0) - (_f(ind["bb_lower"], -i) or 0)
                   for i in range(2, 12)]
        prev_bw = [b for b in prev_bw if b > 0]
        if len(prev_bw) == 10 and bw < sum(prev_bw) / 10 * 0.7:
            add("布林通道", "帶寬收斂", "中性", round(bw, 2),
                "波動壓縮，通常是變盤前兆")

    # OBV divergence over the last 20 sessions: price and cumulative volume flow
    # disagreeing is the tell that a post-earnings move lacks participation.
    obv, obv_20 = _f(ind["obv"]), _f(ind["obv"], -21)
    close_20 = _f(df["Close"], -21)
    if (obv is not None and obv_20 is not None and close is not None
            and close_20):
        price_chg = (close / close_20 - 1) * 100
        if price_chg > 2 and obv < obv_20:
            add("OBV", "量價背離", "偏空", round(price_chg, 1),
                f"近 20 日股價 {price_chg:+.1f}% 但 OBV 下滑，追價量能不足")
        elif price_chg < -2 and obv > obv_20:
            add("OBV", "量價背離", "偏多", round(price_chg, 1),
                f"近 20 日股價 {price_chg:+.1f}% 但 OBV 上升，賣壓在被吸收")
    return out


def _td_block(td):
    """神奇九轉 state: how far the current run has counted, and the last signal.

    `bars_ago` is what decides whether a 9 still matters — a 低9 from 30 bars
    back is history, one from 3 bars back is the setup the note is about.
    """
    seq = td.dropna()
    if seq.empty:
        return None
    cur = int(seq.iloc[-1])
    last_sig = None
    hits = seq[seq.abs() >= 9]
    if not hits.empty:
        v = int(hits.iloc[-1])
        last_sig = {
            "type": "低9" if v < 0 else "高9",
            "side": "偏多（賣壓衰竭）" if v < 0 else "偏空（追價力竭）",
            "date": str(hits.index[-1])[:10],
            "bars_ago": int(len(seq) - seq.index.get_loc(hits.index[-1]) - 1),
        }
    return {
        "count": abs(cur),
        "direction": None if cur == 0 else ("買訊計數（低九）" if cur < 0 else "賣訊計數（高九）"),
        "completed": abs(cur) >= 9,
        "last_signal": last_sig,
        "signals_12m": int((seq[seq.index >= seq.index[-1] - dt.timedelta(days=365)].abs() >= 9).sum()),
    }


def momentum_block(df, ind):
    close = _f(df["Close"])
    rsi = _f(ind["rsi"])
    m, s, h = _f(ind["macd"]), _f(ind["macd_signal"]), _f(ind["macd_hist"])
    k, d, j = _f(ind["k"]), _f(ind["d"]), _f(ind["j"])
    wr = _f(ind["willr"])
    obv, obv_20 = _f(ind["obv"]), _f(ind["obv"], -21)
    vma = _f(ind["vol_ma20"])

    rsi_state = "中性（40 到 60 之間）"
    if rsi is not None:
        if rsi >= 70:
            rsi_state = "超買（>70）"
        elif rsi <= 30:
            rsi_state = "超賣（<30）"
        elif rsi >= 60:
            rsi_state = "偏強（60 以上）"
        elif rsi <= 40:
            rsi_state = "偏弱（40 以下）"

    kdj_state = None
    if k is not None and d is not None:
        kdj_state = ("K 在 D 之上（偏多）" if k > d else "K 在 D 之下（偏空）")
        if j is not None and j >= 80:
            kdj_state += "，J 值超買"
        elif j is not None and j <= 20:
            kdj_state += "，J 值超賣"

    return {
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "rsi_state": rsi_state,
        "macd": round(m, 2) if m is not None else None,
        "macd_signal": round(s, 2) if s is not None else None,
        "macd_hist": round(h, 3) if h is not None else None,
        "macd_state": ("多方（MACD 在訊號線之上）" if (m or 0) > (s or 0)
                       else "空方（MACD 在訊號線之下）"),
        "kdj": {"k": round(k, 1) if k is not None else None,
                "d": round(d, 1) if d is not None else None,
                "j": round(j, 1) if j is not None else None,
                "state": kdj_state},
        "td_sequential": _td_block(ind["td_setup"]),
        "williams_r14": round(wr, 1) if wr is not None else None,
        # OBV is a running total from the start of the series, so its level is
        # arbitrary and a percent change against it is meaningless (a base near
        # zero prints hundreds of percent). Normalise the 20-day move by average
        # daily volume instead: "net buying worth N days of turnover".
        "obv_20d_net_days": (round((obv - obv_20) / vma, 1)
                             if obv is not None and obv_20 is not None and vma
                             else None),
        "close": round(close, 2) if close is not None else None,
    }


def bollinger_block(df, ind):
    up, mid, lo = _f(ind["bb_upper"]), _f(ind["bb_mid"]), _f(ind["bb_lower"])
    close = _f(df["Close"])
    if up is None or mid is None or lo is None or close is None or up <= lo:
        return None
    pct_b = (close - lo) / (up - lo) * 100
    state = "在中軌附近"
    if close >= up:
        state = "貼上軌，短線過熱"
    elif close <= lo:
        state = "貼下軌，短線超跌"
    elif pct_b > 65:
        state = "在中軌之上，偏強"
    elif pct_b < 35:
        state = "在中軌之下，偏弱"
    return {
        "upper": round(up, 2), "mid": round(mid, 2), "lower": round(lo, 2),
        "pct_b": round(pct_b, 1),
        "bandwidth_pct": round((up - lo) / mid * 100, 1),
        "state": state,
    }


def _pivots(df, window=5):
    """Fractal swing highs/lows: a bar that is the extreme of its neighbourhood."""
    highs, lows = [], []
    h, l = df["High"].values, df["Low"].values
    idx = df.index
    for i in range(window, len(df) - window):
        seg_h, seg_l = h[i - window:i + window + 1], l[i - window:i + window + 1]
        if h[i] == seg_h.max():
            highs.append((idx[i], float(h[i])))
        if l[i] == seg_l.min():
            lows.append((idx[i], float(l[i])))
    return highs, lows


def _cluster(points, tol_pct, kind):
    """Group nearby pivots into one level; more touches and fresher touches win."""
    if not points:
        return []
    points = sorted(points, key=lambda p: p[1])
    clusters, cur = [], [points[0]]
    for date_, price in points[1:]:
        if abs(price - cur[-1][1]) / cur[-1][1] * 100 <= tol_pct:
            cur.append((date_, price))
        else:
            clusters.append(cur)
            cur = [(date_, price)]
    clusters.append(cur)

    levels = []
    newest = max(p[0] for p in points)
    for c in clusters:
        price = sum(p[1] for p in c) / len(c)
        last = max(p[0] for p in c)
        age_days = (newest - last).days
        recency = max(0.2, 1 - age_days / 540)
        levels.append({
            "price": round(price, 2),
            "touches": len(c),
            "last_touch": str(last)[:10],
            "kind": kind,
            "score": round(len(c) * recency, 2),
        })
    return levels


def reaction_session(df, report_date):
    """Which session actually priced the report, and the close before it.

    Companies split roughly evenly between reporting after the close (the move
    lands the next session) and before the open (it lands that same session), so
    picking by calendar alone gets half of them wrong — VRT's -17.3% is on the
    report date itself.

    Score both candidates on volume against their own 60-day average, weighted by
    how far the session gapped from the prior close. Volume alone is not enough:
    FORM sold off into the print on slightly heavier volume than the +26% session
    that followed (1.33x vs 1.31x), so a pure volume rule picked the day before
    the report. A 22% opening gap identifies the session that priced the news far
    more reliably than a 2% volume difference.

    Returns (index_position_of_reaction_bar, timing_label) or (None, None).
    """
    try:
        day = dt.date.fromisoformat(report_date)
    except (TypeError, ValueError):
        return None, None
    dates = [d.date() for d in df.index]
    same = next((i for i, d in enumerate(dates) if d == day), None)
    after = next((i for i, d in enumerate(dates) if d > day), None)

    avg = df["Volume"].rolling(60, min_periods=10).mean()

    def score(i):
        if i is None or i == 0:
            return -1
        base = float(avg.iloc[i - 1]) if avg.iloc[i - 1] == avg.iloc[i - 1] else 0
        if not base:
            return -1
        vol_ratio = float(df["Volume"].iloc[i]) / base
        prev_close = float(df["Close"].iloc[i - 1])
        gap = abs(float(df["Open"].iloc[i]) / prev_close - 1) * 100 if prev_close else 0
        return vol_ratio * (1 + gap / 10)

    best = max([same, after], key=score)
    if best is None or best == 0 or score(best) < 0:
        return (after, "盤後") if after else (None, None)
    return best, ("盤前" if best == same else "盤後")


def _gap_levels(df, earnings_dates):
    """Earnings gaps that never filled keep acting as support/resistance."""
    out = []
    for ev in earnings_dates[:8]:
        i, _ = reaction_session(df, ev.get("date"))
        if not i:
            continue
        prev_close = float(df["Close"].iloc[i - 1])
        gap_pct = (float(df["Open"].iloc[i]) / prev_close - 1) * 100
        if abs(gap_pct) < 3:
            continue
        since = df.iloc[i + 1:]
        filled = bool((since["Low"] <= prev_close).any()) if gap_pct > 0 else \
                 bool((since["High"] >= prev_close).any())
        out.append({
            "price": round(prev_close, 2),
            "touches": 1,
            "last_touch": ev["date"],
            "kind": f"{ev.get('quarter') or ev['date']} 財報跳空缺口（{gap_pct:+.1f}%{'，已回補' if filled else '，未回補'}）",
            "score": 1.2 if not filled else 0.6,
            "filled": filled,
        })
    return out


def build_levels(df, ind, earnings_dates, last_close):
    atr_pct = float(ind["atr"].iloc[-1]) / last_close * 100
    tol = max(1.0, min(3.0, atr_pct * 0.6))

    highs, lows = _pivots(df)
    raw = _cluster(highs, tol, "前波高點") + _cluster(lows, tol, "前波低點")
    raw += _gap_levels(df, earnings_dates)

    yr = df[df.index >= df.index[-1] - dt.timedelta(days=365)]
    raw.append({"price": round(float(yr["High"].max()), 2), "touches": 1,
                "last_touch": str(yr["High"].idxmax())[:10], "kind": "52 週高點", "score": 2.0})
    raw.append({"price": round(float(yr["Low"].min()), 2), "touches": 1,
                "last_touch": str(yr["Low"].idxmin())[:10], "kind": "52 週低點", "score": 2.0})
    for n, name in ((50, "50 日均線"), (250, "250 日均線")):
        v = ind[f"sma{n}"].iloc[-1]
        if v == v:  # not NaN
            raw.append({"price": round(float(v), 2), "touches": 1,
                        "last_touch": str(df.index[-1])[:10], "kind": name, "score": 1.5})

    def dress(level, role):
        level = dict(level)
        level["dist_pct"] = round((level["price"] / last_close - 1) * 100, 1)
        # Strength on the same 1-5 scale and 強/中/弱 wording as the app's
        # support/resistance card, so a level reads the same in both places.
        level["strength"] = max(1, min(5, round(math.log1p(level["score"]) * 2.8)))
        level["tier"] = ("強" if level["strength"] >= 4 or level["touches"] >= 3
                         else "弱" if level["strength"] <= 1 else "中")
        # A pivot changes job when price crosses it: an old low sitting above
        # today's price is resistance now, and an old high below it is support.
        if role == "support" and level["kind"] == "前波高點":
            level["kind"] = "前波高點（突破後轉支撐）"
        elif role == "resistance" and level["kind"] == "前波低點":
            level["kind"] = "前波低點（跌破後轉壓力）"
        return level

    def pick(levels):
        """Keep the strongest levels, then merge ones close enough to be the same
        wall (pivot highs and lows are clustered separately and can overlap)."""
        levels.sort(key=lambda l: (-l["score"] * 0.4 + abs(l["dist_pct"]) * 0.1))
        kept = []
        for lv in levels:
            if any(abs(lv["price"] - k["price"]) / k["price"] * 100 <= tol for k in kept):
                continue
            kept.append(lv)
            if len(kept) == 4:
                break
        kept.sort(key=lambda l: abs(l["dist_pct"]))  # nearest level first
        return kept

    support = pick([dress(l, "support") for l in raw
                    if last_close * 0.72 < l["price"] < last_close * 0.995])
    resist = pick([dress(l, "resistance") for l in raw
                   if last_close * 1.005 < l["price"] < last_close * 1.30])
    return support, resist


def post_earnings(df, report_date):
    if not report_date:
        return None
    i, timing = reaction_session(df, report_date)
    if not i:
        return None
    pre_close = float(df["Close"].iloc[i - 1])
    d1 = df.iloc[i]
    after = df.iloc[i:]
    avg_vol = float(df["Volume"].iloc[max(0, i - 60):i].mean())
    out = {
        "report_timing": timing,
        "reaction_date": str(df.index[i])[:10],
        "pre_earnings_close": round(pre_close, 2),
        "gap_open_pct": round((float(d1["Open"]) / pre_close - 1) * 100, 2),
        "reaction_close": round(float(d1["Close"]), 2),
        "reaction_pct": round((float(d1["Close"]) / pre_close - 1) * 100, 2),
        "days_since": len(after) - 1,
        "cum_since_earnings_pct": round((float(df["Close"].iloc[-1]) / pre_close - 1) * 100, 2),
    }
    if avg_vol:
        out["volume_vs_60d_avg"] = round(float(d1["Volume"]) / avg_vol, 2)
    if out["gap_open_pct"] > 0:
        out["gap_filled"] = bool((after["Low"] <= pre_close).any())
    elif out["gap_open_pct"] < 0:
        out["gap_filled"] = bool((after["High"] >= pre_close).any())
    return out


def trend_state(ind, last_close):
    s20, s50, s250 = (float(ind[f"sma{n}"].iloc[-1]) if ind[f"sma{n}"].iloc[-1] == ind[f"sma{n}"].iloc[-1]
                      else None for n in (20, 50, 250))
    state = "資料不足"
    if s20 and s50 and s250:
        if last_close > s20 > s50 > s250:
            state = "多頭排列（價 > 20 > 50 > 250 日均）"
        elif last_close < s20 < s50 < s250:
            state = "空頭排列（價 < 20 < 50 < 250 日均）"
        elif last_close > s250:
            state = "長多短亂（站在 250 日均之上，短均糾結）"
        else:
            state = "跌破長期均線，趨勢轉弱"

    cross = None
    if s50 and s250:
        diff = ind["sma50"] - ind["sma250"]
        sign = diff > 0
        flips = sign.ne(sign.shift()).to_numpy().nonzero()[0]
        if len(flips) > 1:
            i = flips[-1]
            cross = {"type": "黃金交叉" if bool(sign.iloc[i]) else "死亡交叉",
                     "date": str(diff.index[i])[:10],
                     "bars_ago": int(len(diff) - i)}
    return {
        "sma20": round(s20, 2) if s20 else None,
        "sma50": round(s50, 2) if s50 else None,
        "sma250": round(s250, 2) if s250 else None,
        "vs_sma50_pct": round((last_close / s50 - 1) * 100, 1) if s50 else None,
        "vs_sma250_pct": round((last_close / s250 - 1) * 100, 1) if s250 else None,
        "state": state,
        "last_cross": cross,
    }


def _frame(a):
    a.set_facecolor(C_SURFACE)
    a.grid(True, color=C_GRID, linewidth=0.7, zorder=0)
    a.set_axisbelow(True)
    for side in ("top", "right", "left"):
        a.spines[side].set_visible(False)
    a.spines["bottom"].set_color(C_GRID)
    a.tick_params(colors=C_MUTED, labelsize=8.5, length=0)


def _right_labels(ax, x, items, size=8.5, gap_frac=0.075):
    """Direct labels off the right edge — identity never rides on colour alone.

    Series that end close together (MACD and its signal line, price sitting on a
    moving average) would print on top of each other, so labels are nudged up
    until they clear; the text still quotes the true value.
    Call after the panel's y-range is settled.
    """
    lo, hi = ax.get_ylim()
    gap = (hi - lo) * gap_frac
    placed = []
    for value, text, color in sorted(items, key=lambda t: t[0]):
        y = value
        while any(abs(y - p) < gap for p in placed):
            y += gap
        placed.append(y)
        ax.annotate(text, xy=(x, y), xytext=(7, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=size, color=color,
                    fontweight="medium", annotation_clip=False)


def render_chart(df, ind, support, resist, earnings_dates, ticker, label, outpath,
                 months=12, panels=("macd", "rsi")):
    """Candles + MA stack + Bollinger + S/R on top, then volume and the
    requested oscillator panels — the same stack the stock-radar price panel
    shows, flattened into one static image the note can embed."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.ticker import FuncFormatter

    plt.rcParams["font.sans-serif"] = ["PingFang TC", "PingFang HK", "Heiti TC",
                                       "Hiragino Sans CNS", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False

    cutoff = df.index[-1] - dt.timedelta(days=int(30.5 * months))
    view = df[df.index >= cutoff]
    if view.empty:
        return None
    sub = {k: s.reindex(view.index) for k, s in ind.items()}

    panels = [p for p in panels if p in ("macd", "rsi", "kdj", "willr", "obv")]
    heights = [3.4, 0.85] + [1.0] * len(panels)
    fig, axes = plt.subplots(
        len(heights), 1, figsize=(12, 1.32 * sum(heights) + 1.0), sharex=True,
        gridspec_kw={"height_ratios": heights, "hspace": 0.08})
    ax, axv = axes[0], axes[1]
    pane = dict(zip(panels, axes[2:]))
    fig.patch.set_facecolor(C_SURFACE)
    for a in axes:
        _frame(a)

    x = mdates.date2num(view.index.to_pydatetime())
    o = view["Open"].to_numpy(dtype=float)
    h = view["High"].to_numpy(dtype=float)
    lo_ = view["Low"].to_numpy(dtype=float)
    c = view["Close"].to_numpy(dtype=float)
    up = c >= o
    span = float(h.max() - lo_.min())
    body = c - o
    # A doji has zero body and would vanish; give it a hairline so the session
    # still reads as a bar rather than a gap in the series.
    body = np.where(np.abs(body) < span * 0.0012, span * 0.0012, body)
    w = 0.62

    # Bollinger envelope first — it is context, everything else sits on top.
    bb_u, bb_l = sub["bb_upper"].to_numpy(float), sub["bb_lower"].to_numpy(float)
    if np.isfinite(bb_u).sum() > 5:
        ok = np.isfinite(bb_u) & np.isfinite(bb_l)
        ax.fill_between(x[ok], bb_l[ok], bb_u[ok], color=C_BB, alpha=0.10,
                        linewidth=0, zorder=1)
        ax.plot(x[ok], bb_u[ok], color=C_BB, linewidth=0.9, alpha=0.55,
                linestyle=(0, (4, 3)), zorder=2)
        ax.plot(x[ok], bb_l[ok], color=C_BB, linewidth=0.9, alpha=0.55,
                linestyle=(0, (4, 3)), zorder=2)

    for mask, color in ((up, C_UP), (~up, C_DOWN)):
        if not mask.any():
            continue
        ax.vlines(x[mask], lo_[mask], h[mask], color=color, linewidth=0.75, zorder=4)
        ax.bar(x[mask], body[mask], bottom=o[mask], width=w, color=color,
               edgecolor=color, linewidth=0.3, zorder=5)

    ma_series = []
    for n, color, name in ((20, C_MA20, "MA20"), (50, C_MA50, "MA50"),
                           (250, C_MA250, "MA250")):
        ma = sub[f"sma{n}"].to_numpy(float)
        if np.isfinite(ma).sum() > 5:
            ax.plot(x, ma, color=color, linewidth=1.5, zorder=6)
            ma_series.append((name, ma, color))

    # Levels can sit outside the visible price range, so fix the y-range first —
    # otherwise the outermost label lands on the frame and gets clipped.
    ys = [float(lo_.min()), float(h.max())] + [l["price"] for l in support + resist]
    pad = (max(ys) - min(ys)) * 0.06
    ax.set_ylim(min(ys) - pad, max(ys) + pad)

    # Support / resistance: dashed rules labelled on the left so the right edge
    # stays free for the direct series labels.
    for role, levels, color in (("支撐", support, C_SUPPORT), ("壓力", resist, C_RESIST)):
        for lv in levels:
            strong = lv.get("strength", 2) >= 4
            ax.axhline(lv["price"], color=color, linewidth=1.4 if strong else 1.0,
                       linestyle=(0, (5, 4)), alpha=0.75 if strong else 0.5, zorder=3)
            ax.annotate(
                f"{role} {lv['price']:g}（{lv['dist_pct']:+.1f}%・{lv.get('tier', '中')}）",
                xy=(0.008, lv["price"]), xycoords=("axes fraction", "data"),
                va="bottom", ha="left", fontsize=8, color=C_MUTED, zorder=8,
                bbox=dict(boxstyle="round,pad=0.18", fc=C_SURFACE, ec="none", alpha=0.85))

    # 神奇九轉 — only completed 9s are labelled; every bar of every run would
    # bury the candles. The running count goes to the JSON, not the picture.
    td_v = sub["td_setup"].to_numpy(float)
    for i in np.nonzero(np.abs(np.nan_to_num(td_v)) >= 9)[0]:
        buy = td_v[i] < 0
        ax.annotate(
            "低9" if buy else "高9",
            xy=(x[i], lo_[i] if buy else h[i]),
            xytext=(0, -14 if buy else 11), textcoords="offset points",
            ha="center", va="top" if buy else "bottom",
            fontsize=8, fontweight="bold", zorder=9,
            color=C_UP if buy else C_DOWN,
            bbox=dict(boxstyle="round,pad=0.15", fc=C_SURFACE, ec="none", alpha=0.7))

    xr = x[-1]
    price_labels = []
    for name, arr, color in [("收盤", c, C_INK)] + ma_series:
        vals = arr[np.isfinite(arr)]
        if len(vals):
            price_labels.append((float(vals[-1]), f"{name} {float(vals[-1]):,.1f}", color))
    _right_labels(ax, xr, price_labels, size=9, gap_frac=0.045)

    # Earnings markers — drawn on every panel so momentum can be read at the date
    for ev in earnings_dates[:6]:
        try:
            d = dt.date.fromisoformat(ev["date"])
        except (TypeError, ValueError):
            continue
        if d < cutoff.date():
            continue
        dx = mdates.date2num(dt.datetime.combine(d, dt.time()))
        for a in axes:
            a.axvline(dx, color=C_INK, linewidth=0.9, alpha=0.3,
                      linestyle=(0, (2, 3)), zorder=1)
        r = ev.get("reaction_pct")
        tag = f"財報 {r:+.1f}%" if r is not None else "財報"
        ax.annotate(tag, xy=(dx, 1.0), xycoords=("data", "axes fraction"),
                    xytext=(3, -10), textcoords="offset points",
                    fontsize=8, color=C_MUTED, ha="left", va="top")

    # ── Volume ────────────────────────────────────────────────────────────
    vol = view["Volume"].to_numpy(float)
    axv.bar(x[up], vol[up], color=C_UP, alpha=0.45, width=w, linewidth=0)
    axv.bar(x[~up], vol[~up], color=C_DOWN, alpha=0.45, width=w, linewidth=0)
    vma = sub["vol_ma20"].to_numpy(float)
    if np.isfinite(vma).sum() > 5:
        axv.plot(x, vma, color=C_MUTED, linewidth=1.1, alpha=0.8)
        _right_labels(axv, xr, [(float(vma[np.isfinite(vma)][-1]), "20 日均量", C_MUTED)],
                      size=8)
    axv.set_ylabel("成交量", color=C_MUTED, fontsize=8.5)
    axv.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: f"{v/1e6:.0f}M" if v < 1e9 else f"{v/1e9:.1f}B"))

    # ── MACD (12,26,9) ────────────────────────────────────────────────────
    if "macd" in pane:
        a = pane["macd"]
        hist = sub["macd_hist"].to_numpy(float)
        pos = hist >= 0
        a.bar(x[pos], hist[pos], color=C_UP, alpha=0.5, width=w, linewidth=0)
        a.bar(x[~pos], hist[~pos], color=C_DOWN, alpha=0.5, width=w, linewidth=0)
        a.plot(x, sub["macd"].to_numpy(float), color=C_MA20, linewidth=1.3)
        a.plot(x, sub["macd_signal"].to_numpy(float), color=C_MA50, linewidth=1.3)
        a.axhline(0, color=C_MUTED, linewidth=0.8, alpha=0.5)
        a.set_ylabel("MACD\n(12,26,9)", color=C_MUTED, fontsize=8.5)
        mv, sv = _f(sub["macd"]) or 0.0, _f(sub["macd_signal"]) or 0.0
        _right_labels(a, xr, [(mv, f"MACD {mv:.2f}", C_MA20),
                              (sv, f"訊號 {sv:.2f}", C_MA50)], gap_frac=0.16)

    # ── RSI (14) ──────────────────────────────────────────────────────────
    if "rsi" in pane:
        a = pane["rsi"]
        r = sub["rsi"].to_numpy(float)
        a.plot(x, r, color=C_MA50, linewidth=1.4)
        a.fill_between(x, 70, r, where=(r >= 70), color=C_DOWN, alpha=0.18, linewidth=0)
        a.fill_between(x, 30, r, where=(r <= 30), color=C_UP, alpha=0.18, linewidth=0)
        a.axhline(70, color=C_DOWN, linewidth=0.9, alpha=0.55, linestyle=(0, (4, 3)))
        a.axhline(30, color=C_UP, linewidth=0.9, alpha=0.55, linestyle=(0, (4, 3)))
        a.axhline(50, color=C_MUTED, linewidth=0.7, alpha=0.35, linestyle=(0, (1, 3)))
        a.set_ylim(0, 100)
        a.set_yticks([30, 50, 70])
        a.set_ylabel("RSI (14)", color=C_MUTED, fontsize=8.5)
        rv = _f(sub["rsi"]) or 50.0
        _right_labels(a, xr, [(rv, f"RSI {rv:.1f}", C_MA50)])

    # ── KDJ (9,3,3) ───────────────────────────────────────────────────────
    if "kdj" in pane:
        a = pane["kdj"]
        kdj_labels = []
        for key, color, name in (("k", C_MA20, "K"), ("d", C_MA50, "D"),
                                 ("j", C_MA250, "J")):
            a.plot(x, sub[key].to_numpy(float), color=color,
                   linewidth=1.3 if key != "j" else 1.0)
            v = _f(sub[key])
            if v is not None:
                kdj_labels.append((v, f"{name} {v:.0f}", color))
        a.axhline(80, color=C_DOWN, linewidth=0.9, alpha=0.5, linestyle=(0, (4, 3)))
        a.axhline(20, color=C_UP, linewidth=0.9, alpha=0.5, linestyle=(0, (4, 3)))
        a.set_ylim(-20, 120)
        a.set_yticks([20, 50, 80])
        a.set_ylabel("KDJ\n(9,3,3)", color=C_MUTED, fontsize=8.5)
        _right_labels(a, xr, kdj_labels, gap_frac=0.11)

    # ── Williams %R (14) ──────────────────────────────────────────────────
    if "willr" in pane:
        a = pane["willr"]
        a.plot(x, sub["willr"].to_numpy(float), color=C_MA250, linewidth=1.3)
        a.axhline(-20, color=C_DOWN, linewidth=0.9, alpha=0.5, linestyle=(0, (4, 3)))
        a.axhline(-80, color=C_UP, linewidth=0.9, alpha=0.5, linestyle=(0, (4, 3)))
        a.set_ylim(-100, 0)
        a.set_yticks([-80, -50, -20])
        a.set_ylabel("Williams %R", color=C_MUTED, fontsize=8.5)
        wv = _f(sub["willr"]) or -50.0
        _right_labels(a, xr, [(wv, f"%R {wv:.0f}", C_MA250)])

    # ── OBV ───────────────────────────────────────────────────────────────
    if "obv" in pane:
        a = pane["obv"]
        a.plot(x, sub["obv"].to_numpy(float), color=C_MA250, linewidth=1.3)
        a.set_ylabel("OBV", color=C_MUTED, fontsize=8.5)
        a.yaxis.set_major_formatter(FuncFormatter(
            lambda v, _: f"{v/1e9:.1f}B" if abs(v) >= 1e9 else f"{v/1e6:.0f}M"))

    # ── Title + stat strip ────────────────────────────────────────────────
    last_c = float(c[-1])
    chg = (last_c / float(c[-2]) - 1) * 100 if len(c) > 1 else 0.0
    atr = _f(sub["atr"])
    yr = df[df.index >= df.index[-1] - dt.timedelta(days=365)]
    hi52, lo52 = float(yr["High"].max()), float(yr["Low"].min())
    pos52 = (last_c - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else 0
    bits = [f"收盤 {last_c:,.2f}（{chg:+.2f}%）",
            f"RSI14 {_f(sub['rsi']) or 0:.1f}",
            "MACD 多方" if (_f(sub["macd"]) or 0) > (_f(sub["macd_signal"]) or 0) else "MACD 空方"]
    if atr:
        bits.append(f"ATR {atr / last_c * 100:.1f}%（2ATR 停損 {last_c - 2 * atr:,.2f}）")
    bits.append(f"52 週區間位置 {pos52:.0f}%")
    bits.append("K 棒綠漲紅跌")

    ax.set_title(f"{label or ticker}　日線 K 線與技術指標（近 {months} 個月）",
                 color=C_INK, fontsize=13.5, pad=26, loc="left")
    ax.annotate("　｜　".join(bits), xy=(0, 1.012), xycoords="axes fraction",
                ha="left", va="bottom", fontsize=9, color=C_MUTED)

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=max(1, round(months / 10))))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.set_xlim(x[0] - 3, x[-1] + 3)
    fig.subplots_adjust(left=0.065, right=0.885, top=0.925, bottom=0.05)
    fig.savefig(outpath, dpi=170, facecolor=C_SURFACE)
    plt.close(fig)
    return str(outpath)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--report-date", default=None)
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--label", default=None, help="chart title, e.g. 'NVDA FY2026Q4'")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--panels", default="macd,rsi",
                    help="oscillator panels under the candles: macd,rsi,kdj,willr,obv")
    ap.add_argument("--no-chart", action="store_true")
    ap.add_argument("--as-of", default=None,
                    help="YYYY-MM-DD: drop every bar after this date, so a backfilled "
                         "note reads the technicals as they stood a week after that "
                         "quarter's report instead of today's")
    a = ap.parse_args()

    t = a.ticker.upper()
    rt = radar_ticker(t)
    years = 2
    if a.as_of:
        # keep ~2 years of history behind the as-of date (250-day MA + 52w range)
        years = 2 + max(0, -(-(dt.date.today() - dt.date.fromisoformat(a.as_of)).days // 365))
    df = fetch_history(rt, years=years)
    if a.as_of and not df.empty:
        df = df[df.index <= dt.datetime.fromisoformat(a.as_of)]
    if df.empty or len(df) < 60:
        json.dump({"ticker": t, "error": "not enough price history"}, sys.stdout)
        print()
        return

    ind = indicators(df)
    last_close = float(df["Close"].iloc[-1])
    events = known_earnings_dates(t)
    if a.as_of:  # a note written as of that date cannot know later quarters
        events = [e for e in events if e["date"] <= a.as_of]
    if a.report_date and not any(e["date"] == a.report_date for e in events):
        events.insert(0, {"quarter": None, "date": a.report_date, "reaction_pct": None})
    support, resist = build_levels(df, ind, events, last_close)
    yr = df[df.index >= df.index[-1] - dt.timedelta(days=365)]
    hi, lo = float(yr["High"].max()), float(yr["Low"].min())
    atr = float(ind["atr"].iloc[-1])

    out = {
        "ticker": t,
        "radar_ticker": rt,
        "as_of": str(df.index[-1])[:10],
        "last_close": round(last_close, 2),
        "trend": trend_state(ind, last_close),
        "range_52w": {
            "high": round(hi, 2), "high_date": str(yr["High"].idxmax())[:10],
            "low": round(lo, 2), "low_date": str(yr["Low"].idxmin())[:10],
            "pct_from_high": round((last_close / hi - 1) * 100, 1),
            "pct_above_low": round((last_close / lo - 1) * 100, 1),
            "position_in_range_pct": round((last_close - lo) / (hi - lo) * 100, 1),
        },
        "momentum": momentum_block(df, ind),
        "bollinger": bollinger_block(df, ind),
        "signals": signal_alerts(df, ind),
        "volatility": {
            "atr14": round(atr, 2),
            "atr_pct": round(atr / last_close * 100, 2),
            "note": "停損寬度建議至少 1.5 到 2 倍 ATR，否則會被日常波動掃出場",
            "stop_1_5atr": round(last_close - 1.5 * atr, 2),
            "stop_2atr": round(last_close - 2 * atr, 2),
        },
        "support": support,
        "resistance": resist,
        "post_earnings": post_earnings(df, a.report_date),
        "earnings_reaction_history": events[:8],
    }

    if not a.no_chart:
        outdir = pathlib.Path(a.outdir).expanduser()
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / f"{t}_price_chart.png"
        try:
            out["chart"] = render_chart(
                df, ind, support, resist, events, t, a.label, path,
                months=a.months,
                panels=tuple(p.strip() for p in a.panels.split(",") if p.strip()))
        except Exception as e:
            out["chart_error"] = f"{type(e).__name__}: {e}"

    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
