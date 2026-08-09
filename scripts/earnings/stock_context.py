#!/usr/bin/env python3
"""Collect stock-radar data + price reaction for a ticker (deterministic part of /earnings-note).

Registry-aware version: resolves the stock-radar data ticker from
earnings_registry.db (e.g. GOOGL -> GOOG for ratios/DCF/portfolio files), so
ticker aliases never need re-discovery.

Outputs one JSON blob with:
  - valuation: newest {T}_ratios_*.csv key rows
  - dcf: newest {T}_dcf_*.json scenario summary (with data date for staleness)
  - positions: every account holding the ticker (or its alias) in portfolio.csv
  - portfolio_context: what the position is worth *relative to the book* —
    weight, rank, equal-weight multiple, industry concentration and the dollar
    or share delta to reach each candidate target weight (portfolio_context.py)
  - price: closes via yfinance; with --report-date, the pre-earnings close,
    next-day close and % reaction; unrealized P&L per position

Usage: stock_context.py TICKER [--report-date YYYY-MM-DD] [--no-portfolio]
"""
import argparse
import csv
import glob
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import portfolio_context
import price_technicals
import registry

RADAR = pathlib.Path(__file__).parents[2] / "data"
KEEP = {
    "Market Cap", "Last Close Price", "PE Ratio", "Forward PE", "PEG Ratio",
    "PS Ratio", "P/FCF Ratio", "P/OCF Ratio", "EV/EBITDA Ratio", "EV/Sales Ratio",
    "Debt / Equity Ratio", "Net Debt / Equity Ratio", "Market Cap Growth",
}


def radar_ticker(ticker):
    company = registry.get_company(ticker)
    if company and company.get("radar_ticker"):
        return company["radar_ticker"]
    return ticker.upper()


def newest(pattern):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def load_ratios(ticker):
    f = newest(str(RADAR / f"financials/{ticker}_ratios_*.csv"))
    if not f:
        return None
    out = {"file": f, "metrics": {}}
    with open(f) as fh:
        for row in csv.DictReader(fh):
            if row["Metric"] in KEEP:
                out["metrics"][row["Metric"]] = {
                    "current": row.get("Current"),
                    "fy_prev": row.get(next((k for k in row if k.startswith("FY ")), ""), None),
                }
    return out


def load_dcf(ticker):
    f = newest(str(RADAR / f"dcf/{ticker}_dcf_*.json"))
    if not f:
        return None
    d = json.load(open(f))
    out = {"file": f, "data_date": d.get("data_date"), "price_then": d.get("current_price"),
           "scenarios": {}}
    for name, s in d.get("scenarios", {}).items():
        out["scenarios"][name] = {
            "intrinsic_value": s.get("intrinsic_value"),
            "verdict": s.get("verdict"),
            "fcf_margin_assumed": s.get("assumptions", {}).get("fcf_margin"),
            "wacc": s.get("assumptions", {}).get("wacc"),
        }
    return out


def load_positions(tickers):
    f = RADAR / "portfolio.csv"
    if not f.exists():
        return []
    rows = []
    with open(f) as fh:
        for row in csv.DictReader(fh):
            if row["ticker"].upper() in tickers:
                rows.append({"portfolio": row["portfolio"], "account": row["account"],
                             "ticker": row["ticker"].upper(),
                             "shares": float(row["shares"]), "avg_cost": float(row["avg_cost"])})
    return rows


def load_price(ticker, report_date):
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed"}
    if report_date:  # window must cover the report date even if it is months old
        import datetime as dt
        start = dt.date.fromisoformat(report_date) - dt.timedelta(days=120)
        df = yf.Ticker(ticker).history(start=str(start))
    else:
        df = yf.Ticker(ticker).history(period="4mo")
    closes = {str(k)[:10]: round(float(v), 2) for k, v in df["Close"].items()}
    out = {"closes": closes, "last": list(closes.values())[-1] if closes else None}
    if report_date and len(df):
        # Which session priced the report depends on whether the company
        # reported before the open or after the close — price_technicals picks
        # it off the volume spike, and both scripts must agree.
        i, timing = price_technicals.reaction_session(df, report_date)
        if i:
            pre = float(df["Close"].iloc[i - 1])
            out["report_timing"] = timing
            out["reaction_date"] = str(df.index[i])[:10]
            out["pre_earnings_close"] = round(pre, 2)
            out["reaction_close"] = round(float(df["Close"].iloc[i]), 2)
            out["reaction_pct"] = round((float(df["Close"].iloc[i]) / pre - 1) * 100, 2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--report-date", default=None)
    ap.add_argument("--no-portfolio", action="store_true",
                    help="skip position sizing (it quotes the whole book)")
    a = ap.parse_args()
    t = a.ticker.upper()
    rt = radar_ticker(t)

    ctx = {
        "ticker": t,
        "radar_ticker": rt,
        "valuation": load_ratios(rt),
        "dcf": load_dcf(rt),
        "positions": load_positions({t, rt}),
        "price": load_price(rt, a.report_date),
    }
    last = ctx["price"].get("last")
    if last:
        for p in ctx["positions"]:
            p["unrealized_pct"] = round((last / p["avg_cost"] - 1) * 100, 1)
    if not a.no_portfolio:
        try:
            ctx["portfolio_context"] = portfolio_context.build({t, rt})
        except Exception as e:  # sizing must never take the note down with it
            ctx["portfolio_context"] = {"error": f"{type(e).__name__}: {e}"}
    json.dump(ctx, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
