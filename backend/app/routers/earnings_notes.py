"""財報筆記 — 由 /earnings-note skill 產生的個股財報筆記，供個股頁閱讀。

筆記在 Obsidian vault 的層狀結構：投資/財報筆記/{產業}/{TICKER}/{年度_Q}/，
每季一個資料夾，內含筆記 .md 與該季所有附件（官方簡報 PDF、圖表 PNG…）。

讀「非保護目錄」的鏡像而非 ~/Documents：macOS TCC 會擋 launchd 服務讀取
（OSError [Errno 11] Resource deadlock avoided），同 stock_digest 的處理。
鏡像由 scripts/earnings/mirror_notes.sh 產生；鏡像不存在時退回直接讀 vault
（互動啟動的 backend 可行）。

Endpoints:
  GET /api/earnings-notes/conclusions                    — 跨股結論總覽（見下）
  GET /api/earnings-notes/theses                         — 跨股論點追蹤（見下）
  GET /api/earnings-notes/insights/{ticker}              — 單檔的數據＋論點＋深度指標
  GET /api/earnings-notes/{ticker}                       — 該股所有季度（新到舊）
  GET /api/earnings-notes/{ticker}/{quarter}             — 單季全文（圖片連結已改寫）
  GET /api/earnings-notes/{ticker}/{quarter}/asset/{name} — 附件（圖片 / PDF）
"""
import csv
import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

# 個股情報表（scripts/earnings/registry.py 寫入），存每季找到的所有官方連結，
# 含公司官網部落格 / 新聞室文章（key 以 blog 開頭）
REGISTRY_DB = Path(__file__).resolve().parents[3] / "data" / "earnings_registry.db"

# 連結分類：顯示順序 + 中文標籤（未列出的 key 歸「其他」）
DOC_LABELS = {
    "press_release": ("新聞稿", "official"),
    "press_release_pdf": ("新聞稿 PDF", "official"),
    "cfo_commentary": ("CFO 評論", "official"),
    "statements": ("財務報表", "official"),
    "presentation": ("財報簡報", "official"),
    "slides": ("財報簡報", "official"),
    "transcript": ("法說會逐字稿", "official"),
    "prepared_remarks": ("Prepared Remarks", "official"),
    "followup": ("追問場逐字稿", "official"),
    "assets_zip": ("完整文件包", "official"),
    "transcript_3p": ("逐字稿（第三方）", "third_party"),
    "news_release_page": ("IR 新聞稿頁", "official"),
    "webcast": ("法說會回放", "official"),
    "financial_statements": ("財務報表", "official"),
    "shareholder_letter": ("股東信", "official"),
    "key_financial_data": ("財務數據表", "official"),
    "management_report": ("管理層報告", "official"),
    "interim_report": ("期中報告", "official"),
    "ir_page": ("IR 季報頁", "official"),
    "filing": ("SEC 原始文件", "official"),
}


def _norm_q(s: str) -> str:
    return s.replace("_", "").replace("FY", "").upper()


def _registry_links(ticker: str, quarter_dir: str) -> list[dict]:
    """該季在 registry 記錄的連結；公司部落格（blog*）獨立成一類。"""
    if not REGISTRY_DB.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{REGISTRY_DB}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    # 查詢炸掉時原本會跳過 con.close() —— 連線就這樣漏著，撞 EMFILE 時雪上加霜
    try:
        rows = con.execute(
            "SELECT quarter, documents FROM earnings_events WHERE ticker=?",
            (ticker.upper(),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    docs = {}
    for q, blob in rows:
        if _norm_q(q) == _norm_q(quarter_dir) and blob:
            docs = json.loads(blob)
            break

    out, seen = [], set()
    for key, url in docs.items():
        if not url or url in seen:
            continue
        seen.add(url)
        if key.startswith("blog"):
            # blog / blog_ceo / blog:... → 標籤取後綴，沒有就叫「官方部落格」
            suffix = re.sub(r"^blog[_:.-]?", "", key).replace("_", " ").strip()
            out.append({"key": key, "label": suffix or "官方部落格",
                        "kind": "blog", "url": url})
        elif key in DOC_LABELS:
            label, kind = DOC_LABELS[key]
            out.append({"key": key, "label": label, "kind": kind, "url": url})
        elif key == "note":
            continue                       # registry 內部備註，不是連結
        elif "sec.gov" in url:
            # fetch 用原始 exhibit 檔名當 key（amzn-20260630xex991），對人類無意義
            out.append({"key": key, "label": "SEC 原始文件", "kind": "official", "url": url})
        else:
            out.append({"key": key, "label": key, "kind": "other", "url": url})
    order = {"blog": 0, "official": 1, "third_party": 2, "other": 3}
    return sorted(out, key=lambda d: (order.get(d["kind"], 9), d["label"]))

# ── 結論總覽 ─────────────────────────────────────────────────────────────────
# scripts/earnings/extract_conclusions.py 把每篇筆記的操作結論抽成一列，
# 這裡把它跟持倉表接起來，讓「這季我決定做什麼」一頁看完而不用逐篇讀長文。
PORTFOLIO_CSV = Path(__file__).resolve().parents[3] / "data" / "portfolio.csv"

STANCE_LABELS = {
    "add": "加碼", "hold": "續抱", "trim": "減碼", "exit": "出清",
    "avoid": "不進場", "watch": "觀察",
}
# 動作出處：筆記自己寫死的 > 從「操作結論」小節取的 > LLM 補跑的。
# 前端照這個分級標示，推斷來的不會混充成筆記原文。
SOURCE_LABELS = {"explicit": "筆記明寫", "section": "結論小節", "llm": "AI 補讀"}


def _positions() -> dict[str, dict]:
    """portfolio.csv 的持股，同一檔跨帳戶合併股數、成本用股數加權。"""
    if not PORTFOLIO_CSV.exists():
        return {}
    out: dict[str, dict] = {}
    with open(PORTFOLIO_CSV, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                shares, cost = float(row["shares"]), float(row["avg_cost"])
            except (KeyError, ValueError):
                continue
            t = row["ticker"].upper()
            p = out.setdefault(t, {"shares": 0.0, "cost_basis": 0.0, "accounts": []})
            p["shares"] += shares
            p["cost_basis"] += shares * cost
            label = f'{row.get("portfolio", "")}/{row.get("account", "")}'.strip("/")
            if label and label not in p["accounts"]:
                p["accounts"].append(label)
    for p in out.values():
        p["avg_cost"] = round(p["cost_basis"] / p["shares"], 2) if p["shares"] else None
        p.pop("cost_basis")
    return out


def _table_rows(sql: str, args: tuple = ()) -> list[dict]:
    """讀 registry 的一張表；表還沒建（抽取器沒跑過）時回空陣列而不是 500。"""
    if not REGISTRY_DB.exists():
        return []
    con = sqlite3.connect(f"file:{REGISTRY_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.Error:
        return []
    finally:
        con.close()


@router.get("/theses")
def list_theses(held_only: bool = False):
    """論點追蹤表：每檔最新一季的「指標｜現值｜多方需要｜空方確認」。

    同一檔下一季的筆記進來時，metric_key 相同的論點會接成 previous——
    「上季說多方要看到 X，這季實際是 Y」。目前每檔只有一季，所以 previous
    一律是 null，等下一季財報才會有東西。
    """
    rows = _table_rows(
        "SELECT * FROM earnings_theses ORDER BY report_date DESC, ticker, idx")
    if not rows:
        return {"tickers": [], "error": "theses table missing — 先跑 extract_insights.py"}

    positions = _positions()
    # 每檔照 report_date 分季；最新一季當本季，其餘留著給跨季比對
    by_ticker: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], {}).setdefault(r["quarter"], []).append(r)

    stances = {c["ticker"]: c for c in _table_rows(
        "SELECT ticker, quarter, stance, action_text FROM earnings_conclusions")}

    out = []
    for ticker, quarters in by_ticker.items():
        pos = positions.get(ticker)
        if held_only and not pos:
            continue
        ordered = sorted(quarters.items(),
                         key=lambda kv: kv[1][0].get("report_date") or "", reverse=True)
        cur_q, cur_rows = ordered[0]
        prev_rows = {r["metric_key"]: r for r in ordered[1][1]} if len(ordered) > 1 else {}
        theses = []
        for r in cur_rows:
            prev = prev_rows.get(r["metric_key"])
            theses.append({
                "metric": r["metric"], "current_value": r["current_value"],
                "bull_condition": r["bull_condition"], "bear_condition": r["bear_condition"],
                # 上季對同一條論點畫的線，拿來對照這季的現值
                "previous": None if not prev else {
                    "quarter": prev["quarter"], "value": prev["current_value"],
                    "bull_condition": prev["bull_condition"],
                    "bear_condition": prev["bear_condition"],
                },
            })
        conc = stances.get(ticker, {})
        out.append({
            "ticker": ticker, "quarter": cur_q,
            "report_date": cur_rows[0].get("report_date", ""),
            "held": bool(pos), "position": pos,
            "stance": conc.get("stance", ""),
            "stance_label": STANCE_LABELS.get(conc.get("stance") or "", ""),
            "action_text": conc.get("action_text", ""),
            "thesis_count": len(theses),
            "tracked_count": sum(1 for t in theses if t["previous"]),
            "theses": theses,
        })
    out.sort(key=lambda d: d["report_date"], reverse=True)
    return {"count": len(out), "held_count": sum(1 for d in out if d["held"]),
            "tracked_total": sum(d["tracked_count"] for d in out), "tickers": out}


@router.get("/insights/{ticker}")
def get_insights(ticker: str):
    """單檔的關鍵數據、論點與深度指標，最新一季優先。"""
    t = ticker.upper()
    metrics = _table_rows(
        "SELECT * FROM earnings_metrics WHERE ticker=? ORDER BY report_date DESC, idx", (t,))
    grouped: dict[str, dict[str, list[dict]]] = {}
    for m in metrics:
        grouped.setdefault(m["quarter"], {}).setdefault(
            m["section_key"] or m["section"], []).append(m)
    return {
        "ticker": t,
        "metrics": grouped,
        "theses": _table_rows(
            "SELECT * FROM earnings_theses WHERE ticker=? ORDER BY report_date DESC, idx", (t,)),
        "deep": _table_rows(
            "SELECT * FROM earnings_deep_metrics WHERE ticker=? ORDER BY idx", (t,)),
    }


@router.get("/conclusions")
def list_conclusions(held_only: bool = False, limit: int = 200):
    """所有財報筆記的操作結論，新到舊。

    held_only=true 只留 portfolio.csv 裡有部位的標的。
    stance 為空的是抽取器不敢判的（筆記沒寫明確動作），附 candidates 當補件線索，
    前端要標成「待補」而不是當成結論顯示。
    """
    if not REGISTRY_DB.exists():
        return {"conclusions": [], "error": "registry db not found"}
    con = sqlite3.connect(f"file:{REGISTRY_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM earnings_conclusions ORDER BY report_date DESC, ticker LIMIT ?",
            (limit,)).fetchall()
    except sqlite3.Error:
        return {"conclusions": [], "error": "conclusions table missing — 先跑 extract_conclusions.py"}
    finally:
        con.close()

    positions = _positions()
    out = []
    for r in rows:
        d = dict(r)
        pos = positions.get(d["ticker"])
        if held_only and not pos:
            continue
        d["stance_label"] = STANCE_LABELS.get(d["stance"] or "", "")
        d["source_label"] = SOURCE_LABELS.get(d["action_source"] or "", "")
        d["held"] = bool(pos)
        d["position"] = pos
        # 停損距離：現在離筆記畫的那條線還有多遠，負值＝已經跌破
        px, stop = d.get("price_after_earnings"), d.get("stop_loss")
        d["stop_distance_pct"] = round((px - stop) / px * 100, 1) if px and stop else None
        for k in ("action_candidates", "gaps"):
            try:
                d[k] = json.loads(d[k]) if d[k] else []
            except (TypeError, json.JSONDecodeError):
                d[k] = []
        # 30 秒結論動輒上千字、乘上 46 筆就是幾百 KB 的無用 payload；
        # 總覽只需要開頭一段當脈絡，要讀全文點進筆記
        full = (d.pop("summary", "") or "").replace("\n", " ")
        d["summary_short"] = full[:150] + ("…" if len(full) > 150 else "")
        d.pop("conclusion_md", None)               # 同理，整段結論不進總覽
        d.pop("stop_loss_note", None)              # 出處原句是抽取器自我稽核用的
        out.append(d)

    stances = {}
    for d in out:
        key = d["stance"] or "unknown"
        stances[key] = stances.get(key, 0) + 1
    return {"count": len(out), "held_count": sum(1 for d in out if d["held"]),
            "by_stance": stances, "conclusions": out}


MIRROR = Path(os.environ.get(
    "EARNINGS_NOTES_DIR", "~/.arthur-digest/earnings_notes/vault-mirror"
)).expanduser()
VAULT = Path("~/Documents/arthurwang_DB/投資/財報筆記").expanduser()

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _root() -> Path:
    return MIRROR if MIRROR.exists() else VAULT


def _quarter_dirs(ticker: str) -> list[Path]:
    """{產業}/{TICKER}/{季度}/ — 產業層不固定，用 glob 跨過去。"""
    root = _root()
    if not root.exists():
        return []
    return sorted(
        (d for d in root.glob(f"*/{ticker.upper()}/*") if d.is_dir()),
        key=lambda d: d.name, reverse=True,
    )


def _note_file(qdir: Path) -> Path | None:
    files = sorted(qdir.glob("*財報筆記.md"))
    return files[0] if files else None


def _meta(qdir: Path) -> dict:
    note = _note_file(qdir)
    text = note.read_text(encoding="utf-8", errors="replace") if note else ""
    fm = {}
    if text.startswith("---"):
        head = text.split("---", 2)[1] if text.count("---") >= 2 else ""
        for line in head.splitlines():
            if ":" in line and not line.startswith(" "):
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
    assets = [f.name for f in sorted(qdir.iterdir())
              if f.is_file() and not f.name.endswith(".md")]
    return {
        "quarter": qdir.name,
        "industry": qdir.parent.parent.name,
        "title": note.stem if note else qdir.name,
        "report_date": fm.get("report_date", ""),
        "price_after_earnings": fm.get("price_after_earnings", ""),
        "asset_count": len(assets),
        "has_note": note is not None,
        "link_count": len(_registry_links(qdir.parent.name, qdir.name)),
    }


@router.get("/{ticker}")
def list_notes(ticker: str):
    dirs = _quarter_dirs(ticker)
    return {
        "ticker": ticker.upper(),
        "source": "mirror" if _root() is MIRROR else "vault",
        "quarters": [_meta(d) for d in dirs if _note_file(d)],
    }


@router.get("/{ticker}/{quarter}")
def get_note(ticker: str, quarter: str):
    qdir = next((d for d in _quarter_dirs(ticker) if d.name == quarter), None)
    if qdir is None:
        raise HTTPException(404, f"no earnings note for {ticker} {quarter}")
    note = _note_file(qdir)
    if note is None:
        raise HTTPException(404, f"no earnings note for {ticker} {quarter}")
    text = note.read_text(encoding="utf-8", errors="replace")

    # Obsidian 內嵌 ![[檔名.png]] → 後端 asset 路徑，讓瀏覽器讀得到
    base = f"/api/earnings-notes/{ticker.upper()}/{quarter}/asset"
    def _embed(m):
        name = m.group(1).split("|")[0].strip()
        # 檔名含空格，markdown 的 (url) 會在空格處斷掉，必須百分比編碼
        url = f"{base}/{quote(name)}"
        if Path(name).suffix.lower() in IMG_EXT:
            return f"![{name}]({url})"
        return f"[{name}]({url})"                  # PDF 等改成一般連結
    text = re.sub(r"!\[\[([^\]]+)\]\]", _embed, text)
    # 非內嵌的 [[wikilink]]（筆記互連）在網頁沒有目標，降級成純文字
    text = re.sub(r"(?<!!)\[\[([^\]|]+)(?:\|([^\]]+))?\]\]",
                  lambda m: m.group(2) or m.group(1), text)

    meta = _meta(qdir)
    body = text.split("---", 2)[2] if text.startswith("---") and text.count("---") >= 2 else text
    # 卡片 header 已顯示標題，去掉內文第一個 H1 免得重複
    body = re.sub(r"^\s*#\s+.*\n", "", body.lstrip("\n"), count=1)
    return {**meta, "ticker": ticker.upper(), "markdown": body.lstrip("\n"),
            "links": _registry_links(ticker, qdir.name),
            "assets": [f.name for f in sorted(qdir.iterdir())
                       if f.is_file() and not f.name.endswith(".md")]}


@router.get("/{ticker}/{quarter}/asset/{name}")
def get_asset(ticker: str, quarter: str, name: str):
    qdir = next((d for d in _quarter_dirs(ticker) if d.name == quarter), None)
    if not qdir:
        raise HTTPException(404, "quarter not found")
    # 檔名多為中文，不能用字元白名單擋；改確認解析後仍在該季資料夾內
    f = (qdir / name).resolve()
    if qdir.resolve() not in f.parents:
        raise HTTPException(400, "invalid asset path")
    if not f.is_file():
        raise HTTPException(404, f"asset not found: {name}")
    return FileResponse(f)
