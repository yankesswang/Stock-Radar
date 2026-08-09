"""
Sector rotation / Relative Strength (RS) via yfinance.

Tracks the 11 SPDR sector ETFs (XLK, XLV, XLY, ...) against the SPY
benchmark so you can see which sectors are leading and which are lagging.

GET /api/sectors/rs        - all sectors with returns + RS vs SPY (cached 30 min)
GET /api/sectors/rs-line/{symbol} - 6-month RS line (ETF/SPY, normalized to 100)
DELETE /api/sectors/cache  - clear cache
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf
from fastapi import APIRouter

router = APIRouter()

BENCHMARK = "SPY"

# The 11 SPDR Select Sector ETFs. `group` = cyclical / defensive / sensitive
# (classic S&P sector-rotation grouping) for optional UI grouping.
SECTORS = [
    {"symbol": "XLK",  "label": "Technology",             "zh": "科技",       "group": "sensitive"},
    {"symbol": "XLC",  "label": "Communication Services", "zh": "通訊服務",   "group": "sensitive"},
    {"symbol": "XLY",  "label": "Consumer Discretionary", "zh": "非必需消費", "group": "cyclical"},
    {"symbol": "XLF",  "label": "Financials",             "zh": "金融",       "group": "cyclical"},
    {"symbol": "XLI",  "label": "Industrials",            "zh": "工業",       "group": "cyclical"},
    {"symbol": "XLB",  "label": "Materials",              "zh": "原物料",     "group": "cyclical"},
    {"symbol": "XLRE", "label": "Real Estate",            "zh": "房地產",     "group": "cyclical"},
    {"symbol": "XLE",  "label": "Energy",                 "zh": "能源",       "group": "sensitive"},
    {"symbol": "XLV",  "label": "Health Care",            "zh": "醫療保健",   "group": "defensive"},
    {"symbol": "XLP",  "label": "Consumer Staples",       "zh": "必需消費",   "group": "defensive"},
    {"symbol": "XLU",  "label": "Utilities",              "zh": "公用事業",   "group": "defensive"},
]

# lookback windows in trading days
PERIODS = {
    "1w": 5,
    "1m": 21,
    "3m": 63,
    "6m": 126,
}

_CACHE: dict = {"data": None, "ts": 0}
CACHE_TTL = 30 * 60  # 30 minutes


def _pct_change(close: pd.Series, days: int):
    """Percent return over the last `days` trading days."""
    if close is None or len(close) <= days:
        return None
    past = float(close.iloc[-1 - days])
    now = float(close.iloc[-1])
    if not past:
        return None
    return (now - past) / past * 100


def _fetch_close(symbol: str) -> pd.Series | None:
    try:
        hist = yf.Ticker(symbol).history(period="1y", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        close = hist["Close"].dropna()
        return close if not close.empty else None
    except Exception:
        return None


def _rs_line(etf: pd.Series, bench: pd.Series, days: int = 126) -> list[dict]:
    """
    Relative-strength line: ETF / benchmark ratio, rebased to 100 at the start
    of the window. A rising line means the sector is outperforming SPY.
    """
    df = pd.concat([etf.rename("e"), bench.rename("b")], axis=1).dropna()
    if df.empty:
        return []
    df = df.iloc[-days:]
    ratio = df["e"] / df["b"]
    base = float(ratio.iloc[0])
    if not base:
        return []
    out = []
    for idx, val in ratio.items():
        out.append({"date": str(idx)[:10], "rs": round(float(val) / base * 100, 3)})
    return out


def _rank(values: list[float | None]) -> list[int | None]:
    """Percentile rank (1-99) of each non-null value within the list."""
    pairs = [(i, v) for i, v in enumerate(values) if v is not None]
    if not pairs:
        return [None] * len(values)
    order = sorted(pairs, key=lambda p: p[1])
    n = len(order)
    ranks: dict[int, int] = {}
    for rank_pos, (i, _) in enumerate(order):
        pct = 1 if n == 1 else round(1 + rank_pos / (n - 1) * 98)
        ranks[i] = pct
    return [ranks.get(i) for i in range(len(values))]


def _build() -> dict:
    symbols = [s["symbol"] for s in SECTORS] + [BENCHMARK]
    closes: dict[str, pd.Series | None] = {}
    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        futures = {pool.submit(_fetch_close, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            closes[sym] = fut.result()

    bench = closes.get(BENCHMARK)
    bench_ret = {p: _pct_change(bench, d) for p, d in PERIODS.items()} if bench is not None else {}

    items = []
    for meta in SECTORS:
        close = closes.get(meta["symbol"])
        row = {**meta, "ok": close is not None}
        if close is None:
            row.update({"price": None, "returns": {}, "rs": {}})
            items.append(row)
            continue

        price = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) >= 2 else None
        change_pct = (price - prev) / prev * 100 if prev else None

        returns = {p: _pct_change(close, d) for p, d in PERIODS.items()}
        # RS = sector excess return over SPY (percentage points) per window
        rs = {
            p: round(returns[p] - bench_ret[p], 2)
            if (returns.get(p) is not None and bench_ret.get(p) is not None)
            else None
            for p in PERIODS
        }
        # 6-month RS line trend + is-it-currently-rising flag
        rs_line = _rs_line(close, bench) if bench is not None else []
        rs_trend = None
        if len(rs_line) >= 6:
            rs_trend = round(rs_line[-1]["rs"] - rs_line[-6]["rs"], 2)  # ~1w slope

        row.update({
            "price": round(price, 2),
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "last_date": str(close.index[-1])[:10],
            "returns": {k: (round(v, 2) if v is not None else None) for k, v in returns.items()},
            "rs": rs,
            "rs_line_spark": [d["rs"] for d in rs_line[-40:]],
            "rs_trend": rs_trend,
        })
        items.append(row)

    # RS rating: percentile rank of the 3-month RS across all sectors
    ratings = _rank([it["rs"].get("3m") if it.get("rs") else None for it in items])
    for it, r in zip(items, ratings):
        it["rs_rating"] = r

    # sort strongest → weakest by RS rating (fallback 3m RS)
    items.sort(
        key=lambda x: (x.get("rs_rating") if x.get("rs_rating") is not None else -1),
        reverse=True,
    )

    return {
        "benchmark": BENCHMARK,
        "benchmark_returns": {k: (round(v, 2) if v is not None else None) for k, v in bench_ret.items()},
        "periods": list(PERIODS.keys()),
        "items": items,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.get("/rs")
def get_sector_rs():
    """All sector ETFs ranked by relative strength vs SPY (cached 30 min)."""
    global _CACHE
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < CACHE_TTL:
        return _CACHE["data"]
    payload = _build()
    _CACHE = {"data": payload, "ts": now}
    return payload


# indices you can rebase the RS line against
BENCHMARK_CHOICES = [
    {"symbol": "SPY", "label": "S&P 500"},
    {"symbol": "QQQ", "label": "Nasdaq 100"},
    {"symbol": "DIA", "label": "Dow 30"},
    {"symbol": "IWM", "label": "Russell 2000"},
    {"symbol": "RSP", "label": "S&P 500 等權"},
    {"symbol": "ACWI", "label": "全球股市"},
]


@router.get("/benchmarks")
def list_benchmarks():
    """Indices the RS line can be computed against."""
    return {"benchmarks": BENCHMARK_CHOICES, "default": BENCHMARK}


@router.get("/rs-line/{symbol}")
def get_rs_line(symbol: str, benchmark: str = BENCHMARK):
    """6-month RS line (ETF / chosen index, rebased to 100) for a single ETF."""
    bench_sym = (benchmark or BENCHMARK).upper()
    etf = _fetch_close(symbol.upper())
    bench = _fetch_close(bench_sym)
    line = _rs_line(etf, bench) if (etf is not None and bench is not None) else []
    return {
        "symbol": symbol.upper(),
        "benchmark": bench_sym,
        "rs_line": line,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.delete("/cache")
def clear_sector_cache():
    global _CACHE
    _CACHE = {"data": None, "ts": 0}
    return {"cleared": True}
