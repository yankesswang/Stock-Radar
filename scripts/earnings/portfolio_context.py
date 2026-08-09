#!/usr/bin/env python3
"""Position-sizing context for one ticker (deterministic part of /earnings-note).

Answers the questions an earnings note has to answer before it can say anything
useful about what to do: how big is this position relative to everything else,
how much of it is profit, how crowded is the portfolio already in the same
industry, and what would it cost in dollars and shares to move the weight to a
given target.

Reads stock-radar/data/portfolio.csv (same conventions as the backend router:
CASH rows carry the balance in avg_cost with shares=1, leverage multiplies
exposure, .TW prices are TWD and get converted through TWD=X).

Used as a library by stock_context.py; the CLI is for spot checks:
    portfolio_context.py MU
"""
import csv
import json
import pathlib
import sqlite3
import sys

RADAR = pathlib.Path(__file__).parents[2]
PORTFOLIO = RADAR / "data" / "portfolio.csv"
VALUATION_DBS = [RADAR / "backend" / "stock_valuation.db", RADAR / "data" / "stock_valuation.db"]

# Weight bands, expressed as multiples of the portfolio's equal weight. A 5%
# position means something different in a 6-name book than in a 40-name book,
# so the verdict is always relative, with absolute floors/caps as a backstop.
BANDS = [
    (0.5, "試單", "starter"),
    (0.8, "偏低配", "underweight"),
    (1.5, "核心倉", "core"),
    (2.5, "超配", "overweight"),
    (float("inf"), "過度集中", "concentrated"),
]


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load_rows():
    if not PORTFOLIO.exists():
        return []
    with open(PORTFOLIO) as fh:
        return [
            {
                "portfolio": r["portfolio"],
                "account": r["account"],
                "ticker": r["ticker"].upper(),
                "shares": _num(r["shares"]),
                "avg_cost": _num(r["avg_cost"]),
                "leverage": _num(r.get("leverage"), 1.0) or 1.0,
            }
            for r in csv.DictReader(fh)
        ]


_PRICE_CACHE = {}


def fetch_prices(tickers):
    """Last close per ticker, converted to USD. Missing quotes come back absent.

    Memoised per process: the whole book gets quoted on every call, and batch
    callers ask for the same set once per ticker they are writing up.
    """
    if not tickers:
        return {}, None
    key = frozenset(tickers)
    if key in _PRICE_CACHE:
        return _PRICE_CACHE[key]
    try:
        import yfinance as yf
    except ImportError:
        return {}, None

    symbols = sorted(tickers)
    need_fx = any(t.endswith(".TW") for t in symbols)
    fx = None
    prices = {}
    try:
        wanted = symbols + (["TWD=X"] if need_fx else [])
        closes = yf.download(wanted, period="5d", progress=False,
                             auto_adjust=True, group_by="column")["Close"]
        if not hasattr(closes, "columns"):  # single ticker comes back as a Series
            closes = closes.to_frame(name=wanted[0])
        for sym in closes.columns:
            series = closes[sym].dropna()
            if len(series):
                prices[sym] = float(series.iloc[-1])
        fx = prices.pop("TWD=X", None)
    except Exception:
        return {}, None

    if need_fx and fx:
        for sym in list(prices):
            if sym.endswith(".TW"):
                prices[sym] = prices[sym] / fx
    _PRICE_CACHE[key] = (prices, fx)
    return prices, fx


def load_industries(tickers):
    """ticker -> stock-radar industry name, for concentration math."""
    out = {}
    db = next((d for d in VALUATION_DBS if d.exists()), None)
    if not db or not tickers:
        return out
    try:
        con = sqlite3.connect(db)
        marks = ",".join("?" * len(tickers))
        for tkr, ind in con.execute(
            f"SELECT s.ticker, i.name FROM stocks s JOIN industries i ON s.industry_id=i.id "
            f"WHERE s.ticker IN ({marks})",
            tuple(tickers),
        ):
            out[tkr.upper()] = ind
        con.close()
    except sqlite3.Error:
        pass
    return out


def _band(ratio, weight_pct):
    """Verdict from the equal-weight multiple, with absolute overrides."""
    if weight_pct >= 15:
        return "過度集中", "concentrated"
    if weight_pct >= 10 and ratio < 2.5:
        return "超配", "overweight"
    for cutoff, label, key in BANDS:
        if ratio < cutoff:
            return label, key
    return "過度集中", "concentrated"


def _scenarios(weight_pct, market_value, total_value, price, equal_weight, cash):
    """What it costs in dollars and shares to move to each candidate weight.

    Buying with cash leaves total_value unchanged (cash converts to stock), so
    the target-weight arithmetic is exact as long as the cash covers it.
    """
    if not (total_value and price):
        return []
    candidates = [
        (0.0, "清倉"),
        (round(weight_pct / 2, 1), "砍半"),
        (round(equal_weight, 1), "回到等權"),
        (round(equal_weight * 1.5, 1), "等權 1.5 倍（超配上限）"),
        (10.0, "單一持股 10%"),
    ]
    out, seen = [], []
    for target, label in sorted(candidates):
        if not 0 <= target <= 30:
            continue
        if any(abs(target - s) < 0.5 for s in seen) or abs(target - weight_pct) < 0.5:
            continue
        delta_usd = target / 100 * total_value - market_value
        if abs(delta_usd / price) < 0.5:  # under half a share, not an action
            continue
        seen.append(target)
        row = {
            "target_weight_pct": target,
            "label": label,
            "delta_usd": round(delta_usd, 0),
            "delta_shares": round(delta_usd / price, 1),
            "action": "加碼" if delta_usd > 0 else ("清倉" if target == 0 else "減碼"),
        }
        if delta_usd > 0:
            row["cash_sufficient"] = cash >= delta_usd
            row["cash_available_usd"] = round(cash, 0)
        out.append(row)
    return out


def build(ticker_aliases):
    """Position context for a ticker (pass {TICKER, radar_ticker} as aliases)."""
    aliases = {t.upper() for t in ticker_aliases if t}
    rows = load_rows()
    if not rows:
        return {"held": False, "reason": "portfolio.csv not found"}

    held_in = sorted({r["portfolio"] for r in rows if r["ticker"] in aliases})
    quote_tickers = {r["ticker"] for r in rows if r["ticker"] != "CASH"}
    prices, fx = fetch_prices(quote_tickers)
    industries = load_industries(quote_tickers)

    out = {"held": bool(held_in), "held_in": held_in, "usd_twd": fx, "portfolios": {}}

    for pname in sorted({r["portfolio"] for r in rows}):
        prows = [r for r in rows if r["portfolio"] == pname]
        cash = sum(r["avg_cost"] for r in prows if r["ticker"] == "CASH")

        # Aggregate lots: the same ticker can appear once per account, and more
        # than once per account when it was bought at different prices.
        holdings = {}
        for r in prows:
            if r["ticker"] == "CASH":
                continue
            price = prices.get(r["ticker"])
            h = holdings.setdefault(r["ticker"], {
                "ticker": r["ticker"], "shares": 0.0, "cost_basis": 0.0,
                "market_value": 0.0, "effective_exposure": 0.0, "price": price,
                "accounts": set(), "lots": [],
            })
            h["shares"] += r["shares"]
            h["cost_basis"] += r["shares"] * r["avg_cost"]
            h["accounts"].add(r["account"])
            h["lots"].append({"account": r["account"], "shares": r["shares"],
                              "avg_cost": round(r["avg_cost"], 2)})
            if price:
                h["market_value"] += r["shares"] * price
                h["effective_exposure"] += r["shares"] * price * r["leverage"]

        total_value = sum(h["market_value"] for h in holdings.values()) + cash
        if total_value <= 0:
            continue
        invested_pct = (total_value - cash) / total_value * 100
        n = len(holdings) or 1
        equal_weight = invested_pct / n

        ranked = sorted(holdings.values(), key=lambda h: -h["market_value"])
        for i, h in enumerate(ranked, 1):
            h["weight_pct"] = round(h["market_value"] / total_value * 100, 2)
            h["rank"] = i

        # Industry concentration, computed on the whole book so the note can say
        # "you already have X% in this industry" before recommending more.
        by_industry = {}
        for h in ranked:
            ind = industries.get(h["ticker"], "未分類")
            by_industry.setdefault(ind, {"weight_pct": 0.0, "tickers": []})
            by_industry[ind]["weight_pct"] += h["weight_pct"]
            by_industry[ind]["tickers"].append(h["ticker"])
        for v in by_industry.values():
            v["weight_pct"] = round(v["weight_pct"], 2)

        summary = {
            "total_value_usd": round(total_value, 0),
            "cash_usd": round(cash, 0),
            "cash_pct": round(cash / total_value * 100, 2),
            "n_holdings": n,
            "equal_weight_pct": round(equal_weight, 2),
            "top1_weight_pct": ranked[0]["weight_pct"] if ranked else 0,
            "top3_weight_pct": round(sum(h["weight_pct"] for h in ranked[:3]), 2),
            "top5": [{"ticker": h["ticker"], "weight_pct": h["weight_pct"]} for h in ranked[:5]],
        }

        block = {"summary": summary}
        match = next((h for h in ranked if h["ticker"] in aliases), None)
        if match:
            ratio = match["weight_pct"] / equal_weight if equal_weight else 0
            unrealized = match["market_value"] - match["cost_basis"]
            label, key = _band(ratio, match["weight_pct"])
            ind = industries.get(match["ticker"], "未分類")
            block["position"] = {
                "ticker": match["ticker"],
                "shares": round(match["shares"], 4),
                "avg_cost": round(match["cost_basis"] / match["shares"], 2) if match["shares"] else None,
                "price": match["price"],
                "cost_basis_usd": round(match["cost_basis"], 0),
                "market_value_usd": round(match["market_value"], 0),
                "unrealized_usd": round(unrealized, 0),
                "unrealized_pct": round(unrealized / match["cost_basis"] * 100, 1) if match["cost_basis"] else None,
                "weight_pct": match["weight_pct"],
                "weight_rank": f"{match['rank']}/{n}",
                "equal_weight_multiple": round(ratio, 2),
                "sizing_verdict": label,
                "sizing_key": key,
                "leverage_note": "含槓桿部位" if match["effective_exposure"] > match["market_value"] * 1.01 else None,
                "accounts": sorted(match["accounts"]),
                "lots": match["lots"],
                "industry": ind,
                "industry_weight_pct": by_industry.get(ind, {}).get("weight_pct"),
                "industry_peers": [t for t in by_industry.get(ind, {}).get("tickers", []) if t != match["ticker"]],
                # What a move in this name does to the whole book — the number
                # that decides whether a drawdown here is survivable.
                "portfolio_impact_if_down_20pct": round(-0.20 * match["weight_pct"], 2),
                "weight_scenarios": _scenarios(match["weight_pct"], match["market_value"],
                                               total_value, match["price"] or 0,
                                               equal_weight, cash),
            }
        block["industry_exposure"] = dict(sorted(by_industry.items(),
                                                 key=lambda kv: -kv[1]["weight_pct"])[:6])
        out["portfolios"][pname] = block

    if not held_in:
        # Not held: sizing advice still needs a denominator, so hand over the
        # main book's cash and equal weight to size a starter position against.
        main = out["portfolios"].get("Main") or next(iter(out["portfolios"].values()), None)
        if main:
            s = main["summary"]
            out["starter_sizing"] = {
                "reference_portfolio": "Main" if "Main" in out["portfolios"] else None,
                "cash_available_usd": s["cash_usd"],
                "equal_weight_pct": s["equal_weight_pct"],
                "starter_3pct_usd": round(s["total_value_usd"] * 0.03, 0),
                "full_position_usd": round(s["total_value_usd"] * s["equal_weight_pct"] / 100, 0),
            }
    return out


def main():
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else None
    if not ticker:
        sys.exit(__doc__)
    json.dump(build({ticker}), sys.stdout, indent=2, ensure_ascii=False, default=str)
    print()


if __name__ == "__main__":
    main()
