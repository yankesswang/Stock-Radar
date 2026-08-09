#!/usr/bin/env python3
"""Earnings registry: per-ticker knowledge base so nothing gets re-discovered.

Two tables in stock-radar/data/earnings_registry.db:

  companies        one row per ticker: CIK, IR URL templates, fiscal-calendar
                   notes, transcript source, stock-radar ticker mapping
  earnings_events  one row per (ticker, quarter): report date, every document
                   URL found, next-day price reaction, vault note path

fetch_ir_docs.py reads templates/CIK from here (skips SEC lookup + URL probing
when already known) and writes back what it finds.

CLI:
  registry.py show [TICKER]              dump companies (and TICKER's events)
  registry.py upsert TICKER k=v ...      set company fields
                                         (cik/radar_ticker/fiscal_note/ir_base/
                                          transcript_source; ir_docs as JSON)
  registry.py record TICKER QUARTER k=v  set event fields
                                         (report_date/reaction_pct/note_path;
                                          doc:<kind>=<url> adds a document)
"""
import json
import pathlib
import sqlite3
import sys
from datetime import date

DB = pathlib.Path(__file__).parents[2] / "data" / "earnings_registry.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  ticker TEXT PRIMARY KEY,
  cik INTEGER,
  radar_ticker TEXT,
  fiscal_note TEXT,
  ir_base TEXT,
  ir_docs TEXT,
  transcript_source TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS earnings_events (
  ticker TEXT,
  quarter TEXT,
  report_date TEXT,
  documents TEXT,
  reaction_pct REAL,
  note_path TEXT,
  created_at TEXT,
  PRIMARY KEY (ticker, quarter)
);
"""


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:  # migration: industry column (層狀輸出第一層，取自 stock-radar industries 表)
        con.execute("ALTER TABLE companies ADD COLUMN industry TEXT")
    except sqlite3.OperationalError:
        pass
    return con


def lookup_industry(ticker):
    """Industry per stock-radar's own taxonomy (stock_valuation.db); None if absent."""
    company = get_company(ticker)
    if company and company.get("industry"):
        return company["industry"]
    rt = (company or {}).get("radar_ticker") or ticker.upper()
    db = pathlib.Path(__file__).parents[2] / "backend" / "stock_valuation.db"
    if not db.exists():
        return None
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT i.name FROM stocks s JOIN industries i ON s.industry_id=i.id WHERE s.ticker=?",
        (rt,),
    ).fetchone()
    con.close()
    if row:
        upsert_company(ticker, industry=row[0])
        return row[0]
    return None


def get_company(ticker):
    with connect() as con:
        row = con.execute("SELECT * FROM companies WHERE ticker=?", (ticker.upper(),)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["ir_docs"] = json.loads(d["ir_docs"]) if d["ir_docs"] else {}
    return d


def upsert_company(ticker, **fields):
    if "ir_docs" in fields and not isinstance(fields["ir_docs"], str):
        fields["ir_docs"] = json.dumps(fields["ir_docs"])
    fields["updated_at"] = date.today().isoformat()
    cols = ", ".join(fields)
    with connect() as con:
        con.execute(
            f"INSERT INTO companies (ticker, {cols}) VALUES (?, {','.join('?' * len(fields))}) "
            f"ON CONFLICT(ticker) DO UPDATE SET "
            + ", ".join(f"{k}=excluded.{k}" for k in fields),
            (ticker.upper(), *fields.values()),
        )


def record_event(ticker, quarter, documents=None, **fields):
    with connect() as con:
        row = con.execute(
            "SELECT documents FROM earnings_events WHERE ticker=? AND quarter=?",
            (ticker.upper(), quarter),
        ).fetchone()
        docs = json.loads(row["documents"]) if row and row["documents"] else {}
        docs.update(documents or {})
        fields["documents"] = json.dumps(docs, ensure_ascii=False)
        fields.setdefault("created_at", date.today().isoformat())
        cols = ", ".join(fields)
        con.execute(
            f"INSERT INTO earnings_events (ticker, quarter, {cols}) "
            f"VALUES (?, ?, {','.join('?' * len(fields))}) "
            f"ON CONFLICT(ticker, quarter) DO UPDATE SET "
            + ", ".join(f"{k}=excluded.{k}" for k in fields),
            (ticker.upper(), quarter, *fields.values()),
        )


def industry_dirname(ticker):
    """Vault folder name for the ticker's industry: slashes are path separators,
    so 'AI 液冷 / 電源 / 散熱 / 儲能' becomes 'AI 液冷／電源／散熱／儲能'."""
    ind = lookup_industry(ticker) or "其他"
    return ind.replace(" / ", "／").replace("/", "／")


def show(ticker=None):
    with connect() as con:
        for row in con.execute("SELECT * FROM companies ORDER BY ticker"):
            if ticker and row["ticker"] != ticker.upper():
                continue
            ind = row["industry"] if "industry" in row.keys() else None
            print(f"== {row['ticker']} (CIK {row['cik']}, radar={row['radar_ticker'] or row['ticker']}, 產業={ind or '?'})")
            if row["fiscal_note"]:
                print(f"   fiscal: {row['fiscal_note']}")
            if row["ir_base"]:
                print(f"   ir_base: {row['ir_base']}")
            for kind, pats in (json.loads(row["ir_docs"]) if row["ir_docs"] else {}).items():
                print(f"   doc[{kind}]: {pats}")
            if row["transcript_source"]:
                print(f"   transcript: {row['transcript_source']}")
            for ev in con.execute(
                "SELECT * FROM earnings_events WHERE ticker=? ORDER BY report_date DESC",
                (row["ticker"],),
            ):
                r = f"{ev['reaction_pct']:+.1f}%" if ev["reaction_pct"] is not None else "?"
                print(f"   {ev['quarter']}  報告日 {ev['report_date']}  隔日 {r}  筆記 {ev['note_path'] or '-'}")
                for kind, url in json.loads(ev["documents"] or "{}").items():
                    print(f"      {kind}: {url}")


def _parse_kv(args):
    fields, docs = {}, {}
    for a in args:
        k, _, v = a.partition("=")
        if k.startswith("doc:"):
            docs[k[4:]] = v
        elif k == "cik" or k == "reaction_pct":
            fields[k] = float(v) if "." in v else int(v)
        else:
            fields[k] = v
    return fields, docs


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "show":
        show(sys.argv[2] if len(sys.argv) > 2 else None)
    elif sys.argv[1] == "upsert":
        fields, _ = _parse_kv(sys.argv[3:])
        upsert_company(sys.argv[2], **fields)
        print(f"ok: {sys.argv[2].upper()}")
    elif sys.argv[1] == "record":
        fields, docs = _parse_kv(sys.argv[4:])
        record_event(sys.argv[2], sys.argv[3], documents=docs, **fields)
        print(f"ok: {sys.argv[2].upper()} {sys.argv[3]}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
