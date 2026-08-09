#!/usr/bin/env python3
"""Earnings calendar + publication detector for the /earnings-note automation.

Table earnings_calendar (same DB as the registry) keeps the next expected
report date per watched ticker. Detection of an ACTUAL publication never
trusts the calendar; it re-checks EDGAR for an 8-K with Item 2.02, because
calendar dates move but filings do not lie.

CLI:
  earnings_calendar.py update                    refresh expected dates via yfinance
  earnings_calendar.py show                      table dump
  earnings_calendar.py watch TICKER              add a minimal company row to watch it
  earnings_calendar.py sync-portfolio            watch every operating company held
        in portfolio.csv. The watchlist is otherwise hand-curated, and a company
        missing from it is invisible to pending() — no note ever gets written.
  earnings_calendar.py pending [--wait-days 2] [--window 6]
        print "TICKER FILING_DATE" for every watched company whose earnings
        8-K was filed between wait-days and window days ago and which has no
        note recorded yet. wait-days=2 in Taipei time means roughly the day
        after the US earnings call, so transcripts are out.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import registry

UA = {"User-Agent": "Arthur Wang research gbt.ai.lab.intern1@gmail.com"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS earnings_calendar (
  ticker TEXT PRIMARY KEY,
  next_report_date TEXT,
  source TEXT,
  updated_at TEXT
);
"""


def connect():
    con = registry.connect()
    con.executescript(SCHEMA)
    return con


def watched_tickers():
    with connect() as con:
        return [r["ticker"] for r in con.execute("SELECT ticker FROM companies ORDER BY ticker")]


def update():
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance not installed")
    today = dt.date.today().isoformat()
    for t in watched_tickers():
        nxt = None
        try:
            cal = yf.Ticker(t).calendar or {}
            dates = cal.get("Earnings Date") or []
            future = [d for d in dates if str(d) >= today]
            nxt = str(min(future)) if future else (str(max(dates)) if dates else None)
        except Exception as e:
            print(f"{t}: yfinance error {e}", file=sys.stderr)
        with connect() as con:
            con.execute(
                "INSERT INTO earnings_calendar (ticker, next_report_date, source, updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
                "next_report_date=excluded.next_report_date, source=excluded.source, "
                "updated_at=excluded.updated_at",
                (t, nxt, "yfinance", today),
            )
        print(f"{t}: next report {nxt}")


def latest_earnings_8k(cik):
    """Most recent earnings filing date, or None.

    US filers: 8-K carrying Item 2.02 (Results of Operations).
    Foreign private issuers (ARM, TSM, ASX…): 6-K, which has no item codes at
    all, so instead require an exhibit whose filename looks like an earnings
    release (ex99 / results / earnings) — a bare 6-K can be any announcement.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        sub = json.load(r)
    rec = sub["filings"]["recent"]
    items = rec.get("items", [""] * len(rec["form"]))
    for i in range(len(rec["form"])):
        form = rec["form"][i]
        if form == "8-K" and "2.02" in (items[i] or ""):
            return rec["filingDate"][i]
        if form == "6-K" and _is_6k_earnings(cik, rec["accessionNumber"][i]):
            return rec["filingDate"][i]
    return None


_EARNINGS_WORDS = re.compile(
    r"(unaudited|net revenue|operating income|earnings per|diluted|"
    r"gross margin|first quarter|second quarter|third quarter|fourth quarter)", re.I)


def _is_6k_earnings(cik, accession):
    """Does this 6-K report results?

    Filenames are useless here: TSM uses a2q26e_withguidance..., ASX uses
    dp250875_6k... So read the exhibit text and require several distinct
    earnings terms — a 6-K can just as easily be a monthly revenue update,
    a treasurer change, or a dividend notice.
    """
    acc = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/", headers=UA), timeout=30) as r:
            idx = r.read().decode("utf-8", "replace")
    except Exception:
        return False
    names = [n for n in re.findall(r'href="/Archives/[^"]*/([^/"]+\.htm)"', idx)
             if not re.match(r"R\d+\.htm$", n)]
    for n in names[:3]:
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(f"{base}/{n}", headers=UA), timeout=30) as r:
                raw = r.read().decode("utf-8", "replace")
        except Exception:
            continue
        text = re.sub(r"<[^>]+>", " ", raw)
        if len({w.lower() for w in _EARNINGS_WORDS.findall(text)}) >= 4:
            return True
    return False


def note_exists(ticker, filing_date):
    with connect() as con:
        row = con.execute(
            "SELECT note_path FROM earnings_events WHERE ticker=? AND report_date=? "
            "AND note_path IS NOT NULL AND note_path != ''",
            (ticker.upper(), filing_date),
        ).fetchone()
    return bool(row)


def pending(wait_days, window):
    today = dt.date.today()
    out = []
    for t in watched_tickers():
        company = registry.get_company(t)
        if not company or not company.get("cik"):
            continue
        try:
            filed = latest_earnings_8k(int(company["cik"]))
        except Exception as e:
            print(f"{t}: EDGAR error {e}", file=sys.stderr)
            continue
        if not filed:
            continue
        age = (today - dt.date.fromisoformat(filed)).days
        if wait_days <= age <= window and not note_exists(t, filed):
            out.append((t, filed))
    for t, filed in out:
        print(f"{t} {filed}")


def show():
    with connect() as con:
        for r in con.execute("SELECT * FROM earnings_calendar ORDER BY next_report_date"):
            print(f"{r['ticker']:6s} 下次財報 {r['next_report_date'] or '?'}  (更新 {r['updated_at']})")


def sec_ticker_map():
    """SEC's ticker -> CIK table. ETFs and most funds simply aren't in it."""
    req = urllib.request.Request(
        "https://www.sec.gov/files/company_tickers.json", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return {row["ticker"].upper(): int(row["cik_str"]) for row in json.load(r).values()}


# Funds that DO resolve to a CIK (the issuing trust files with SEC) but never
# report earnings. Tickers absent from SEC's table (SMH, VT) drop out on their own.
FUND_TICKERS = {"VIXY"}


def sync_portfolio():
    """Add every operating company held in portfolio.csv to the watchlist.

    The watchlist was hand-curated, so a newly bought name silently never got an
    earnings note — pending() only iterates this table, and a company missing
    from it is invisible: no EDGAR check, no marker, not even a log line.
    (2026-08-09: AMD plus 7 held names had reported that week and none were seen.)
    """
    import portfolio_context

    held = {r["ticker"] for r in portfolio_context.load_rows()}
    with connect() as con:
        known = {r["ticker"] for r in con.execute("SELECT ticker FROM companies")}
        known_ciks = {r["cik"] for r in con.execute("SELECT cik FROM companies") if r["cik"]}
    missing = sorted(t for t in held - known
                     if t != "CASH" and not t.endswith(".TW") and t not in FUND_TICKERS)
    if not missing:
        print("portfolio sync: watchlist already covers every holding")
        return
    table = sec_ticker_map()
    for t in missing:
        cik = table.get(t)
        if not cik:
            print(f"portfolio sync: {t} not in SEC ticker table (ETF/fund?), skipped")
            continue
        if cik in known_ciks:
            # GOOG vs GOOGL: same filer, one watchlist row is enough
            print(f"portfolio sync: {t} shares CIK {cik} with a watched ticker, skipped")
            continue
        registry.upsert_company(t, cik=cik)
        known_ciks.add(cik)
        print(f"portfolio sync: now watching {t} (CIK {cik})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["update", "show", "pending", "watch", "sync-portfolio"])
    ap.add_argument("ticker", nargs="?")
    ap.add_argument("--wait-days", type=int, default=2)
    ap.add_argument("--window", type=int, default=6)
    a = ap.parse_args()
    if a.cmd == "update":
        update()
    elif a.cmd == "show":
        show()
    elif a.cmd == "sync-portfolio":
        sync_portfolio()
    elif a.cmd == "watch":
        if not a.ticker:
            sys.exit("watch needs TICKER")
        # resolve CIK now so pending() can detect filings immediately
        cik = None
        try:
            cik = sec_ticker_map().get(a.ticker.upper())
        except Exception as e:
            print(f"CIK lookup failed ({e}); fill manually with registry.py upsert", file=sys.stderr)
        if cik:
            registry.upsert_company(a.ticker, cik=cik)
        else:
            registry.upsert_company(a.ticker)
        print(f"watching {a.ticker.upper()} (CIK {cik or '?'}); IR templates fill on first /earnings-note run)")
    else:
        pending(a.wait_days, a.window)


if __name__ == "__main__":
    main()
