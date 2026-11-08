"""強弱榜 — 產業與個股的相對強弱（RS），當天與一段區間一起看。

為什麼要有這支：機會掃描原本只看「這一檔自己怎麼樣」（超賣、低估、突破），
完全不知道它站在哪個產業裡、相對大盤是強是弱。結果就是報告裡一再出現
「低估訊號被基本面打臉的價值陷阱」—— 那些多半是弱勢產業裡的弱勢股。

資料來源刻意選了零成本的路：Stage-1 預篩本來就會對整個 universe 抓一年日線
（`_fetch_technicals_batch`），這裡吃它算好的 `returns`，不再打一次 yfinance。
SPDR 板塊那張榜則直接用 sectors router 現成的 RS 引擎（自己有 30 分鐘快取）。

三張榜：
- stocks： 每檔 vs SPY 的超額報酬（1D/1W/1M/3M）與百分位，取強勢／弱勢各 N 檔
- themes： 自訂 19 個主題桶（半導體、記憶體、AI 半導體、機器人…）桶內等權
- sectors：SPDR 11 大板塊，看公認的板塊輪動
"""

from __future__ import annotations

from src.config import STOCK_LIST

# 榜單期間。1d 是「今天誰在動」，1m/3m 是「這段區間誰真的在走」——
# 只看當天會把一次跳空當成趨勢，只看三個月會錯過剛翻多的。
PERIODS = ("1d", "1w", "1m", "3m")
BENCHMARK = "SPY"

# 排行榜長度。太長就不是榜了，看的人得自己再篩一次。
TOP_N = 12

# 主題桶裡這些不是個股，排名沒有意義
_NON_STOCK_BUCKETS = {"ETF"}


def _pct_rank(values: list[float | None]) -> list[float | None]:
    """百分位（0-100，越大越強）。None 不參與排名，回傳也是 None。"""
    idx = [i for i, v in enumerate(values) if v is not None]
    out: list[float | None] = [None] * len(values)
    if not idx:
        return out
    ordered = sorted(idx, key=lambda i: values[i] or 0.0)
    n = len(ordered)
    for rank, i in enumerate(ordered):
        out[i] = round(rank / (n - 1) * 100, 1) if n > 1 else 50.0
    return out


def _theme_of() -> dict[str, str]:
    """ticker → 主題桶。一檔落在多個桶時取第一個，桶本來就是互斥設計的。

    手寫桶優先；發現層（全市場自動撈進來的）沒有手寫桶，退回它的 GICS sector 桶
    （名字帶 ＊ 前綴，見 discovery_universe.SECTOR_TO_THEME）。沒有這段的話發現層
    個股的 theme 全是 None，排不進主題強弱榜，rs_leader 與價值陷阱降權也會少掉
    「它站在哪個產業裡」這半邊資訊。
    """
    out: dict[str, str] = {}
    for bucket, tickers in STOCK_LIST.items():
        if bucket in _NON_STOCK_BUCKETS:
            continue
        for t in tickers:
            out.setdefault(t.upper(), bucket)
    try:
        from src.analysis import discovery_universe
        for t, meta in discovery_universe.load().items():
            out.setdefault(t.upper(), meta.get("theme") or discovery_universe.FALLBACK_THEME)
    except Exception:
        pass
    return out


def _curated_themes() -> set[str]:
    return {b for b in STOCK_LIST if b not in _NON_STOCK_BUCKETS}


def _mean(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def build_stock_strength(tech_map: dict[str, dict]) -> dict:
    """個股強弱：相對 SPY 的超額報酬 + 百分位。tech_map 來自 Stage-1 的批次抓取。"""
    bench = (tech_map.get(BENCHMARK) or {}).get("returns") or {}
    theme_of = _theme_of()

    rows: list[dict] = []
    for ticker, tech in tech_map.items():
        if ticker == BENCHMARK:
            continue
        ret = (tech or {}).get("returns") or {}
        if all(ret.get(p) is None for p in PERIODS):
            continue
        rs = {
            p: (round(ret[p] - bench[p], 2)
                if ret.get(p) is not None and bench.get(p) is not None else None)
            for p in PERIODS
        }
        rows.append({
            "ticker": ticker,
            "theme": theme_of.get(ticker),
            "price": tech.get("price"),
            "returns": {p: ret.get(p) for p in PERIODS},
            "rs": rs,
            "rsi": tech.get("rsi_14"),
            "price_vs_ma20_pct": tech.get("price_vs_ma20_pct"),
        })

    # 百分位以 1m RS 為準：一天太吵，三個月又轉得太慢，一個月是「這段區間」的骨幹
    ranks = _pct_rank([r["rs"].get("1m") for r in rows])
    for r, pr in zip(rows, ranks):
        r["rs_rank"] = pr

    # 沒有 1M 資料的（新上市、剛分割）不該混進榜裡冒充弱勢
    ranked = sorted([r for r in rows if r["rs_rank"] is not None],
                    key=lambda r: r["rs_rank"], reverse=True)
    return {
        "benchmark": BENCHMARK,
        "benchmark_returns": {p: bench.get(p) for p in PERIODS},
        "evaluated": len(rows),
        "leaders": ranked[:TOP_N],
        "laggards": ranked[-TOP_N:][::-1],
        "by_ticker": {r["ticker"]: {"rs": r["rs"], "rs_rank": r["rs_rank"], "theme": r["theme"]} for r in rows},
    }


def build_theme_strength(tech_map: dict[str, dict]) -> list[dict]:
    """自訂主題桶：桶內等權平均報酬，各期間都算，按 1m 排序。"""
    theme_of = _theme_of()
    curated = _curated_themes()
    buckets: dict[str, list[dict]] = {}
    for ticker, tech in tech_map.items():
        theme = theme_of.get(ticker)
        if not theme:
            continue
        ret = (tech or {}).get("returns") or {}
        if all(ret.get(p) is None for p in PERIODS):
            continue
        buckets.setdefault(theme, []).append({"ticker": ticker, "returns": ret})

    bench = (tech_map.get(BENCHMARK) or {}).get("returns") or {}
    out: list[dict] = []
    for theme, members in buckets.items():
        avg = {p: _mean([m["returns"][p] for m in members if m["returns"].get(p) is not None]) for p in PERIODS}
        rs = {
            p: (round(avg[p] - bench[p], 2) if avg.get(p) is not None and bench.get(p) is not None else None)
            for p in PERIODS
        }
        # 桶內誰在領漲／領跌 —— 「半導體很強」之後緊接著的問題就是「強在誰」
        by_1m = sorted(
            [m for m in members if m["returns"].get("1m") is not None],
            key=lambda m: m["returns"]["1m"], reverse=True,
        )
        out.append({
            "theme": theme,
            "tier": "curated" if theme in curated else "discovery",
            "count": len(members),
            "returns": avg,
            "rs": rs,
            "top": [m["ticker"] for m in by_1m[:3]],
            "bottom": [m["ticker"] for m in by_1m[-3:][::-1]],
        })

    out.sort(key=lambda x: (x["rs"].get("1m") if x["rs"].get("1m") is not None else -999), reverse=True)
    ranks = _pct_rank([x["rs"].get("1m") for x in out])
    for x, pr in zip(out, ranks):
        x["rs_rank"] = pr
    return out


def build_sector_strength() -> list[dict]:
    """SPDR 11 大板塊。用 sectors router 現成的 RS 引擎（它自己有 30 分鐘快取）。"""
    try:
        from backend.app.routers.sectors import get_sector_rs
        payload = get_sector_rs()
    except Exception:
        return []
    out = []
    for it in payload.get("items", []):
        # sectors router 抓不到資料時（yfinance 限流）會回一排 ok=False 的空殼，
        # 而且那個結果會被它自己快取 30 分鐘 —— 空殼不要放進榜裡冒充板塊。
        if it.get("ok") is False or not (it.get("returns") or {}):
            continue
        # sectors router 的 PERIODS 從 1w 起跳，沒有 1d —— 當天用它另外帶的 change_pct
        rets = dict(it.get("returns") or {})
        rets.setdefault("1d", it.get("change_pct"))
        out.append({
            "symbol": it.get("symbol"),
            "name": it.get("name") or it.get("label"),
            "returns": {p: rets.get(p) for p in PERIODS},
            "rs": {p: (it.get("rs") or {}).get(p) for p in PERIODS},
            "rs_rating": it.get("rs_rating"),
            "rs_trend": it.get("rs_trend"),
        })
    return out


def build_strength_board(tech_map: dict[str, dict], with_sectors: bool = True) -> dict:
    """三張榜一起產。tech_map 是 Stage-1 抓好的技術面（含 returns）。"""
    stocks = build_stock_strength(tech_map)
    return {
        "periods": list(PERIODS),
        "benchmark": BENCHMARK,
        "benchmark_returns": stocks["benchmark_returns"],
        "stocks": stocks,
        "themes": build_theme_strength(tech_map),
        "sectors": build_sector_strength() if with_sectors else [],
    }
