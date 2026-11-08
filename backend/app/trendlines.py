"""
Trend structure — the part of technical analysis that is drawn, not computed
per bar: diagonal trendlines, channels, a linear-regression channel, and the
classical chart patterns that fall out of the pivot sequence.

Three entry points:

    regression_channel_frame(df, lookback)
        -> (DataFrame of reg_* series, stats dict)

    compute_trend_structure(df, periods_per_year=252)
        -> dict with trendlines / channel / regression / patterns / summary

Everything is pure pandas/numpy, matching the conventions in indicators.py so
the chart endpoint, the screener and the war-room scripts can share it.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .indicators import adx_dmi

EPS = 1e-9

# 近期擺動用的固定小窗格（見 multi_scale_pivots）
SWING_W = 5


# ── Pivots ───────────────────────────────────────────────────────────────────
def find_pivots(high: np.ndarray, low: np.ndarray, w: int) -> tuple[list[int], list[int]]:
    """Fractal swing pivots: a bar that is the extreme of the ±w window.

    The last `w` bars can never be *confirmed* pivots — there is no right-hand
    window yet — but the newest swing is exactly the one a trader is drawing
    from. So a provisional pivot is allowed in the tail when the bar still
    dominates every bar to its left within the window.
    """
    n = len(high)
    if n < 2 * w + 3:
        return [], []

    hi = pd.Series(high)
    lo = pd.Series(low)
    rmax = hi.rolling(2 * w + 1, center=True).max().values
    rmin = lo.rolling(2 * w + 1, center=True).min().values

    ph = [i for i in range(w, n - w) if high[i] >= rmax[i] - EPS]
    pl = [i for i in range(w, n - w) if low[i] <= rmin[i] + EPS]

    # Provisional tail pivots (left window only).
    tail = range(max(n - w, w), n)
    cand_h = max(tail, key=lambda i: high[i], default=None)
    if cand_h is not None and high[cand_h] >= high[cand_h - w:cand_h].max() - EPS:
        ph.append(cand_h)
    cand_l = min(tail, key=lambda i: low[i], default=None)
    if cand_l is not None and low[cand_l] <= low[cand_l - w:cand_l].min() + EPS:
        pl.append(cand_l)

    def _thin(idx: list[int], values: np.ndarray, keep_max: bool) -> list[int]:
        """Collapse runs of near-adjacent pivots to the single extreme bar."""
        out: list[int] = []
        for i in sorted(set(idx)):
            if out and i - out[-1] <= w:
                better = values[i] > values[out[-1]] if keep_max else values[i] < values[out[-1]]
                if better:
                    out[-1] = i
                continue
            out.append(i)
        return out

    return _thin(ph, high, True), _thin(pl, low, False)


def multi_scale_pivots(high: np.ndarray, low: np.ndarray, w: int,
                       periods_per_year: int = 252) -> tuple[list[int], list[int]]:
    """結構 pivot（隨圖長放大的窗格）＋ 近期的擺動 pivot（固定小窗格）。

    只用一個隨 n 放大的窗格，長圖就看不見短擺動：AMZN 的 5 年日線 w=14，8/28 的
    267.56 與 9/21 的 259.49 兩個較低高點都不算 pivot，於是那條兩個月的下降壓力線
    連「候選」都生不出來 —— 不是分數輸掉，是根本沒有被考慮過。

    小窗格只補最近半年那一段。整條五年都用小窗格會塞進上百個 pivot，候選名額
    （只取最後幾個）反而會把長期結構線擠掉，那是另一種瞎。
    """
    ph, pl = find_pivots(high, low, w)
    if w <= SWING_W:
        return ph, pl
    ph_s, pl_s = find_pivots(high, low, SWING_W)
    n = len(high)
    cutoff = n - min(n, int(periods_per_year * 0.55))
    ph = sorted(set(ph) | {i for i in ph_s if i >= cutoff})
    pl = sorted(set(pl) | {i for i in pl_s if i >= cutoff})
    return ph, pl


def _alternating(ph: list[int], pl: list[int],
                 high: np.ndarray, low: np.ndarray) -> list[dict]:
    """Merge highs and lows into one strictly alternating H/L sequence.

    Pattern recognition only makes sense on an alternating zig-zag; two highs in
    a row means the lesser one was never a turning point at this scale.
    """
    merged = [{"i": i, "kind": "H", "price": float(high[i])} for i in ph] + \
             [{"i": i, "kind": "L", "price": float(low[i])} for i in pl]
    merged.sort(key=lambda p: p["i"])

    out: list[dict] = []
    for p in merged:
        if out and out[-1]["kind"] == p["kind"]:
            keep_new = (p["price"] > out[-1]["price"]) if p["kind"] == "H" \
                else (p["price"] < out[-1]["price"])
            if keep_new:
                out[-1] = p
            continue
        out.append(p)
    return out


# ── Trendlines ───────────────────────────────────────────────────────────────
def _fit_side(df: pd.DataFrame, pivot_idx: list[int], side: str,
              atr_arr: np.ndarray, max_lines: int = 3,
              periods_per_year: int = 252, refine: bool = True) -> list[dict]:
    """Every pivot pair is a candidate line; keep the few that price respected."""
    high = df["High"].astype(float).values
    low = df["Low"].astype(float).values
    close = df["Close"].astype(float).values
    n = len(close)
    current = float(close[-1])
    if len(pivot_idx) < 2 or current <= 0:
        return []

    price_arr = high if side == "resistance" else low
    sign = 1.0 if side == "resistance" else -1.0  # >0 excess = price beyond the line
    x = np.arange(n, dtype=float)

    # Pair count is quadratic, so only the recent pivots are candidates —
    # a line anchored on a pivot from five years ago is not one anyone trades.
    piv = pivot_idx[-26:]
    # 以前是 max(8, n//40)：5 年日線變成 31 根，等於規定「短於一個半月的腿不算趨勢線」。
    # 一段 15 根的下降腿在 1 年圖上可以畫，在 5 年圖上就不行 —— 但它是同一段行情。
    min_span = int(np.clip(n // 40, 8, 25))

    def _evaluate(a: int, b: int) -> dict | None:
        """一組錨點 → 候選線。過不了品質關卡回 None。"""
        span = b - a
        if span < min_span:
            return None
        pa, pb = price_arr[a], price_arr[b]
        slope = (pb - pa) / span
        line = pa + slope * (x - a)

        # Tolerance tracks volatility: a $400 stock with $8 ATR should not be
        # judged on the same absolute band as a $12 one.
        tol = np.maximum(atr_arr * 0.30, np.abs(line) * 0.004)

        v_now = float(line[-1])
        if not np.isfinite(v_now) or v_now <= 0:
            return None
        # Reject lines whose projection has run away from tradeable prices.
        if not (0.35 * current <= v_now <= 2.8 * current):
            return None

        # 1) Validity between the anchors — the defining property of a
        #    trendline is that price did NOT cross it inside the leg.
        seg = slice(a, b + 1)
        excess = sign * (price_arr[seg] - line[seg])
        breaches = int((excess > tol[seg]).sum())
        if breaches > max(1, int(span * 0.03)):
            return None

        # 2) Break scan after the second anchor, on closes (wicks poke
        #    through constantly; a close beyond is what invalidates a line).
        end = n - 1
        broken_at = None
        if b < n - 1:
            post = np.arange(b + 1, n)
            excess_post = sign * (close[post] - line[post])
            # One close through is noise on an illiquid day; two in a row
            # (or a single close by >1.5×tol) is a break. Scan every bar —
            # taking only the first poke-through misses the real break that
            # follows a month later, and the line stays wrongly "intact".
            beyond = excess_post > tol[post]
            two_in_row = beyond.copy()
            two_in_row[:-1] &= beyond[1:]
            two_in_row[-1] = False
            decisive = two_in_row | (excess_post > tol[post] * 1.5)
            hits = np.flatnonzero(decisive)
            if hits.size:
                broken_at = int(post[hits[0]])
                end = broken_at

        # 3) Touches — distinct approaches within tolerance, clustered so a
        #    week of hugging the line counts once.
        window = np.arange(a, end + 1)
        near = np.abs(price_arr[window] - line[window]) <= tol[window]
        touch_idx = window[near]
        touches: list[int] = []
        for t in touch_idx:
            if touches and t - touches[-1] < max(3, min_span // 3):
                continue
            touches.append(int(t))
        if len(touches) < 2:
            return None

        # 4) Bounce quality — the property that separates a real trendline
        #    from a line sitting inside a chop. Price is supposed to LEAVE
        #    after touching: reject a line price merely hugs. This is what
        #    catches an 18-touch "support" on a range-bound series, where
        #    the touch count measures confinement, not strength.
        marks = touches + [end]
        excursions = []
        for mi in range(len(marks) - 1):
            s0, e0 = marks[mi], marks[mi + 1]
            if e0 - s0 < 2:
                continue
            away = sign * (line[s0:e0 + 1] - close[s0:e0 + 1]) \
                / np.maximum(atr_arr[s0:e0 + 1], EPS)
            excursions.append(float(away.max()))
        bounce = float(np.median(excursions)) if excursions else 0.0
        if bounce < 1.2:
            return None

        # 5) 貼合度 —— 線浮在它所跨越的那段高點上方多遠（以 ATR 計）。
        #    兩點弦天生只保證碰到兩個端點：一段先急跌後轉平的行情，頭尾相連的弦
        #    會整條浮在中間那些高點上方。AMZN 8/3→9/21 就是這樣，中間三個高點分別
        #    在線下方 11.0、4.2、6.5 元。這個數字讓「貼著走」的線贏過「架在上面」的弦。
        gap = np.clip(sign * (line[seg] - price_arr[seg]), 0.0, None) \
            / np.maximum(atr_arr[seg], EPS)
        float_atr = float(gap.mean())

        # 這兩個權重以前都是「佔全圖的比例」，所以圖拉得越長，近期的線越吃虧：
        # 同一條 34 根的下降線，在 1 年圖上 span_score=0.30，在 5 年圖上只剩 0.06；
        # 而一條最後觸及在 220 根之前的舊線，在 5 年圖上 recency 還有 0.68。
        # 結果 5 年圖的三個名額全被 10~18% 之外的長期上升線佔滿。
        # 改成絕對尺度（用 periods_per_year 換算，週線月線一樣適用）：
        # 超過半年的跨度不再加分，最後錨點離現在越遠折得越兇。
        span_score = min(span / max(periods_per_year * 0.5, 20.0), 1.0)
        recency = math.exp(-(n - 1 - b) / max(periods_per_year * 0.35, 20.0))
        dist = abs(v_now - current) / current
        proximity = float(np.clip(1.0 - dist / 0.30, 0.12, 1.0))
        bounce_factor = float(np.clip(bounce / 3.5, 0.35, 1.4))
        score = (len(touches) ** 1.25) * (0.45 + 0.55 * span_score) \
            * (0.35 + 0.65 * recency) * proximity * bounce_factor
        if broken_at is not None:
            score *= 0.4
        score /= (1 + breaches * 0.5)

        return {
            "side": side,
            "a": a, "b": b,
            "slope": slope,
            "value_now": v_now,
            "touches_idx": touches,
            "touches": len(touches),
            "span": span,
            "broken_at": broken_at,
            "bounce": bounce,
            "float_atr": float_atr,
            "score": score,
        }

    def _refine(c: dict) -> dict:
        """把錨點往旁邊挪幾根，找真正貼合高點的位置。

        候選線只能錨在 pivot 上，而 pivot 是某個窗格下的局部極值 —— 一段急跌裡
        真正撐住線的那幾根往往不是 pivot。AMZN 那條弦（8/3→9/21）平均浮空 8.92 元；
        窮舉所有 K 棒配對找得到平均浮空 5.17、觸及 5 次的線，錨點 8/10 與 8/28
        兩根都不是 pivot。全域窮舉是 O(n²)（5 年圖 150 萬組）所以不可行，但在原錨點
        附近做小範圍搜尋就夠把弦拉回貼合的位置，而且成本固定。

        只在「觸及更多、或同樣觸及但更貼」時才換，所以退化的情況不會發生。
        """
        best = c
        r = int(np.clip(c["span"] // 4, 2, 6))

        def _try(a2: int, b2: int) -> None:
            nonlocal best
            if not (0 <= a2 < n and 0 <= b2 < n) or b2 <= a2:
                return
            cand = _evaluate(a2, b2)
            if cand is None:
                return
            # 換線不能把「還沒破」的線換成「已經破」的
            if cand["broken_at"] is not None and best["broken_at"] is None:
                return
            if (cand["touches"], -cand["float_atr"]) > \
               (best["touches"], -best["float_atr"]):
                best = cand

        # 座標下降而不是 (2r+1)² 的網格：兩個錨點輪流調，來回兩輪。目標函數在這裡
        # 夠平滑，找到的位置跟全網格幾乎一樣，但評估次數從 169 降到約 39 ——
        # 5 年圖的趨勢線計算從 90ms 回到 40ms 以內。
        for _ in range(2):
            a0, b0 = best["a"], best["b"]
            for da in range(-r, r + 1):
                _try(a0 + da, best["b"])
            for db in range(-r, r + 1):
                _try(best["a"], b0 + db)
        return best

    out: list[dict] = []
    for ai in range(len(piv) - 1):
        for bi in range(ai + 1, len(piv)):
            cand = _evaluate(piv[ai], piv[bi])
            if cand is not None:
                out.append(cand)

    # A resistance sitting 12% *below* price, or a support above it, is a line
    # price already left behind — informative only while the break is fresh.
    fresh_break = max(20, n // 20)
    keepable = []
    for c in out:
        wrong_side = (c["value_now"] < current * 0.98) if side == "resistance" \
            else (c["value_now"] > current * 1.02)
        if c["broken_at"] is not None:
            if (n - 1 - c["broken_at"]) > fresh_break:
                continue
        elif wrong_side:
            continue
        keepable.append(c)
    out = keepable
    if not out:
        return []

    out.sort(key=lambda c: -c["score"])

    # Hard distance cap. Proximity already discounts the score, but a line 90%
    # above price would still fill one of three slots on a quiet chart — and
    # nobody trades off it. Fall back to the nearest lines if that empties.
    near = [c for c in out if abs(c["value_now"] - current) / current <= 0.30]
    if not near:
        near = sorted(out, key=lambda c: abs(c["value_now"] - current))[:1]
    out = near

    # Dedupe: near-identical lines differ only in which pivot they anchored on.
    def _dup(c: dict, pool: list[dict]) -> bool:
        for k in pool:
            same_level = abs(c["value_now"] - k["value_now"]) / current < 0.025
            same_tilt = abs(c["slope"] - k["slope"]) / current < 0.0012
            if same_level and same_tilt:
                return True
        return False

    kept: list[dict] = []
    for c in out:
        if not _dup(c, kept):
            kept.append(c)
        if len(kept) >= max_lines:
            break

    # 保留一個名額給「價格當下就貼著」的那條。純照分數排，三個名額很容易被長期的
    # 強線佔滿，而現價正踩著的那條被擠掉 —— 實測十檔 × 四種週期共 40 張圖，有 16 張
    # 如此，留下的最遠一條在 9~17% 之外。長期結構有它的價值，但「現在站在哪條線上」
    # 是看圖的人第一個要問的問題，所以讓最近的那條頂掉分數最低的。
    # 門檻差 1 個百分點才換，避免兩條差不多近的線互相抖動。
    if len(out) > len(kept):
        nearest = min(out, key=lambda c: abs(c["value_now"] - current))
        d_near = abs(nearest["value_now"] - current) / current
        d_kept = min((abs(c["value_now"] - current) / current for c in kept), default=1e9)
        if d_near < d_kept - 0.01 and not _dup(nearest, kept):
            if len(kept) >= max_lines:
                kept[-1] = nearest          # out 是照分數排的，kept[-1] 就是最低分那條
            else:
                kept.append(nearest)

    # 選完才 refine：refine 是 O(r²) 的局部搜尋，對每個候選都做太貴（5 年圖上
    # 候選有數百個），而它改的是「線畫在哪」不是「哪條線重要」。
    if refine:
        kept = [_refine(c) for c in kept]
    kept.sort(key=lambda c: -c["score"])
    return kept


def _serialise_line(df: pd.DataFrame, c: dict, times: np.ndarray,
                    bar_seconds: int, proj_bars: int,
                    periods_per_year: int) -> dict:
    n = len(df)
    close = float(df["Close"].iloc[-1])
    a, b, slope = c["a"], c["b"], c["slope"]
    price_arr = df["High"].astype(float).values if c["side"] == "resistance" \
        else df["Low"].astype(float).values

    def at(i: float) -> float:
        return float(price_arr[a] + slope * (i - a))

    v_now = at(n - 1)
    v_proj = at(n - 1 + proj_bars)
    # Slope per bar means nothing across intervals; %/year is comparable.
    slope_pa = float(slope / close) * periods_per_year * 100 if close else None

    rising = slope > 0
    if c["side"] == "resistance":
        label = "上升壓力線" if rising else "下降壓力線"
    else:
        label = "上升支撐線" if rising else "下降支撐線"

    return {
        "side": c["side"],
        "label": label,
        "direction": "up" if rising else "down",
        "p1": {"time": int(times[a]), "price": round(float(price_arr[a]), 2)},
        "p2": {"time": int(times[b]), "price": round(float(price_arr[b]), 2)},
        "end": {"time": int(times[n - 1]), "price": round(v_now, 2)},
        "proj": {"time": int(times[n - 1] + bar_seconds * proj_bars), "price": round(v_proj, 2)},
        "touch_times": [int(times[t]) for t in c["touches_idx"]],
        "touches": c["touches"],
        "span_bars": int(c["span"]),
        "slope_per_year_pct": round(slope_pa, 1) if slope_pa is not None else None,
        "value_now": round(v_now, 2),
        "dist_pct": round((v_now - close) / close * 100, 2) if close else None,
        "status": "broken" if c["broken_at"] is not None else "intact",
        "broken_time": int(times[c["broken_at"]]) if c["broken_at"] is not None else None,
        "bounce_atr": round(float(c.get("bounce", 0.0)), 1),
        "strength": int(np.clip(round(math.log1p(max(c["score"], 0.0)) * 2.4), 1, 5)),
        "score": round(float(c["score"]), 2),
    }


# ── Linear-regression channel ────────────────────────────────────────────────
def regression_channel_frame(df: pd.DataFrame, lookback: int = 252,
                             periods_per_year: int = 252
                             ) -> tuple[pd.DataFrame, dict | None]:
    """Least-squares fit on log price with ±1σ / ±2σ residual bands.

    Log space matters: on a 5-year window a linear fit in dollars puts the
    channel walls a constant $ apart, so the early years look absurdly wide and
    the recent ones absurdly tight. In log space the band is a constant %.
    """
    frame = pd.DataFrame(index=df.index, columns=
                         ["reg_mid", "reg_u1", "reg_l1", "reg_u2", "reg_l2"], dtype=float)
    n = len(df)
    look = int(min(n, max(40, lookback)))
    if look < 30:
        return frame, None

    sub = df["Close"].astype(float).iloc[-look:]
    y = np.log(sub.values)
    if not np.all(np.isfinite(y)):
        return frame, None

    x = np.arange(look, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fit = intercept + slope * x
    resid = y - fit
    sd = float(resid.std(ddof=1))
    var_y = float(y.var())
    r2 = float(1 - (resid.var() / var_y)) if var_y > EPS else None

    idx = df.index[-look:]
    frame.loc[idx, "reg_mid"] = np.exp(fit)
    frame.loc[idx, "reg_u1"] = np.exp(fit + sd)
    frame.loc[idx, "reg_l1"] = np.exp(fit - sd)
    frame.loc[idx, "reg_u2"] = np.exp(fit + 2 * sd)
    frame.loc[idx, "reg_l2"] = np.exp(fit - 2 * sd)

    z = float(resid[-1] / sd) if sd > EPS else 0.0
    pos = float(np.clip((z + 2) / 4 * 100, 0, 100))
    stats = {
        "lookback": look,
        "r2": round(r2, 3) if r2 is not None else None,
        "slope_per_year_pct": round((math.exp(slope * periods_per_year) - 1) * 100, 1),
        "sigma_pct": round((math.exp(sd) - 1) * 100, 2),
        "z": round(z, 2),
        "position_pct": round(pos, 1),
        "mid": round(float(np.exp(fit[-1])), 2),
        "upper": round(float(np.exp(fit[-1] + 2 * sd)), 2),
        "lower": round(float(np.exp(fit[-1] - 2 * sd)), 2),
    }
    return frame, stats


# ── Regime: is this thing trending at all? ───────────────────────────────────
def variance_ratio(close: pd.Series | np.ndarray, q: int = 20) -> float | None:
    """Lo-MacKinlay variance ratio. <1 mean-reverting, ≈1 random walk, >1 momentum.

    Used only to LABEL a non-trending series (mean-reverting vs merely
    directionless) — not to score trendability. A violent trender like a
    high-beta semi prints VR≈0.5 from its reversals while still being in a
    textbook uptrend, so scoring on it would be wrong.
    """
    r = np.diff(np.log(np.asarray(close, dtype=float)))
    n = len(r)
    if n < q * 4 or q < 2:
        return None
    mu = r.mean()
    var1 = ((r - mu) ** 2).sum() / (n - 1)
    if var1 <= EPS:
        return None
    rq = np.convolve(r, np.ones(q), "valid")
    m = q * (n - q + 1) * (1 - q / n)
    varq = ((rq - q * mu) ** 2).sum() / max(m, EPS)
    return float(varq / var1)


def mean_reversion_halflife(close: pd.Series | np.ndarray) -> float | None:
    """OU half-life in bars: how long a deviation from the mean takes to decay."""
    y = np.log(np.asarray(close, dtype=float))
    if len(y) < 30:
        return None
    lag, dy = y[:-1], np.diff(y)
    beta = float(np.polyfit(lag, dy, 1)[0])
    if beta >= -EPS:
        return None
    hl = -math.log(2) / beta
    return round(hl, 1) if 0 < hl < 10_000 else None


def trend_regime(df: pd.DataFrame, reg_stats: dict | None,
                 lines: list[dict]) -> dict:
    """Verdict on whether trendlines mean anything on this chart at all.

    Three independent kinds of evidence, deliberately not including anything
    that a mean-reverting series can fake:

      • R² of the log-linear fit — does price follow a line over the window
      • the found lines' own bounce quality — do the lines actually get respected

    Three other candidates were measured and rejected as score inputs:
    ADX is an *inverse* signal here (^VIX medians 25 against NVDA's 17, because
    a vol index spikes directionally); Hurst and the variance ratio both file
    violent trenders under mean-reversion. ADX and VR are still reported —
    VR labels a non-trending series as mean-reverting vs merely directionless —
    but neither is allowed to vote.
    """
    close = df["Close"].astype(float)
    n = len(close)
    look = min(n, 252)
    sub = close.iloc[-look:]

    r2 = (reg_stats or {}).get("r2")
    try:
        adx_s, _, _ = adx_dmi(df["High"].astype(float), df["Low"].astype(float), close)
        adx_med = float(adx_s.iloc[-look:].median())
    except Exception:
        adx_med = None
    vr20 = variance_ratio(sub.values, 20)
    hl = mean_reversion_halflife(sub.values)

    bounces = [l["bounce_atr"] for l in lines if l.get("bounce_atr") is not None]
    bounce_med = float(np.median(bounces)) if bounces else 0.0

    s_r2 = float(np.clip((r2 or 0.0) / 0.60, 0, 1))
    s_line = float(np.clip(bounce_med / 6.0, 0, 1))
    score = round(100 * (0.60 * s_r2 + 0.40 * s_line))

    if score >= 60:
        label, trendable = "趨勢明確", True
    elif score >= 35:
        label, trendable = "趨勢偏弱", True
    elif vr20 is not None and vr20 < 0.55:
        label, trendable = "均值回歸", False
    else:
        label, trendable = "區間震盪", False

    bits = []
    if r2 is not None:
        bits.append(f"回歸 R²={r2}")
    if bounces:
        bits.append(f"趨勢線平均彈開 {bounce_med:.1f}×ATR")
    if vr20 is not None:
        bits.append(f"變異比 VR(20)={vr20:.2f}")

    if trendable:
        reason = "、".join(bits)
    elif label == "均值回歸":
        hl_txt = f"，偏離後約 {hl:.0f} 根 K 棒回到均值" if hl else ""
        reason = ("價格繞著均值來回而不是單向推進（" + "、".join(bits) + "）"
                  + hl_txt + "。趨勢線在這裡量到的是「被關在區間裡」，不是支撐壓力——"
                  "該看的是區間上下緣與偏離程度。")
    else:
        reason = ("這段期間沒有方向（" + "、".join(bits) + "）。"
                  "趨勢線可以畫，但價格沒有在遵守它們——請改用水平的支撐壓力與區間位置。")

    return {
        "label": label, "score": score, "trendable": trendable,
        "r2": r2, "adx_median": round(adx_med, 1) if adx_med is not None else None,
        "vr20": round(vr20, 2) if vr20 is not None else None,
        "halflife_bars": hl,
        "bounce_median_atr": round(bounce_med, 1) if bounces else None,
        "reason": reason,
    }


def range_stats(df: pd.DataFrame, lookback: int = 252) -> dict | None:
    """What to read instead of trendlines when the thing is range-bound."""
    n = len(df)
    look = min(n, max(60, lookback))
    sub = df.iloc[-look:]
    hi = float(sub["High"].astype(float).max())
    lo = float(sub["Low"].astype(float).min())
    close = float(df["Close"].iloc[-1])
    if hi <= lo:
        return None
    closes = sub["Close"].astype(float)
    return {
        "lookback": look,
        "high": round(hi, 2),
        "low": round(lo, 2),
        "median": round(float(closes.median()), 2),
        "position_pct": round((close - lo) / (hi - lo) * 100, 1),
        "percentile": round(float((closes <= close).mean() * 100), 1),
    }


# ── Chart patterns ───────────────────────────────────────────────────────────
def _pattern_from_lines(res: list[dict], sup: list[dict], close: float) -> dict | None:
    """Triangle / wedge / channel classification from the two dominant lines."""
    r = next((l for l in res if l["status"] == "intact"), None)
    s = next((l for l in sup if l["status"] == "intact"), None)
    if not r or not s or close <= 0:
        return None

    # Normalise slopes to %/year so "flat" means the same on any price scale.
    rs = r["slope_per_year_pct"]
    ss = s["slope_per_year_pct"]
    if rs is None or ss is None:
        return None
    flat = 12.0  # |%/yr| under this reads as horizontal to the eye
    gap_pct = (r["value_now"] - s["value_now"]) / close * 100
    # A "triangle" 30% tall is two unrelated lines, not a formation.
    if not (1.5 <= gap_pct <= 25.0):
        return None

    converging = (rs - ss) < -flat
    diverging = (rs - ss) > flat
    parallel = not converging and not diverging

    kind = label = None
    bias = "neutral"
    if converging:
        if abs(rs) <= flat:
            kind, label, bias = "ascending_triangle", "上升三角形", "bullish"
        elif abs(ss) <= flat:
            kind, label, bias = "descending_triangle", "下降三角形", "bearish"
        elif rs < 0 and ss > 0:
            kind, label = "symmetrical_triangle", "對稱三角形"
        elif rs < 0 and ss < 0:
            kind, label, bias = "falling_wedge", "下降楔形", "bullish"
        elif rs > 0 and ss > 0:
            kind, label, bias = "rising_wedge", "上升楔形", "bearish"
    elif parallel:
        if abs(rs) <= flat and abs(ss) <= flat:
            kind, label = "rectangle", "矩形箱型"
        elif rs > 0:
            kind, label, bias = "ascending_channel", "上升通道", "bullish"
        else:
            kind, label, bias = "descending_channel", "下降通道", "bearish"
    else:
        kind, label = "broadening", "擴散喇叭"

    if not kind:
        return None

    pos = (close - s["value_now"]) / max(r["value_now"] - s["value_now"], EPS) * 100
    return {
        "kind": kind,
        "label": label,
        "bias": bias,
        "upper": r["value_now"],
        "lower": s["value_now"],
        "width_pct": round(gap_pct, 2),
        "position_pct": round(float(np.clip(pos, -20, 120)), 1),
        "upper_slope_pa": rs,
        "lower_slope_pa": ss,
        "target": round(close + (r["value_now"] - s["value_now"]), 2) if bias != "bearish"
        else round(close - (r["value_now"] - s["value_now"]), 2),
    }


def _reversal_patterns(piv: list[dict], times: np.ndarray, close: float,
                       n: int, atr: float) -> list[dict]:
    """Double top/bottom and head & shoulders off the alternating pivot chain."""
    out: list[dict] = []
    if len(piv) < 4 or close <= 0:
        return out

    tol = max(0.035, (atr / close) * 1.2 if close else 0.035)   # peak-equality band
    recent = piv[-7:]

    # A pattern whose right shoulder printed a year ago is chart history, not a
    # setup. Only formations still in play near the right edge count — and a
    # forming one whose neckline is 30% away has no trigger worth watching.
    max_age = max(15, n // 8)

    def add(kind, label, bias, pts, neck, target, status):
        if (n - 1 - pts[-1]["i"]) > max_age:
            return
        if status != "confirmed" and abs(neck - close) / close > 0.25:
            return
        out.append({
            "kind": kind, "label": label, "bias": bias,
            "points": [{"time": int(times[p["i"]]), "price": round(p["price"], 2)} for p in pts],
            "neckline": round(neck, 2),
            "target": round(target, 2),
            "status": status,
            "neck_dist_pct": round((neck - close) / close * 100, 2),
        })

    # ── Double top / bottom: X ─ Y ─ X' with the two X within tolerance ───────
    for k in range(len(recent) - 2):
        p1, mid, p2 = recent[k], recent[k + 1], recent[k + 2]
        if p1["kind"] != p2["kind"] or p1["kind"] == mid["kind"]:
            continue
        if p2["i"] - p1["i"] < 12:
            continue
        if abs(p1["price"] - p2["price"]) / max(p1["price"], EPS) > tol:
            continue
        depth = abs(mid["price"] - (p1["price"] + p2["price"]) / 2) / max(close, EPS)
        if depth < 0.04:                       # too shallow to be a real M/W
            continue
        neck = mid["price"]
        height = abs((p1["price"] + p2["price"]) / 2 - neck)
        if p1["kind"] == "H":
            status = "confirmed" if close < neck else "forming"
            add("double_top", "雙重頂 M 頭", "bearish", [p1, mid, p2], neck, neck - height, status)
        else:
            status = "confirmed" if close > neck else "forming"
            add("double_bottom", "雙重底 W 底", "bullish", [p1, mid, p2], neck, neck + height, status)

    # ── Head & shoulders: five alternating pivots, middle extreme dominant ────
    for k in range(len(recent) - 4):
        seq = recent[k:k + 5]
        kinds = [p["kind"] for p in seq]
        if kinds not in (["H", "L", "H", "L", "H"], ["L", "H", "L", "H", "L"]):
            continue
        ls, t1, head, t2, rs_ = seq
        shoulder_gap = abs(ls["price"] - rs_["price"]) / max(ls["price"], EPS)
        if shoulder_gap > tol * 1.6:
            continue
        neck = (t1["price"] + t2["price"]) / 2
        if kinds[0] == "H":
            if not (head["price"] > ls["price"] * 1.02 and head["price"] > rs_["price"] * 1.02):
                continue
            height = head["price"] - neck
            status = "confirmed" if close < neck else "forming"
            add("head_shoulders", "頭肩頂", "bearish", seq, neck, neck - height, status)
        else:
            if not (head["price"] < ls["price"] * 0.98 and head["price"] < rs_["price"] * 0.98):
                continue
            height = neck - head["price"]
            status = "confirmed" if close > neck else "forming"
            add("inv_head_shoulders", "頭肩底", "bullish", seq, neck, neck + height, status)

    # Newest first, confirmed ahead of forming.
    out.sort(key=lambda p: (p["status"] != "confirmed", -max(pt["time"] for pt in p["points"])))

    # Overlapping windows of the pivot chain describe the same formation twice.
    deduped: list[dict] = []
    for p in out:
        if any(q["kind"] == p["kind"]
               and abs(q["neckline"] - p["neckline"]) / max(close, EPS) < 0.03
               for q in deduped):
            continue
        deduped.append(p)
    return deduped[:2]


def _summary(close: float, res: list[dict], sup: list[dict],
             channel: dict | None, patterns: list[dict],
             reg: dict | None, regime: dict | None = None,
             rng: dict | None = None) -> str | None:
    """One line a human can act on, in the order a trader reads a chart.

    When the regime says there is no trend, that verdict IS the summary — it
    would be worse than useless to lead with "最近趨勢支撐 $x" on a series that
    does not respect trendlines.
    """
    if regime and not regime.get("trendable"):
        head = f"【{regime['label']}・趨勢分數 {regime['score']}/100】{regime['reason']}"
        if rng:
            head += (f" 近 {rng['lookback']} 根區間 ${rng['low']}–${rng['high']}"
                     f"（中位 ${rng['median']}），現價在區間 {rng['position_pct']}%、"
                     f"歷史百分位 {rng['percentile']}。")
        return head

    parts: list[str] = []
    if regime:
        parts.append(f"{regime['label']}（趨勢分數 {regime['score']}/100）")

    if reg and reg.get("r2") is not None:
        fit = "貼合" if reg["r2"] >= 0.75 else ("鬆散" if reg["r2"] < 0.4 else "普通")
        pa = reg.get("slope_per_year_pct")
        tilt = "走平" if pa is None or abs(pa) < 8 else ("上傾" if pa > 0 else "下傾")
        parts.append(f"回歸通道{tilt}"
                     + (f" {pa:+.0f}%/年" if pa is not None else "")
                     + f"，R²={reg['r2']}（{fit}），現價在通道 {reg['position_pct']}% 位置")

    if channel:
        parts.append(f"{channel['label']}，區間寬度 {channel['width_pct']}%，"
                     f"現價在通道 {channel['position_pct']}% 處")

    nearest_sup = min((l for l in sup if l["status"] == "intact" and l["value_now"] <= close * 1.01),
                      key=lambda l: close - l["value_now"], default=None)
    if nearest_sup:
        parts.append(f"最近趨勢支撐 ${nearest_sup['value_now']}"
                     f"（{abs(nearest_sup['dist_pct']):.1f}% 下方，{nearest_sup['touches']} 次觸及）")
    nearest_res = min((l for l in res if l["status"] == "intact" and l["value_now"] >= close * 0.99),
                      key=lambda l: l["value_now"] - close, default=None)
    if nearest_res:
        parts.append(f"最近趨勢壓力 ${nearest_res['value_now']}"
                     f"（{abs(nearest_res['dist_pct']):.1f}% 上方，{nearest_res['touches']} 次觸及）")

    for p in patterns[:1]:
        parts.append(f"{p['label']}{'已確認' if p['status'] == 'confirmed' else '成形中'}，"
                     f"頸線 ${p['neckline']}、量測目標 ${p['target']}")

    return "；".join(parts) if parts else None


# ── Entry point ──────────────────────────────────────────────────────────────
def compute_trend_structure(df: pd.DataFrame, periods_per_year: int = 252,
                            reg_stats: dict | None = None) -> dict:
    n = len(df)
    empty = {"trendlines": {"resistance": [], "support": []}, "channel": None,
             "patterns": [], "regime": None, "range": None, "summary": None,
             "proj_bars": 0, "bar_seconds": 86400}
    if n < 60:
        return empty

    high = df["High"].astype(float).values
    low = df["Low"].astype(float).values
    close_arr = df["Close"].astype(float).values
    close = float(close_arr[-1])
    times = (df.index.astype("int64") // 10 ** 9).values
    diffs = np.diff(times)
    bar_seconds = int(np.median(diffs)) if diffs.size else 86400
    proj_bars = int(np.clip(n // 25, 5, 30))

    prev = np.roll(close_arr, 1)
    prev[0] = close_arr[0]
    tr = np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])
    atr_arr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().bfill().values
    atr_now = float(atr_arr[-1])

    # Pivot scale follows chart length — a 5y daily chart and a 3m one should
    # not surface swings of the same bar count.
    w = int(np.clip(n // 45, 4, 14))
    ph, pl = find_pivots(high, low, w)
    # 趨勢線另外吃一份多尺度的 pivot（結構窗格＋近期小窗格），長圖才看得見短腿。
    # 形態辨識「不」用這一份：頭肩、雙頂這類形態是對某個尺度的主張，把 5 根的擺動
    # 跟 14 根的結構混在一條 zig-zag 上認形態，認出來的東西不對應任何一個尺度。
    ph_tl, pl_tl = multi_scale_pivots(high, low, w, periods_per_year)

    res = [_serialise_line(df, c, times, bar_seconds, proj_bars, periods_per_year)
           for c in _fit_side(df, ph_tl, "resistance", atr_arr,
                              periods_per_year=periods_per_year)]
    sup = [_serialise_line(df, c, times, bar_seconds, proj_bars, periods_per_year)
           for c in _fit_side(df, pl_tl, "support", atr_arr,
                              periods_per_year=periods_per_year)]

    channel = _pattern_from_lines(res, sup, close)
    piv_chain = _alternating(ph, pl, high, low)
    patterns = _reversal_patterns(piv_chain, times, close, n, atr_now)

    # regime 讀的是「線被遵守得多好」（bounce，以 ATR 計），而 bounce 跟線的長度
    # 高度相關 —— 一條橫跨 700 根的線本來就比 34 根的彈得遠。所以它必須用固定的
    # 證據基礎來算，不能跟著顯示用的選線政策跑：拿多尺度那份去餵，NVDA 的五年線
    # 會因為選到較短較近的線而被判成「不趨勢」（分數 40→26），形態層跟著整個消失。
    # （_fit_side 回的是原始候選，欄位叫 bounce；trend_regime 讀的是序列化後的
    #   bounce_atr，所以這裡要對上名字。）
    regime_lines = [
        {"bounce_atr": round(float(c.get("bounce", 0.0)), 1)}
        for c in (_fit_side(df, ph, "resistance", atr_arr,
                            periods_per_year=periods_per_year, refine=False)
                  + _fit_side(df, pl, "support", atr_arr,
                              periods_per_year=periods_per_year, refine=False))
    ]
    regime = trend_regime(df, reg_stats, regime_lines)
    rng = range_stats(df, periods_per_year)

    # A formation is a claim about trend continuation or reversal. On a series
    # that does not trend, an "M top" is two spikes that happened to be similar
    # heights — the pattern layer is suppressed rather than shipped with a
    # caveat nobody reads.
    if not regime["trendable"]:
        channel = None
        patterns = []

    return {
        "trendlines": {"resistance": res, "support": sup},
        "channel": channel,
        "patterns": patterns,
        "regime": regime,
        "range": rng,
        "summary": _summary(close, res, sup, channel, patterns, reg_stats, regime, rng),
        "proj_bars": proj_bars,
        "bar_seconds": bar_seconds,
    }
