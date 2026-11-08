"""
Sector rotation / Relative Strength (RS) via yfinance.

Tracks the 11 SPDR sector ETFs (XLK, XLV, XLY, ...) against the SPY
benchmark so you can see which sectors are leading and which are lagging.

GET /api/sectors/rs        - all sectors with returns + RS vs SPY (cached 30 min)
GET /api/sectors/rs-line/{symbol} - 6-month RS line (ETF/SPY, normalized to 100)
GET /api/sectors/exposure  - my portfolio/watchlist tickers grouped by SPDR sector
GET /api/sectors/{symbol}/holdings - top constituents of one sector ETF, quoted,
                             flagged with whether I hold or watch them
DELETE /api/sectors/cache  - clear cache
"""
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf
from fastapi import APIRouter, HTTPException

from ..config import settings

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
            # raw price closes (~3 months) so the UI can draw a price trend line
            # over whichever window the user picks
            "price_spark": [round(float(v), 4) for v in close.iloc[-63:]],
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


# ── Constituents: which single stocks are actually moving a sector ───────────
#
# The ETF row answers "is 科技 up today"; the expanded row answers "because of
# whom". Top holdings come from yfinance's fund data, quotes from one batched
# download, and each name is flagged if it shows up in portfolio.csv or a
# watchlist so my own exposure is visible in the same list.

# Yahoo's own sector taxonomy → the SPDR ETF that tracks it. Used to place my
# holdings into a sector even when they are not a top-10 constituent.
YF_SECTOR_TO_ETF = {
    "Technology":             "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical":      "XLY",
    "Financial Services":     "XLF",
    "Industrials":            "XLI",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Energy":                 "XLE",
    "Healthcare":             "XLV",
    "Consumer Defensive":     "XLP",
    "Utilities":              "XLU",
}

HOLDINGS_TTL = 30 * 60
_HOLDINGS_CACHE: dict[str, dict] = {}   # symbol -> {"data": ..., "ts": ...}
_OWNED_CACHE: dict = {"data": None, "ts": 0}
OWNED_TTL = 5 * 60

# ticker -> {sector, name}, persisted: neither moves in practice, and re-deriving
# them costs one .info call per position on every cold start.
_SECTOR_MAP_PATH: Path = settings.DATA_DIR / "ticker_sectors.json"
# last constituent list seen per ETF, so a throttled fund call still renders
_HOLDINGS_STORE_PATH: Path = settings.DATA_DIR / "etf_holdings.json"

# Yahoo throttles per-ticker profile lookups; resolve at most this many unknown
# tickers per request, and after a fully-failed batch stop asking for a while
# instead of retrying on every request.
PROFILE_BATCH = 8
PROFILE_BACKOFF = 20 * 60
_PROFILE_BACKOFF_UNTIL = 0.0

_SKIP_TICKERS = {"CASH", "ETORO"}

# last good USD/TWD rate — an FX quote that fails should not silently rescale
# every Taiwan position's weight
_LAST_USD_TWD: float | None = None


def _load_sector_map() -> dict[str, dict]:
    try:
        raw = json.loads(_SECTOR_MAP_PATH.read_text())
    except Exception:
        return {}
    # earlier revisions stored a bare sector string per ticker
    return {
        k: (v if isinstance(v, dict) else {"sector": v, "name": None})
        for k, v in raw.items()
    }


def _save_sector_map(mapping: dict[str, dict]) -> None:
    try:
        _SECTOR_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SECTOR_MAP_PATH.write_text(
            json.dumps(mapping, indent=2, sort_keys=True, ensure_ascii=False)
        )
    except Exception:
        pass


def _yf_profile(ticker: str) -> dict | None:
    """
    Sector + display name for one ticker, or None when the lookup failed.

    Uses Yahoo's search endpoint rather than Ticker.info: it carries the same
    two fields, and .info is throttled aggressively enough that resolving a
    whole portfolio through it starves the fund-holdings calls beside it.
    """
    quote = None
    try:
        url = (
            "https://query2.finance.yahoo.com/v1/finance/search"
            f"?q={urllib.parse.quote(ticker)}&quotesCount=5&newsCount=0"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            quotes = json.load(resp).get("quotes") or []
        quote = next(
            (q for q in quotes if str(q.get("symbol", "")).upper() == ticker.upper()),
            None,
        )
    except Exception:
        quote = None

    if quote is None:
        return None
    name = quote.get("shortname") or quote.get("longname")
    sector = quote.get("sector")
    if not name and not sector:
        return None
    # sector is legitimately absent for ETFs and funds — cache that verdict so
    # they are not re-asked on every request; the name doubles as "resolved"
    return {"sector": sector, "name": name or ticker}


def _resolve_sectors(tickers: list[str]) -> dict[str, dict]:
    """Sector + display name per ticker, disk-cached; only unknowns hit the network."""
    global _PROFILE_BACKOFF_UNTIL
    mapping = _load_sector_map()
    missing = [t for t in tickers if not (mapping.get(t) or {}).get("name")]
    if missing and time.time() < _PROFILE_BACKOFF_UNTIL:
        # Yahoo is throttling .info — serve what is already known rather than
        # re-running a batch that will only fail again
        return mapping
    if missing:
        # Resolve a slice at a time: a cold cache should fill in over a handful
        # of page loads rather than firing one burst Yahoo will throttle — and a
        # throttled burst also poisons the fund-holdings calls beside it.
        missing = missing[:PROFILE_BATCH]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_yf_profile, t): t for t in missing}
            for fut in as_completed(futures):
                ticker = futures[fut]
                profile = fut.result()
                previous = mapping.get(ticker) or {}
                if profile:
                    mapping[ticker] = profile
                elif previous.get("sector"):
                    # sector already known — settle for the ticker as its label
                    # rather than re-asking Yahoo on every single request
                    mapping[ticker] = {"sector": previous["sector"], "name": ticker}
        resolved = sum(1 for t in missing if (mapping.get(t) or {}).get("name"))
        if resolved == 0:
            _PROFILE_BACKOFF_UNTIL = time.time() + PROFILE_BACKOFF
        _save_sector_map(mapping)
    return mapping


def _owned_positions() -> dict:
    """
    My tickers from portfolio.csv + watchlists.json, no per-position quotes.

    `cost_weight_pct` is share of total *cost basis*, not market value: it is the
    only weight derivable without pricing every position, and it is close enough
    to answer "how big is this for me".
    """
    now = time.time()
    if _OWNED_CACHE["data"] is not None and now - _OWNED_CACHE["ts"] < OWNED_TTL:
        return _OWNED_CACHE["data"]

    # Taiwan positions are booked in TWD; left unconverted they would outweigh
    # every US position by roughly the exchange rate.
    global _LAST_USD_TWD
    usd_twd = None
    try:
        from .portfolio import _fetch_price

        usd_twd = _fetch_price("TWD=X")
    except Exception:
        usd_twd = None
    if usd_twd:
        _LAST_USD_TWD = usd_twd
    else:
        usd_twd = _LAST_USD_TWD

    held: dict[str, dict] = {}
    has_twd = False
    try:
        from .portfolio import _read_csv  # local import: avoids an import cycle

        df = _read_csv()
        df.columns = df.columns.str.lower().str.strip()
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).upper().strip()
            if not ticker or ticker in _SKIP_TICKERS:
                continue
            try:
                shares = float(row.get("shares") or 0)
                cost = shares * float(row.get("avg_cost") or 0)
            except (TypeError, ValueError):
                shares, cost = 0.0, 0.0
            if ticker.endswith(".TW"):
                has_twd = True
                if usd_twd:
                    cost /= usd_twd
            entry = held.setdefault(ticker, {"shares": 0.0, "cost_basis": 0.0, "portfolios": []})
            entry["shares"] += shares
            entry["cost_basis"] += cost
            pname = str(row.get("portfolio", "")).strip()
            if pname and pname not in entry["portfolios"]:
                entry["portfolios"].append(pname)
    except Exception:
        held = {}

    # Without an FX rate, TWD-booked positions would swamp every US one — better
    # to publish no weights at all than weights that are wrong by ~30x.
    weights_ok = bool(usd_twd) or not has_twd
    total_cost = sum(e["cost_basis"] for e in held.values()) or 0.0
    for entry in held.values():
        entry["shares"] = round(entry["shares"], 4)
        entry["cost_basis"] = round(entry["cost_basis"], 2)
        entry["cost_weight_pct"] = (
            round(entry["cost_basis"] / total_cost * 100, 2)
            if (total_cost and weights_ok) else None
        )

    watched: dict[str, list[str]] = {}
    try:
        groups = json.loads((settings.DATA_DIR / "watchlists.json").read_text())
        for group, tickers in groups.items():
            for t in tickers or []:
                watched.setdefault(str(t).upper().strip(), []).append(group)
    except Exception:
        watched = {}

    payload = {"held": held, "watched": watched}
    _OWNED_CACHE.update({"data": payload, "ts": now})
    return payload


def _owned_flag(ticker: str, owned: dict) -> dict | None:
    """Per-ticker ownership badge, or None when I neither hold nor watch it."""
    held = owned["held"].get(ticker)
    watched = owned["watched"].get(ticker)
    if not held and not watched:
        return None
    flag = {"held": bool(held), "watchlists": watched or []}
    if held:
        flag.update({
            "shares": held["shares"],
            "portfolios": held["portfolios"],
            "cost_weight_pct": held["cost_weight_pct"],
        })
    return flag


def _load_holdings_store() -> dict[str, list[dict]]:
    try:
        return json.loads(_HOLDINGS_STORE_PATH.read_text())
    except Exception:
        return {}


def _top_holdings(symbol: str, limit: int) -> list[dict]:
    """
    Top constituents (symbol, name, weight) of an ETF, heaviest first.

    Falls back to the last list seen on disk: a fund's composition drifts over
    months, while Yahoo's rate limiter blanks this call for minutes at a time,
    and a slightly stale constituent list beats an empty panel.
    """
    rows: list[dict] = []
    try:
        df = yf.Ticker(symbol).funds_data.top_holdings
    except Exception:
        df = None
    if df is not None and not getattr(df, "empty", True):
        for idx, row in df.iterrows():
            ticker = str(idx).upper().strip()
            if not ticker:
                continue
            try:
                weight = round(float(row.get("Holding Percent")) * 100, 2)
            except (TypeError, ValueError):
                weight = None
            rows.append(
                {"symbol": ticker, "name": str(row.get("Name") or ticker), "weight_pct": weight}
            )

    store = _load_holdings_store()
    if rows:
        store[symbol] = rows
        try:
            _HOLDINGS_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _HOLDINGS_STORE_PATH.write_text(
                json.dumps(store, indent=2, sort_keys=True, ensure_ascii=False)
            )
        except Exception:
            pass
    else:
        rows = store.get(symbol, [])

    rows = sorted(
        rows, key=lambda r: r["weight_pct"] if r["weight_pct"] is not None else -1, reverse=True
    )
    return rows[:limit]


def _batch_closes(tickers: list[str], period: str = "7mo") -> dict[str, pd.Series]:
    """One download for the whole basket → {ticker: close series}."""
    if not tickers:
        return {}
    try:
        raw = yf.download(
            tickers, period=period, auto_adjust=True, progress=False, threads=True,
        )
    except Exception:
        return {}
    if raw is None or raw.empty:
        return {}

    out: dict[str, pd.Series] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" not in raw.columns.get_level_values(0):
            return {}
        close = raw["Close"]
        for t in close.columns:
            series = close[t].dropna()
            if not series.empty:
                out[str(t).upper()] = series
    else:  # single ticker → flat columns
        series = raw["Close"].dropna() if "Close" in raw.columns else pd.Series(dtype=float)
        if not series.empty:
            out[tickers[0].upper()] = series
    return out


# windows the home-page strip can switch between (6m needs more history than we
# pull here, and the strip does not offer it)
QUOTE_PERIODS = {"1w": 5, "1m": 21, "3m": 63}


def _quote_row(close: pd.Series | None) -> dict:
    """Price + the same return windows the sector rows use, so the UI shares code."""
    if close is None or close.empty:
        return {"ok": False, "price": None, "change_pct": None, "returns": {}, "price_spark": []}
    price = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) >= 2 else None
    change_pct = (price - prev) / prev * 100 if prev else None
    returns = {p: _pct_change(close, d) for p, d in QUOTE_PERIODS.items()}
    return {
        "ok": True,
        "price": round(price, 2),
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "returns": {k: (round(v, 2) if v is not None else None) for k, v in returns.items()},
        "price_spark": [round(float(v), 4) for v in close.iloc[-63:]],
    }


def _build_holdings(symbol: str, limit: int) -> dict:
    meta = next((s for s in SECTORS if s["symbol"] == symbol), None)
    rows = _top_holdings(symbol, limit)
    owned = _owned_positions()

    closes = _batch_closes([r["symbol"] for r in rows])
    items = []
    for row in rows:
        item = {**row, **_quote_row(closes.get(row["symbol"]))}
        item["owned"] = _owned_flag(row["symbol"], owned)
        items.append(item)

    # my positions in this sector that are NOT top constituents — the exposure
    # the constituent list alone would hide
    others = []
    if meta:
        my_tickers = sorted(set(owned["held"]) | set(owned["watched"]))
        shown = {r["symbol"] for r in rows}
        sector_map = _resolve_sectors(my_tickers)
        mine_here = [
            t for t in my_tickers
            if t not in shown
            and YF_SECTOR_TO_ETF.get((sector_map.get(t) or {}).get("sector", "")) == symbol
        ]
        other_closes = _batch_closes(mine_here)
        others = [
            {
                "symbol": t,
                "name": (sector_map.get(t) or {}).get("name") or t,
                "weight_pct": None,
                **_quote_row(other_closes.get(t)),
                "owned": _owned_flag(t, owned),
            }
            for t in mine_here
        ]
        # biggest position first: the truncated list should show what matters most
        others.sort(
            key=lambda o: (o["owned"] or {}).get("cost_weight_pct") or -1, reverse=True
        )

    mine = [i for i in items + others if i["owned"]]
    held_weights = [i["owned"].get("cost_weight_pct") for i in mine if i["owned"].get("held")]
    my_weight = (
        round(sum(held_weights), 2) if held_weights and None not in held_weights else None
    )
    coverage = sum(r["weight_pct"] or 0 for r in rows)

    return {
        "symbol": symbol,
        "zh": meta["zh"] if meta else symbol,
        "label": meta["label"] if meta else symbol,
        "items": items,
        "my_others": others,
        "coverage_pct": round(coverage, 2),
        "my_summary": {
            "count": len(mine),
            "held_count": sum(1 for i in mine if i["owned"].get("held")),
            "cost_weight_pct": my_weight,
        },
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.get("/exposure")
def get_sector_exposure():
    """My portfolio + watchlist tickers grouped by the SPDR sector they belong to."""
    owned = _owned_positions()
    tickers = sorted(set(owned["held"]) | set(owned["watched"]))
    sector_map = _resolve_sectors(tickers)

    groups: dict[str, dict] = {}
    unmapped = []
    for t in tickers:
        profile = sector_map.get(t) or {}
        etf = YF_SECTOR_TO_ETF.get(profile.get("sector", ""))
        if not etf:
            unmapped.append(t)
            continue
        entry = groups.setdefault(etf, {"tickers": [], "held_count": 0, "cost_weight_pct": 0.0})
        flag = _owned_flag(t, owned)
        entry["tickers"].append({"symbol": t, "name": profile.get("name"), **(flag or {})})
        if flag and flag.get("held"):
            entry["held_count"] += 1
            weight = flag.get("cost_weight_pct")
            # one unweighted position makes the sector total meaningless
            if weight is None:
                entry["cost_weight_pct"] = None
            elif entry["cost_weight_pct"] is not None:
                entry["cost_weight_pct"] += weight

    for entry in groups.values():
        if entry["cost_weight_pct"] is not None:
            entry["cost_weight_pct"] = round(entry["cost_weight_pct"], 2)
        entry["tickers"].sort(key=lambda x: x.get("cost_weight_pct") or -1, reverse=True)

    return {
        "sectors": groups,
        "unmapped": unmapped,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.get("/{symbol}/holdings")
def get_sector_holdings(symbol: str, limit: int = 10):
    """
    Top constituents of one sector ETF with quotes, plus my own positions in
    that sector. Cached 30 min per symbol.
    """
    sym = symbol.upper().strip()
    limit = max(1, min(limit, 25))
    key = f"{sym}:{limit}"
    now = time.time()
    cached = _HOLDINGS_CACHE.get(key)
    if cached and now - cached["ts"] < HOLDINGS_TTL:
        return cached["data"]

    payload = _build_holdings(sym, limit)
    if not payload["items"] and not payload["my_others"]:
        raise HTTPException(status_code=404, detail=f"No holdings available for {sym}")
    # a constituent list Yahoo failed to return is a transient miss, not an
    # answer worth holding onto for half an hour
    if payload["items"]:
        _HOLDINGS_CACHE[key] = {"data": payload, "ts": now}
    return payload


@router.delete("/cache")
def clear_sector_cache():
    global _CACHE
    _CACHE = {"data": None, "ts": 0}
    _HOLDINGS_CACHE.clear()
    _OWNED_CACHE.update({"data": None, "ts": 0})
    return {"cleared": True}
