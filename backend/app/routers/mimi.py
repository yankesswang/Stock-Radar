"""
Mimi watchlist — read mimi操作建議總表.json (produced by scripts/export_mimi_json.py),
store in DB, serve with live prices.

Endpoints:
  POST /api/mimi/sync        — re-run export script, upsert all tickers
  GET  /api/mimi/watchlist   — all entries + live prices + held status
  GET  /api/mimi/section/{t} — raw Mimi note section for one ticker (+ obsidian URI)
  GET  /api/mimi/sections    — batch: which tickers have a Mimi section
"""
import csv
import json
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import MimiWatchlist

router = APIRouter()

PORTFOLIO_CSV = settings.DATA_DIR / "portfolio.csv"
PORTFOLIO_SKIP = {"", "CASH", "ETORO"}

EXPORT_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "export_mimi_json.py"
MIMI_JSON = Path(settings.MIMI_DOC_PATH).expanduser().parent / "mimi操作建議總表.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _from_analysis(analysis: dict, *keys: str) -> str:
    for k in keys:
        v = analysis.get(k, "")
        if v:
            return v.strip()
    return ""


# New-format columns to auto-add to the existing SQLite table (idempotent).
_NEW_COLUMNS = {
    "action_light": "TEXT DEFAULT ''",
    "return_pct":   "TEXT DEFAULT ''",
    "price_rec":    "REAL",
    "history_json": "TEXT DEFAULT ''",
}


def _ensure_columns(db: Session) -> None:
    """Add new mimi_watchlist columns if the on-disk table predates them."""
    from sqlalchemy import text
    existing = {row[1] for row in db.execute(text("PRAGMA table_info(mimi_watchlist)"))}
    for col, ddl in _NEW_COLUMNS.items():
        if col not in existing:
            db.execute(text(f"ALTER TABLE mimi_watchlist ADD COLUMN {col} {ddl}"))
    db.commit()


# ── Portfolio held tickers ────────────────────────────────────────────────────

def _held_tickers() -> set[str]:
    held: set[str] = set()
    if not PORTFOLIO_CSV.exists():
        return held
    with open(PORTFOLIO_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = row.get("ticker", "").strip().upper()
            if t and t not in PORTFOLIO_SKIP:
                held.add(t)
    return held


# ── Live price fetch ──────────────────────────────────────────────────────────

def _fetch_prices_batch(tickers: list[str]) -> dict[str, dict]:
    """Batch-download 2 days of Close for all tickers in one request."""
    result: dict[str, dict] = {t: {"price": None, "change_pct": None} for t in tickers}
    try:
        # Filter to US-style tickers yfinance can handle; TW tickers need suffix
        df = yf.download(
            tickers,
            period="2d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if df.empty:
            return result

        close = df["Close"] if "Close" in df.columns else df.get("close")
        if close is None:
            return result

        for ticker in tickers:
            col = ticker if ticker in close.columns else None
            if col is None:
                continue
            series = close[col].dropna()
            if len(series) < 1:
                continue
            price = float(series.iloc[-1])
            prev  = float(series.iloc[-2]) if len(series) >= 2 else None
            chg_pct = (price - prev) / prev * 100 if prev and prev > 0 else None
            result[ticker] = {"price": price, "change_pct": chg_pct}
    except Exception:
        pass
    return result


# ── Near-entry-zone check ─────────────────────────────────────────────────────

def _is_near_entry(price: float | None, entry_text: str, threshold: float = 15.0) -> bool:
    if price is None or not entry_text:
        return False
    ranges = re.findall(r"\$?([\d,]+(?:\.\d+)?)\s*[-–~～至到]\s*\$?([\d,]+(?:\.\d+)?)", entry_text)
    for lo_s, hi_s in ranges:
        try:
            lo, hi = float(lo_s.replace(",", "")), float(hi_s.replace(",", ""))
            if lo > hi:
                lo, hi = hi, lo
            if lo <= price <= hi:
                return True
            if price < lo and price >= lo * (1 - threshold / 100):
                return True
            if price > hi and price <= hi * (1 + threshold / 100):
                return True
        except ValueError:
            continue
    singles = re.findall(r"\$?([\d,]+(?:\.\d+)?)\s*(?:以下|以內|below)", entry_text, re.IGNORECASE)
    for s in singles:
        try:
            target = float(s.replace(",", ""))
            if price <= target * (1 + threshold / 100):
                return True
        except ValueError:
            continue
    return False


# ── Raw Mimi note sections (source traceability) ──────────────────────────────
# The War Room cites Mimi in AI theses; these endpoints let the UI show the
# ORIGINAL note section behind a citation, plus an obsidian:// deep link.
#
# TCC caveat: the :6000 backend runs under launchd, which may be denied access
# to ~/Documents. Every read here is wrapped so a PermissionError / OSError
# degrades to a "source unavailable" response with DB-derived fallback fields —
# it must never 500.

_SECTION_CACHE: dict = {"at": 0.0, "sections": None, "error": None}
_SECTION_TTL = 60.0  # seconds


def _split_mimi_sections(text: str) -> dict[str, str]:
    """{TICKER: full section markdown} — same split rule as scripts/daily_analysis.py."""
    sections: dict[str, str] = {}
    for part in text.split("\n#### ")[1:]:
        m = re.match(r"^([A-Z0-9][A-Z0-9\.\-]{0,9})", part.split("\n")[0])
        if not m:
            continue
        # Any following heading above H4 starts the next group — cut there so the
        # last ticker of a group doesn't absorb the next group's heading/preamble.
        body = re.split(r"\n#{1,3} ", part)[0]
        sections[m.group(1).upper()] = ("#### " + body).rstrip().rstrip("-").rstrip()
    return sections


def _load_mimi_sections() -> tuple[dict[str, str], str | None]:
    """Read + parse the Mimi doc with a 60s cache. Returns (sections, error_msg)."""
    now = time.monotonic()
    if _SECTION_CACHE["sections"] is not None and now - _SECTION_CACHE["at"] < _SECTION_TTL:
        return _SECTION_CACHE["sections"], _SECTION_CACHE["error"]

    sections: dict[str, str] = {}
    error: str | None = None
    try:
        doc = Path(settings.MIMI_DOC_PATH).expanduser()
        if not doc.exists():
            error = "找不到 Mimi 筆記檔案，僅顯示已同步欄位"
        else:
            sections = _split_mimi_sections(doc.read_text(encoding="utf-8"))
    except (PermissionError, OSError) as e:  # TCC denial surfaces here (EDEADLK/EPERM)
        error = f"檔案讀取受限（TCC），僅顯示已同步欄位：{type(e).__name__}"
    except Exception as e:  # never take the endpoint down for a parse hiccup
        error = f"筆記解析失敗，僅顯示已同步欄位：{type(e).__name__}"

    _SECTION_CACHE.update(at=now, sections=sections, error=error)
    return sections, error


def _vault_info() -> tuple[str, Path, str]:
    """(vault name, vault root, doc path relative to vault) for the Mimi doc."""
    doc = Path(settings.MIMI_DOC_PATH).expanduser()
    parts = doc.parts
    vault_name = doc.parent.name
    vault_root = doc.parent
    rel = doc.name
    for i, p in enumerate(parts):
        if p.endswith("_DB"):
            vault_name = p
            vault_root = Path(*parts[: i + 1])
            rel = "/".join(parts[i + 1:])
            break
    return vault_name, vault_root, rel


def _uri_for(file_param: str) -> str:
    """obsidian://open URI. `file_param` is a vault-relative path OR a bare note name."""
    vault_name, _, _ = _vault_info()
    return (
        "obsidian://open?vault="
        + urllib.parse.quote(vault_name, safe="")
        + "&file="
        + urllib.parse.quote(file_param, safe="/")
    )


def _obsidian_uri() -> tuple[str, str]:
    """(obsidian://open URI, vault-relative source path) for the Mimi master table."""
    _, _, rel = _vault_info()
    return _uri_for(rel), rel


# ── Vault note index (wikilink → real note path) ──────────────────────────────
# The master table's per-ticker sections end with wikilinks back to the ORIGINAL
# notes (`📖 [[note title]]｜[[note title]]`). Resolving those names to real
# vault paths needs an index of every .md in the vault — too expensive per
# request, so it's cached. Defensive throughout: a permission error or an
# undecodable filename must degrade to "not found", never 500.

_VAULT_INDEX_CACHE: dict = {"at": 0.0, "index": None}
_VAULT_INDEX_TTL = 600.0  # 10 min
_SKIP_DIRS = {".obsidian", ".trash", ".git", ".smart-env", "node_modules"}

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")


def _vault_index() -> dict[str, str]:
    """{note stem: vault-relative path} for every .md in the vault (10 min cache).

    Also indexes each stem with its leading `YYYY-MM-DD ` stripped, so a wikilink
    written before the note was renamed with a date prefix still resolves — but
    only when that undated form is unambiguous across the whole vault.
    """
    import os

    now = time.monotonic()
    cached = _VAULT_INDEX_CACHE["index"]
    if cached is not None and now - _VAULT_INDEX_CACHE["at"] < _VAULT_INDEX_TTL:
        return cached

    index: dict[str, str] = {}
    undated: dict[str, list[str]] = {}
    try:
        _, vault_root, _ = _vault_info()
        for dirpath, dirnames, filenames in os.walk(vault_root, onerror=lambda e: None):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if not fn.endswith(".md"):
                    continue
                try:
                    rel = str(Path(dirpath, fn).relative_to(vault_root))
                except (ValueError, OSError):
                    continue
                stem = fn[:-3]
                # First hit wins; prefer the shallower path on collision.
                prev = index.get(stem)
                if prev is None or rel.count("/") < prev.count("/"):
                    index[stem] = rel
                bare = _DATE_PREFIX_RE.sub("", stem)
                if bare != stem:
                    undated.setdefault(bare, []).append(rel)

        # Undated aliases only when unambiguous, and never shadowing a real note.
        for bare, rels in undated.items():
            if len(rels) == 1 and bare not in index:
                index[bare] = rels[0]
    except (PermissionError, OSError):
        pass  # TCC / unreadable vault → empty index, links fall back to note name
    except Exception:
        pass

    _VAULT_INDEX_CACHE.update(at=now, index=index)
    return index


def _extract_wikilinks(section: str | None) -> list[str]:
    """Note names from `[[name]]`, `[[name|alias]]`, `[[name#heading]]` — in order, deduped."""
    if not section:
        return []
    names: list[str] = []
    for raw in _WIKILINK_RE.findall(section):
        name = raw.split("|", 1)[0].split("#", 1)[0].split("^", 1)[0].strip()
        if name.endswith(".md"):
            name = name[:-3]
        if name and name not in names:
            names.append(name)
    return names


def _source_notes(section: str | None) -> list[dict]:
    """Individual original notes behind a ticker's master-table section."""
    names = _extract_wikilinks(section)
    if not names:
        return []
    index = _vault_index()
    notes = []
    for name in names:
        rel = index.get(name)
        notes.append({
            "title": name,
            "path": rel,
            # Unresolved → still deep-link by note name; Obsidian resolves it itself.
            "obsidian_uri": _uri_for(rel if rel else name),
            "found": rel is not None,
        })
    return notes


def _db_fields(db: Session, ticker: str) -> dict:
    """Parsed fields from the synced DB row — the TCC fallback payload."""
    row = db.get(MimiWatchlist, ticker)
    if row is None:
        return {}
    return {
        "entry_zone":    row.entry_zone_raw or "",
        "stop_loss":     row.stop_loss_raw or "",
        "target_price":  row.target_price_raw or "",
        "catalyst":      row.catalyst_raw or "",
        "position_size": row.position_size_raw or "",
        "company_name":  row.company_name or "",
        "direction":     row.direction or "",
        "action_light":  getattr(row, "action_light", "") or "",
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/sections")
def get_mimi_sections(tickers: str = "", db: Session = Depends(get_db)):
    """Batch source-availability probe.

    `?tickers=AAPL,NVDA` → which of them have a Mimi note. The War Room uses the
    returned ticker set to decide which cards get a MIMI source chip (rather than
    string-matching the AI thesis, which is unreliable). Omit `tickers` for all.
    """
    sections, error = _load_mimi_sections()
    master_uri, rel = _obsidian_uri()

    wanted = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not wanted:
        # No filter → every ticker we know about (doc, else the synced DB).
        wanted = sorted(sections.keys()) or [r.ticker for r in db.query(MimiWatchlist).all()]

    items: dict[str, dict] = {}
    for t in wanted:
        section = sections.get(t)
        fields = _db_fields(db, t)
        # A ticker "has Mimi" if it's in the doc, or (doc unreadable) in the DB.
        if section is None and not (error and fields):
            continue
        notes = _source_notes(section)
        items[t] = {
            "ticker": t,
            "found": True,
            "has_section": section is not None,
            "fields": fields,
            "source_notes": notes,
            # Primary link = the first ORIGINAL note; master table is the fallback.
            "obsidian_uri": notes[0]["obsidian_uri"] if notes else master_uri,
        }

    return {
        "items": items,
        "tickers": sorted(items.keys()),
        "total": len(items),
        "source_path": rel,
        "master_table_uri": master_uri,
        "obsidian_uri": master_uri,
        "source_unavailable": error,
    }


@router.get("/section/{ticker}")
def get_mimi_section(ticker: str, db: Session = Depends(get_db)):
    """Raw Mimi note section for one ticker + obsidian:// deep link.

    Degrades gracefully when the doc can't be read (TCC under launchd): still
    returns found/fields from the synced DB with `source_unavailable` set,
    and `section: null`. Never raises.
    """
    t = ticker.strip().upper()
    sections, error = _load_mimi_sections()
    master_uri, rel = _obsidian_uri()
    fields = _db_fields(db, t)
    section = sections.get(t)

    found = section is not None or bool(fields)
    # The individual ORIGINAL notes this ticker's section cites. Empty when the
    # doc is unreadable (TCC) or the section carries no wikilink.
    notes = _source_notes(section)

    return {
        "ticker": t,
        "found": found,
        "section": section,
        "fields": fields,
        "fallback_fields": fields if section is None else None,
        "source_notes": notes,
        "source_path": rel,
        "master_table_uri": master_uri,
        # Legacy key — now points at the first ORIGINAL note, master table only as fallback.
        "obsidian_uri": notes[0]["obsidian_uri"] if notes else master_uri,
        "source_unavailable": error if section is None else None,
    }


@router.post("/sync")
def sync_mimi_watchlist(db: Session = Depends(get_db)):
    """Run export script, read JSON, upsert all tickers."""
    # Re-generate JSON from source .md
    result = subprocess.run(
        ["uv", "run", "python", str(EXPORT_SCRIPT)],
        cwd=str(EXPORT_SCRIPT.parent.parent),
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr[:500], "synced": 0}

    if not MIMI_JSON.exists():
        return {"ok": False, "error": "JSON output not found", "synced": 0}

    records = json.loads(MIMI_JSON.read_text(encoding="utf-8"))

    _ensure_columns(db)  # auto-add new-format columns to existing SQLite table

    # Flatten: one entry per ticker (multi-ticker blocks share the same data).
    # Process single-ticker canonical blocks first so a group/watchlist block
    # (e.g. Rubin 多錨清單) never overrides a stock's own dedicated block.
    records = sorted(records, key=lambda r: len(r.get("tickers", [])) != 1)
    entries: dict[str, dict] = {}
    for rec in records:
        analysis = rec.get("analysis", {})
        callouts = rec.get("callouts", {})
        names    = rec.get("names", [])
        tickers  = rec.get("tickers", [])

        # Prefer new-format top-level fields; fall back to old analysis lookups.
        entry = {
            "company_name":      names[0] if names else "",
            "entry_zone_raw":    rec.get("entry") or _from_analysis(analysis,
                                     "進場區間", "進場策略", "進場區間或觀察點",
                                     "進場時機", "進場點", "第一批"),
            "stop_loss_raw":     rec.get("stop_loss", ""),
            "position_size_raw": _from_analysis(analysis, "建議倉位", "倉位"),
            "target_price_raw":  rec.get("target") or _from_analysis(analysis,
                                     "第一目標", "第一目標價", "目標價（第一目標）",
                                     "目標價（基準）", "目標價", "長線目標", "第二目標"),
            "time_frame_raw":    _from_analysis(analysis, "時間框架", "主升段預期", "主升段"),
            "catalyst_raw":      rec.get("catalyst") or _from_analysis(
                                     analysis, "關鍵催化劑時間點", "關鍵催化劑", "催化劑"),
            "rec_date":          rec.get("date_first", ""),
            "date_updated":      rec.get("date_updated", ""),
            "tags":              rec.get("heading", "")[:120],
            "sector":            rec.get("sector", ""),
            "direction":         rec.get("direction", ""),
            "logic_raw":         rec.get("logic") or callouts.get("tip", ""),
            "risk_raw":          rec.get("risk") or callouts.get("warning", ""),
            "section_raw":       rec.get("raw_md", ""),
            "action_light":      rec.get("action_light", ""),
            "return_pct":        rec.get("return_pct", ""),
            "price_rec":         rec.get("price_rec"),
            "history_json":      json.dumps(rec.get("history", []), ensure_ascii=False),
        }

        for ticker in tickers:
            t = ticker.strip().upper()
            if t and t not in entries:  # first specific block wins
                entries[t] = entry

    now = datetime.now(timezone.utc)
    for ticker, e in entries.items():
        row = db.get(MimiWatchlist, ticker)
        if row is None:
            row = MimiWatchlist(ticker=ticker)
            db.add(row)
        for k, v in e.items():
            setattr(row, k, v)
        row.last_synced = now

    # Remove tickers no longer in doc
    doc_tickers = set(entries.keys())
    for row in db.query(MimiWatchlist).all():
        if row.ticker not in doc_tickers:
            db.delete(row)

    db.commit()
    return {"ok": True, "synced": len(entries)}


@router.get("/watchlist")
def get_mimi_watchlist(db: Session = Depends(get_db)):
    """Return all Mimi watchlist entries from DB — no price fetch, instant."""
    rows = db.query(MimiWatchlist).order_by(MimiWatchlist.ticker).all()
    if not rows:
        return {"items": [], "total": 0, "synced": False}

    held = _held_tickers()

    items = []
    for row in rows:
        is_held = row.ticker in held
        items.append({
            "ticker":        row.ticker,
            "company_name":  row.company_name,
            "sector":        row.sector,
            "entry_zone":    row.entry_zone_raw,
            "stop_loss":     row.stop_loss_raw,
            "position_size": row.position_size_raw,
            "target_price":  row.target_price_raw,
            "time_frame":    row.time_frame_raw,
            "catalyst":      row.catalyst_raw,
            "direction":     row.direction,
            "action_light":  getattr(row, "action_light", "") or "",
            "return_pct":    getattr(row, "return_pct", "") or "",
            "price_rec":     getattr(row, "price_rec", None),
            "history":       json.loads(getattr(row, "history_json", "") or "[]"),
            "logic":         row.logic_raw,
            "risk":          row.risk_raw,
            "rec_date":      row.rec_date,
            "date_updated":  row.date_updated,
            "tags":          row.tags,
            "held":          is_held,
            "last_synced":   row.last_synced.isoformat() if row.last_synced else None,
        })

    items.sort(key=lambda x: (not x["held"], x["ticker"]))
    return {"items": items, "total": len(items), "synced": True}


@router.get("/watchlist/prices")
def get_mimi_prices(db: Session = Depends(get_db)):
    """Fetch live prices for all watchlist tickers. Called separately by frontend."""
    rows  = db.query(MimiWatchlist).all()
    tickers = [r.ticker for r in rows]
    if not tickers:
        return {}

    prices = _fetch_prices_batch(tickers)

    # Compute near_entry with live price
    entry_map = {r.ticker: r.entry_zone_raw or "" for r in rows}
    result = {}
    for ticker, pdata in prices.items():
        price   = pdata.get("price")
        chg_pct = pdata.get("change_pct")
        is_near = _is_near_entry(price, entry_map.get(ticker, ""))
        result[ticker] = {
            "price":      price,
            "change_pct": round(chg_pct, 2) if chg_pct is not None else None,
            "near_entry": is_near,
        }
    return result
