#!/usr/bin/env python3
"""Fetch official earnings documents for a ticker (deterministic part of /earnings-note).

Registry-aware version (lives in stock-radar): reads CIK and IR URL templates
from data/earnings_registry.db so known tickers need zero re-discovery, and
writes back every document URL it finds so the next run (or a human) can see
what exists without searching again.

Does, in order:
  1. CIK from registry, else SEC ticker map (then cached into registry).
  2. Latest N earnings 8-Ks (Item 2.02) from EDGAR; every exhibit .htm
     downloaded and stripped to .txt (current + prior quarters = QoQ data).
  3. IR PDF probing from registry templates ({q}/{yyyy}/{yy}/{ORD} placeholders;
     full-URL patterns supported), transcripts excluded — step 5 owns those.
     Hits recorded into earnings_events.
  4. pdftotext every PDF; pdftoppm presentation/statements to PNGs.
  5. Transcript, stockanalysis.com first because it covers every US ticker with
     no per-company config; only if that misses does it fall back to an official
     IR template. Without this file the note gets prepared remarks but no
     analyst Q&A.
  6. manifest.json + summary print.

Usage:
  fetch_ir_docs.py TICKER [--workdir DIR] [--num-8k 2] [--q N --y YYYY] [--before YYYY-MM-DD]

--q/--y override the auto-guessed quarter (fiscal-year companies like AAPL and
NVDA; see the company's fiscal_note in the registry).
"""
import argparse
import datetime as dt
import html.parser
import json
import pathlib
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import registry

# stockanalysis.com 400s on a request with no Accept header, so send one
# everywhere; */*;q=0.8 keeps SEC's JSON endpoints happy.
UA = {
    "User-Agent": "Arthur Wang research gbt.ai.lab.intern1@gmail.com",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def get_text(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def get_bytes(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def head_status(url):
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except Exception as e:
        return getattr(e, "code", 0)


class _TextExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.out = []

    def handle_starttag(self, tag, attrs):
        del attrs
        if tag in ("tr", "p", "div", "br"):
            self.out.append("\n")
        if tag in ("td", "th"):
            self.out.append(" | ")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(raw):
    t = _TextExtractor()
    t.feed(raw)
    return re.sub(r"\n{3,}", "\n\n", "".join(t.out))


def resolve_cik(ticker, workdir):
    company = registry.get_company(ticker)
    if company and company.get("cik"):
        return int(company["cik"])
    cache = workdir / "company_tickers.json"
    if not cache.exists():
        cache.write_text(get_text("https://www.sec.gov/files/company_tickers.json"))
    for row in json.loads(cache.read_text()).values():
        if row["ticker"].upper() == ticker.upper():
            registry.upsert_company(ticker, cik=int(row["cik_str"]))
            return int(row["cik_str"])
    sys.exit(f"CIK not found for {ticker}")


def fetch_8ks(cik, n, workdir, manifest, before=None):
    """before=YYYY-MM-DD: ignore filings after that date, so a backfill run for an
    old quarter sees that quarter's 8-K as press_releases[0] (the one the exhibit
    screenshots, quarter guess and registry write-back all key off)."""
    sub = json.loads(get_text(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    r = sub["filings"]["recent"]
    got = 0
    for i in range(len(r["form"])):
        if got >= n:
            break
        if before and r["filingDate"][i] > before:
            continue
        # Item 2.02 = Results of Operations -> earnings 8-K
        if r["form"][i] != "8-K" or "2.02" not in (r.get("items", [""] * len(r["form"]))[i] or ""):
            continue
        acc = r["accessionNumber"][i].replace("-", "")
        date = r["filingDate"][i]
        idx = get_text(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/")
        links = re.findall(r'href="(/Archives/[^"]*\.htm)"', idx)
        # exhibits = every filed .htm except XBRL viewer pages (R1.htm...) and the
        # 8-K primary document (ticker-YYYYMMDD.htm); covers ex99 AND vendor names
        # like NVDA's q1fy27pr.htm / q1fy27cfocommentary.htm
        exhibits = [u for u in links
                    if not re.search(r"/R\d+\.htm$", u)
                    and not re.search(r"[a-z]+-\d{8}\.htm$", u)]
        if not exhibits:
            continue
        for u in exhibits:
            url = "https://www.sec.gov" + u
            stem = pathlib.Path(u).stem
            out = workdir / f"press_release_{date}_{stem}.txt"
            out.write_text(html_to_text(get_text(url)))
            manifest["press_releases"].append(
                {"date": date, "name": stem, "url": url, "txt": str(out)})
        got += 1
    for i in range(len(r["form"])):
        if before and r["filingDate"][i] > before:
            continue
        if r["form"][i] == "10-Q":
            manifest["latest_10q"] = {
                "date": r["filingDate"][i],
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                f"{r['accessionNumber'][i].replace('-', '')}/{r['primaryDocument'][i]}",
            }
            break


def guess_quarter(manifest):
    """Earnings 8-K lands 25-35 days after quarter end; minus 40 days falls inside it."""
    d = dt.date.fromisoformat(manifest["press_releases"][0]["date"]) - dt.timedelta(days=40)
    return (d.month - 1) // 3 + 1, d.year


ORDINALS = {1: "First", 2: "Second", 3: "Third", 4: "Fourth"}


def probe_ir_docs(ticker, q, y, workdir, manifest, kinds=None):
    """Download the IR PDFs whose templates are on file for this ticker.

    kinds=None means everything except 'transcript': transcripts are owned by
    fetch_transcript(), which tries stockanalysis first and only then calls
    back in here with kinds={'transcript'}.
    """
    company = registry.get_company(ticker)
    if not company or not company.get("ir_docs"):
        manifest["ir_note"] = ("no IR templates in registry; run Step 1.5 web search, "
                               "then registry.py upsert the URL templates")
        return
    wanted = kinds if kinds is not None else set(company["ir_docs"]) - {"transcript"}
    # {ORD} = First/Second/Third/Fourth — Q4-platform IR sites (VRT, ETN...)
    # name files that way instead of q{n}.
    fmt = dict(q=q, yyyy=y, yy=y % 100, ORD=ORDINALS[q])
    base = (company.get("ir_base") or "").format(**fmt)
    for kind, patterns in company["ir_docs"].items():
        if kind not in wanted:
            continue
        for pat in patterns:
            try:
                filled = pat.format(**fmt)
            except KeyError as e:  # unknown placeholder: skip, never kill the run
                manifest.setdefault("template_errors", []).append(f"{kind}: {pat} -> {e}")
                continue
            url = filled if filled.startswith("http") else f"{base}/{filled}"
            if head_status(url) == 200:
                out = workdir / f"{kind}.pdf"
                out.write_bytes(get_bytes(url))
                manifest["ir_docs"][kind] = {"url": url, "pdf": str(out)}
                break


def capture_exhibit_tables(workdir, manifest, min_h=180, min_w=400):
    """Screenshot every financial table in the current quarter's 8-K exhibits.

    Why: the note must carry the *original* statements (income statement,
    GAAP→Non-GAAP reconciliation, balance sheet, cash flow, the KPI series in
    an investor supplement), not only charts the LLM redraws from them. PDF
    decks already get pdftoppm'd; the EDGAR exhibits are HTML, so until now a
    company without a deck (FSLY 2026Q2) ended up with zero official images.
    Renders with Playwright + the SEC-declared UA; each <table> taller than
    min_h px becomes exhibit_{stem}_table_NN.png (2x scale so digits stay
    crisp). Failures never kill the run: the note can still be written, the
    manifest just says why the images are missing.
    """
    prs = manifest["press_releases"]
    if not prs:
        return
    latest = prs[0]["date"]
    targets = [pr for pr in prs if pr["date"] == latest]
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # pragma: no cover
        manifest["exhibit_tables_note"] = f"playwright not available: {e}"
        return
    shots = []
    try:
        with sync_playwright() as pw:
            # Installed Google Chrome first: SEC 403s Playwright's bundled
            # Chromium (old headless build, "Undeclared Automated Tool" page)
            # even with the declared UA, while real Chrome 152 headless passes.
            try:
                browser = pw.chromium.launch(headless=True, channel="chrome")
            except Exception:
                browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA["User-Agent"],
                                      viewport={"width": 1400, "height": 1000},
                                      device_scale_factor=2)
            for pr in targets:
                page = ctx.new_page()
                try:
                    page.goto(pr["url"], wait_until="load", timeout=60000)
                except Exception as e:
                    manifest.setdefault("exhibit_tables_errors", []).append(f"{pr['name']}: {e}")
                    continue
                tables = page.locator("table")
                k = 0
                for i in range(tables.count()):
                    tbl = tables.nth(i)
                    box = tbl.bounding_box()
                    if not box or box["height"] < min_h or box["width"] < min_w:
                        continue
                    k += 1
                    out = workdir / f"exhibit_{pr['name']}_table_{k:02d}.png"
                    tbl.screenshot(path=str(out))
                    # first non-empty row = what the table is about, so SKILL
                    # Step 2 can pick statements without opening every PNG
                    rows_txt = [h.strip() for h in tbl.inner_text().strip().split("\n") if h.strip()]
                    head = " | ".join(rows_txt)[:160]
                    shots.append({"exhibit": pr["name"], "png": str(out), "head": head,
                                  "text": " | ".join(rows_txt)[:4000],
                                  "height": int(box["height"])})
                page.close()
            browser.close()
    except Exception as e:
        manifest["exhibit_tables_note"] = f"screenshot failed: {e}"
    manifest["exhibit_tables"] = shots


def fetch_transcript(ticker, q, y, workdir, manifest):
    """Transcript: stockanalysis.com first, official IR template only as fallback.

    Third-party-first is deliberate. stockanalysis covers every US ticker with
    one URL shape and always carries the analyst Q&A, while official transcript
    PDFs exist for a handful of companies and each needs a hand-found template,
    so this way a new ticker needs no transcript config at all. Whatever the
    source, missing this file means a note with prepared remarks but no Q&A —
    and the Q&A is where management gets pushed on the quarter's sore spot.

    The per-quarter URL carries an opaque id (.../652988-q2-2026/), so the index
    page has to be read; there is nothing to template into the registry.
    """
    t = ticker.lower()
    index = f"https://stockanalysis.com/stocks/{t}/transcripts/"
    try:
        idx = get_text(index)
        m = re.search(rf"/stocks/{re.escape(t)}/transcripts/(\d+-q{q}-{y})/", idx)
        if m:
            url = f"{index}{m.group(1)}/"
            out = workdir / "transcript.txt"
            out.write_text(html_to_text(get_text(url)))
            manifest["ir_docs"]["transcript"] = {
                "url": url, "txt": str(out), "source": "stockanalysis.com"}
            return
        # transcripts land 1-48h after the call, so an empty hit on report day
        # is expected rather than broken.
        why = f"no q{q}-{y} transcript on stockanalysis yet (they appear 1-48h after the call)"
    except Exception as e:
        # 404 = no transcripts section at all, usual for ADRs like TSM.
        why = (f"stockanalysis has no transcripts page for {ticker.upper()} "
               "(usual for ADRs/foreign issuers)"
               if getattr(e, "code", None) == 404 else f"stockanalysis unreachable: {e}")

    probe_ir_docs(ticker, q, y, workdir, manifest, kinds={"transcript"})
    if "transcript" in manifest["ir_docs"]:
        convert(workdir, manifest, kinds={"transcript"})
        manifest["transcript_note"] = f"{why}; fell back to the official IR transcript"
    else:
        manifest["transcript_note"] = (
            f"{why}; no official transcript template on file either — re-run tomorrow, "
            "or find one per SKILL Step 1.5 and registry.py upsert it")


def convert(workdir, manifest, kinds=None):
    for kind, info in manifest["ir_docs"].items():
        if kinds is not None and kind not in kinds:
            continue
        if "pdf" not in info:  # text-only sources (third-party transcript)
            continue
        pdf = pathlib.Path(info["pdf"])
        txt = pdf.with_suffix(".txt")
        subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)], check=False)
        if txt.exists():
            info["txt"] = str(txt)
        if kind in ("presentation", "statements"):
            subprocess.run(
                ["pdftoppm", "-png", "-r", "110", str(pdf), str(workdir / f"{kind}_slide")],
                check=False,
            )
            info["slides"] = sorted(str(p) for p in workdir.glob(f"{kind}_slide*.png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--num-8k", type=int, default=2)
    ap.add_argument("--q", type=int, default=None)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--no-exhibit-shots", action="store_true",
                    help="skip Playwright screenshots of the 8-K exhibit tables")
    ap.add_argument("--before", default=None,
                    help="YYYY-MM-DD: backfill mode, only 8-Ks filed on or before this "
                         "date (pick a date between the target quarter's report and the next one)")
    a = ap.parse_args()

    workdir = pathlib.Path(a.workdir or f"/tmp/earnings_{a.ticker.upper()}").resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    manifest = {"ticker": a.ticker.upper(), "press_releases": [], "ir_docs": {}}

    cik = resolve_cik(a.ticker, workdir)
    manifest["cik"] = cik
    fetch_8ks(cik, a.num_8k, workdir, manifest, before=a.before)
    if not manifest["press_releases"]:
        sys.exit("no earnings 8-K with exhibits found")

    q, y = (a.q, a.y) if a.q and a.y else guess_quarter(manifest)
    manifest["quarter"], manifest["year"] = q, y
    probe_ir_docs(a.ticker, q, y, workdir, manifest)
    convert(workdir, manifest)
    if not a.no_exhibit_shots:
        capture_exhibit_tables(workdir, manifest)
    fetch_transcript(a.ticker, q, y, workdir, manifest)

    # write back to the registry so this quarter never needs re-discovery.
    # Fiscal-year companies (fiscal_note set) with no explicit --q/--y would get
    # a wrong calendar-quarter label here; skip and let SKILL Step 6 record it
    # under the correct fiscal label instead (MSFT test produced a stray row).
    company = registry.get_company(a.ticker)
    label_trusted = bool(a.q and a.y) or not (company and company.get("fiscal_note"))
    if label_trusted:
        quarter_label = f"{y}Q{q}"
        docs = {p["name"]: p["url"] for p in manifest["press_releases"]
                if p["date"] == manifest["press_releases"][0]["date"]}
        for kind, info in manifest["ir_docs"].items():
            # A third-party transcript goes in under its own key: stock-radar's
            # earnings_notes router labels 'transcript' 官方 and 'transcript_3p'
            # 第三方, and a stockanalysis URL under the official key would show
            # up in the UI as if the company had published it. The local file
            # stays transcript.txt either way (SKILL Step 2 reads that name).
            key = "transcript_3p" if kind == "transcript" and info.get("source") else kind
            docs[key] = info["url"]
        registry.record_event(a.ticker, quarter_label,
                              documents=docs,
                              report_date=manifest["press_releases"][0]["date"])
        print(f"registry updated: {a.ticker.upper()} {quarter_label}")
    else:
        print("fiscal company without --q/--y: registry event left for SKILL Step 6")

    (workdir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"workdir: {workdir}")
    print(f"quarter guessed: Q{q} {y} (override with --q/--y if fiscal calendar differs)")
    print(f"press releases: {[p['date'] for p in manifest['press_releases']]}")
    print(f"ir docs found: {list(manifest['ir_docs']) or 'NONE -> Step 1.5 web search, then registry upsert'}")
    shots = manifest.get("exhibit_tables", [])
    print(f"exhibit tables screenshotted: {len(shots)}"
          + (f" ({manifest['exhibit_tables_note']})" if manifest.get("exhibit_tables_note") else ""))
    for s in shots:
        print(f"  {pathlib.Path(s['png']).name}: {s['head'][:90]}")
    if "transcript_note" in manifest:
        print(f"transcript: {manifest['transcript_note']}")
    if "latest_10q" in manifest:
        print(f"latest 10-Q: {manifest['latest_10q']['date']}")


if __name__ == "__main__":
    main()
