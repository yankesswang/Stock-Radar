#!/usr/bin/env python3
"""Obsidian 投資筆記 → 觀點層（thesis layer）。

我每週手動整理的投資筆記（宏觀策略／科技軟體／AI半導體／財報筆記…）本來完全在
pipeline 視野外：戰情官與獵手的質化脈絡只讀 data/finance-digest（Substack 爬蟲），
看不到「我自己對這檔already有什麼看法」。這支模組把那些筆記變成兩個東西：

  1. 觀點層：ticker → 我的近期論點（餵進 war_room / market_scan 簡報）
  2. universe 擴充：近期筆記提到的標的自動進 Stage-1 預篩名單

## 為什麼要鏡像，不直接讀 vault

vault 在 ~/Documents，檔案是 iCloud 的 `dataless` 佔位檔（近 90 天筆記約 27%）。
2026-09-05 實測（launchctl submit 起的 job）：

    列目錄 OK ／ 讀已具現化的檔 OK ／ 讀 dataless 檔 → OSError(11) Resource deadlock avoided

所以**不是 TCC 權限問題，是檔案具現化問題**：背景行程無法叫 iCloud 下載檔案。
對策是鏡像到 ~/.arthur-digest（一般 APFS，永遠具現化）：

  - mirror()：在有 GUI session 的行程跑（互動 shell / Claude Code）能觸發下載，
    讀得到全部；在 launchd 背景跑則讀得到「還在本地的」，dataless 的記進 pending。
  - build_index() / load_*()：只讀鏡像，背景行程 100% 安全。

pipeline 每天會自己試一次 mirror（能撈多少算多少），撈不到的留在 pending，
你下次在終端機跑 `uv run python scripts/sync_vault_notes.py` 就會補齊。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

VAULT_ROOT = Path(os.environ.get(
    "VAULT_NOTES_VAULT", "~/Documents/arthurwang_DB/投資")).expanduser()
MIRROR_ROOT = Path(os.environ.get(
    "VAULT_NOTES_MIRROR", "~/.arthur-digest/vault_notes/vault-mirror")).expanduser()
STATE_PATH = MIRROR_ROOT.parent / "state.json"
INDEX_PATH = PROJECT_ROOT / "data" / "agent" / "vault-notes" / "index.json"

# 主題資料夾（近期為主）＋ 財報筆記。深度會員/Uncle Stock Notes 量大且多為歷史存檔，
# Stock Digest 是 pipeline 自己的產出（收進來會變成自我迴圈），一律不收。
INCLUDE_DIRS = [
    "宏觀策略", "科技軟體", "AI半導體", "太空", "能源原物料",
    "技術面", "金融保險", "人形機器人", "生技醫療", "量化交易", "財報筆記",
]
EXCLUDE_DIRS = {"Stock Digest", "深度會員", "Uncle Stock Notes",
                "Portfolio_management", ".obsidian", ".git", ".venv", ".trash"}
# 已經有專門管線在讀的大檔，不重複收
EXCLUDE_FILES = {"mimi操作建議總表.md", "投資操作建議總表.md", "投資筆記時間軸.md"}

WINDOW_DAYS = 90          # 索引窗口：近 N 天（依 mtime 篩，鏡像也只鏡這個窗口）
THESIS_MAX_CHARS = 260    # 單檔論點在簡報裡的字數上限
SF_DATALESS = 0x40000000  # macOS：檔案內容不在本機（iCloud 佔位檔）

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
# 財報筆記/ 底下混著我的筆記與原始素材（逐字稿、prepared remarks）。素材不是觀點，
# 論點抽出來會變成「Welcome, everyone. Thank you for standing by...」。只收筆記本身。
_SOURCE_DOC_RE = re.compile(r"(transcript|prepared remarks|逐字稿|新聞稿|earnings call)", re.I)

# 宏觀筆記常引用 ETF / 指數 / 加密當參照物。它們該留在觀點層（我對大盤的看法），
# 但不該進 Stage-1 個股預篩 universe —— 那只是白花 yfinance 額度掃不會買的東西。
NON_STOCK_TICKERS = {
    "SPY", "QQQ", "QQQE", "RSP", "IWM", "DIA", "VOO", "VTI", "VT", "MDY",
    "TLT", "IEF", "SHY", "TLH", "AGG", "LQD", "HYG", "GLD", "SLV", "USO", "UNG",
    "SOXX", "SMH", "IGV", "SKYY", "HACK", "BUG", "PAVE", "MOAT", "COWZ", "ITB", "XHB",
    "XLK", "XLF", "XLE", "XLV", "XLU", "XLP", "XLI", "XLY", "XLB", "XLC", "XLRE",
    "ARKK", "ARKG", "BITO", "IBIT", "KWEB", "FXI", "EEM", "EFA", "VEA", "VWO",
    "TQQQ", "SQQQ", "SPXL", "SPXS", "JEPI", "SCHD", "VUG", "VTV", "VIG", "DVY",
    "SPX", "NDX", "DJI", "VIX", "DXY", "TNX", "US10Y", "BTC", "ETH", "GC", "CL",
}
_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


# ── 鏡像 ──────────────────────────────────────────────────────────────────────

def _is_indexable(rel: Path) -> bool:
    """這個相對路徑該不該進觀點層。"""
    if set(rel.parts) & EXCLUDE_DIRS or rel.name in EXCLUDE_FILES:
        return False
    if rel.parts and rel.parts[0] == "財報筆記":
        return rel.name.endswith("財報筆記.md")     # 同資料夾的逐字稿/簡報是素材
    return not _SOURCE_DOC_RE.search(rel.stem)


def _iter_vault_files(window_days: int):
    """yield (abs_path, rel_path, stat)；只回窗口內、允許資料夾裡的 .md。"""
    if not VAULT_ROOT.exists():
        return
    cutoff = time.time() - window_days * 86400
    roots = [(VAULT_ROOT, VAULT_ROOT.glob("*.md"))]
    for d in INCLUDE_DIRS:
        p = VAULT_ROOT / d
        if p.is_dir():
            roots.append((p, p.rglob("*.md")))
    for _base, it in roots:
        for src in it:
            try:
                rel = src.relative_to(VAULT_ROOT)
            except ValueError:
                continue
            if not _is_indexable(rel):
                continue
            try:
                st = src.lstat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            yield src, rel, st


def mirror(window_days: int = WINDOW_DAYS, force: bool = False,
           materialize: bool = True, verbose: bool = False) -> dict:
    """vault → 鏡像，增量（mtime+size 相同就跳過）。永不 raise。

    materialize=False：遇到 dataless 檔直接記 pending 不嘗試讀取（背景排程用，
    避免無謂的 I/O）；True 時在有 GUI session 的行程會觸發 iCloud 下載。
    """
    result = {"copied": 0, "skipped": 0, "pending": [], "vault_ok": VAULT_ROOT.exists()}
    if not result["vault_ok"]:
        return result          # 讀不到 vault 就別覆蓋上次成功同步的狀態（含 pending 清單）

    for src, rel, st in _iter_vault_files(window_days):
        dst = MIRROR_ROOT / rel
        if not force and dst.exists():
            try:
                d = dst.stat()
                if d.st_size == st.st_size and abs(d.st_mtime - st.st_mtime) < 1:
                    result["skipped"] += 1
                    continue
            except OSError:
                pass
        dataless = bool(st.st_flags & SF_DATALESS)
        if dataless and not materialize:
            result["pending"].append({"rel": str(rel), "reason": "dataless"})
            continue
        try:
            data = src.read_bytes()
        except OSError as e:
            # 背景行程讀 dataless → Errno 11 (EDEADLK)；不是錯誤，是「現在拿不到」
            result["pending"].append({
                "rel": str(rel),
                "reason": "dataless" if dataless else f"{type(e).__name__}:{e.errno}",
            })
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            os.utime(dst, (st.st_atime, st.st_mtime))
        except OSError:
            continue
        result["copied"] += 1
        if verbose:
            print(f"  + {rel}")
    _save_state(result)
    return result


def _save_state(result: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({
            "synced_at": datetime.now().isoformat(timespec="seconds"),
            "copied": result.get("copied", 0),
            "skipped": result.get("skipped", 0),
            "pending": result.get("pending", []),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def pending_count() -> int:
    """還沒鏡像成功的筆記數（daily_manifest 缺件回報用）。"""
    try:
        return len(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("pending") or [])
    except Exception:
        return 0


# ── 解析（只讀鏡像，背景行程安全）──────────────────────────────────────────────

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
# callout 跨了好幾代寫法：[!summary] / [!TLDR] / [!tldr] / [!note]，標籤行本身要丟掉
_SUMMARY_RE = re.compile(r"^>\s*\[![A-Za-z]+\][^\n]*\n((?:^>.*\n?)+)", re.MULTILINE)
_CALLOUT_LABEL_RE = re.compile(r"^\s*\[![A-Za-z]+\][^\n]*")
_TLDR_RE = re.compile(r"^##+\s*(?:TL;DR|TLDR|30\s*秒結論|摘要)\s*\n(.*?)(?=^##\s|\Z)",
                      re.MULTILINE | re.DOTALL)
_ACTION_RE = re.compile(r"(?:動作|操作)[：:]\s*([^；。\n]{2,60})")
# 「停損」後面的數字不一定是停損價：常見誤判是「停損要放在 1.5 到 2 倍 ATR」、
# 「停損評估：若 2027 年前…」。抓全部候選再評分，寧可留空也不寫錯價位。
_STOP_RE = re.compile(r"停損[^0-9\n]{0,10}(\$?)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(\S?)")
_STOP_REJECT_NEXT = set("倍年%％月日到~-–—")


def _parse_frontmatter(text: str) -> dict:
    m = _FM_RE.match(text)
    if not m:
        return {}
    raw = m.group(1)
    try:
        import yaml
        fm = yaml.safe_load(raw)
        if isinstance(fm, dict):
            return fm
    except Exception:
        pass
    # yaml 壞掉（標題含未跳脫的冒號等）→ 退回只撈得到的 key，不整篇放棄
    fm: dict = {}
    key = None
    for line in raw.splitlines():
        if re.match(r"^\s*-\s+", line) and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(line.split("-", 1)[1].strip())
        else:
            m2 = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
            if m2:
                key, val = m2.group(1), m2.group(2).strip()
                fm[key] = val if val else []
    return fm


def _clean(text: str, limit: int) -> str:
    text = _CALLOUT_LABEL_RE.sub("", text or "")                 # [!TLDR] 30 秒結論 這種標籤行
    text = re.sub(r"!?\[\[[^\]]*\]\]", "", text)               # wikilink / 圖片嵌入
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)         # markdown link
    text = re.sub(r"[*#`>|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -—｜")
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _conclusion_block(text: str) -> str:
    """未截斷的結論段（財報筆記的 [!summary] callout / 主題筆記的 TL;DR）。

    論點顯示時會截斷，但停損價常寫在結論最後一句，截斷後就找不到了——所以
    抽停損要用這份完整的，不能用截斷後的論點。
    """
    m = _SUMMARY_RE.search(text)
    if m:
        return re.sub(r"^>\s?", "", m.group(1), flags=re.MULTILINE)
    m = _TLDR_RE.search(text)
    return m.group(1) if m else ""


def _extract_thesis(text: str, limit: int = THESIS_MAX_CHARS) -> str:
    """論點：財報筆記走 [!summary] callout，主題筆記走 TL;DR，都沒有就取正文首段。"""
    m = _SUMMARY_RE.search(text)
    if m:
        return _clean(re.sub(r"^>\s?", "", m.group(1), flags=re.MULTILINE), limit)
    m = _TLDR_RE.search(text)
    if m:
        # TL;DR 是條列，「核心主張」那幾條比「重要數字」有資訊量，優先留
        bullets = [b.strip() for b in m.group(1).splitlines() if b.strip().startswith("-")]
        core = [b for b in bullets if "核心主張" in b or "關鍵" in b] or bullets
        return _clean(" ".join(core), limit)
    body = _FM_RE.sub("", text)
    body = re.sub(r"^#.*$", "", body, flags=re.MULTILINE)
    for para in body.split("\n\n"):
        cleaned = _clean(para, limit)
        if len(cleaned) > 30:
            return cleaned
    return ""


def _extract_stop(text: str, thesis: str = "") -> str:
    """從「停損…數字」的所有候選裡挑最像價位的一個；沒有夠可信的就回空字串。

    一篇筆記常提到好幾個停損位（1 倍 ATR / 2 倍 ATR / 前低），結論段寫的那個才是
    我真正要執行的，所以先只看結論，結論裡沒有才退回全文。
    """
    if thesis:
        hit = _scan_stop(thesis)
        if hit:
            return hit
    return _scan_stop(text)


def _scan_stop(text: str) -> str:
    best, best_score = "", 0
    for m in _STOP_RE.finditer(text):
        dollar, num, nxt = m.group(1), m.group(2), m.group(3)
        if nxt in _STOP_REJECT_NEXT:
            continue                       # 1.5 到 / 2027 年 / 2 倍 → 不是價位
        try:
            if float(num.replace(",", "")) < 3:
                continue                   # 「1.5 倍 ATR」這類倍數，不是價位
        except ValueError:
            continue
        score = (2 if dollar else 0) + (1 if "." in num else 0)
        if not dollar and re.fullmatch(r"(19|20)\d{2}", num):
            continue                       # 裸的四位數多半是年份
        if score > best_score:
            best, best_score = num.replace(",", ""), score
    return best


def _note_tickers(fm: dict, rel: Path) -> list[str]:
    raw: list = []
    for key in ("stocks", "ticker", "tickers"):
        v = fm.get(key)
        if isinstance(v, list):
            raw += v
        elif isinstance(v, str) and v.strip():
            raw.append(v)
    # 財報筆記沒有 stocks 欄位，ticker 在路徑上：財報筆記/<產業>/<TICKER>/<季>/xxx.md
    if rel.parts and rel.parts[0] == "財報筆記" and len(rel.parts) >= 3:
        raw.append(rel.parts[2])
    out: list[str] = []
    for t in raw:
        t = str(t).strip().upper()
        if _TICKER_RE.match(t) and t not in out:
            out.append(t)
    return out[:12]


def _note_date(fm: dict, rel: Path, mtime: float) -> str:
    for key in ("date", "report_date"):
        v = fm.get(key)
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, str):
            m = _DATE_IN_NAME.search(v)
            if m:
                return m.group(1)
    m = _DATE_IN_NAME.search(rel.name)
    if m:
        return m.group(1)
    return date.fromtimestamp(mtime).isoformat()


def parse_note(path: Path, rel: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    fm = _parse_frontmatter(text)
    tickers = _note_tickers(fm, rel)
    thesis = _extract_thesis(text)
    if not thesis and not tickers:
        return None                      # 既沒論點也沒標的 → 對 pipeline 沒價值
    action = _ACTION_RE.search(text)
    stop = _extract_stop(text, _conclusion_block(text))
    tags = fm.get("tags")
    if isinstance(tags, str):
        tags = [tags]
    sector = fm.get("sector") or (rel.parts[0] if len(rel.parts) > 1 else "投資")
    sources = fm.get("sources") if isinstance(fm.get("sources"), list) else []
    url = fm.get("url") or (sources[0] if sources else "")
    return {
        "id": str(rel),
        "title": str(fm.get("title") or rel.stem)[:120],
        "date": _note_date(fm, rel, path.stat().st_mtime),
        "sector": str(sector)[:40],
        "folder": rel.parts[0] if len(rel.parts) > 1 else "（根目錄）",
        "kind": "earnings" if rel.parts and rel.parts[0] == "財報筆記" else "theme",
        "tags": [str(t) for t in (tags or [])][:8],
        "stocks": tickers,
        "thesis": thesis,
        "action": _clean(action.group(1), 40) if action else "",
        "stop_loss": stop,
        "url": str(url or ""),
    }


def build_index(window_days: int = WINDOW_DAYS) -> dict:
    """鏡像 → data/agent/vault-notes/index.json。永不 raise。"""
    cutoff = date.today() - timedelta(days=window_days)
    notes: list[dict] = []
    if MIRROR_ROOT.exists():
        for p in MIRROR_ROOT.rglob("*.md"):
            try:
                rel = p.relative_to(MIRROR_ROOT)
            except ValueError:
                continue
            if not _is_indexable(rel):
                continue
            note = parse_note(p, rel)
            if not note:
                continue
            try:
                if date.fromisoformat(note["date"]) < cutoff:
                    continue
            except ValueError:
                pass
            notes.append(note)
    notes.sort(key=lambda n: n["date"], reverse=True)

    by_ticker: dict[str, list[str]] = {}
    for n in notes:
        for t in n["stocks"]:
            by_ticker.setdefault(t, []).append(n["id"])

    index = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": window_days,
        "notes": notes,
        "by_ticker": by_ticker,
        "pending": pending_count(),
    }
    try:
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return index


# ── 讀取端（war_room / market_scan / opportunity_scanner 用）────────────────────

_CACHE_TTL = 300.0
_cache: dict[str, tuple[float, dict]] = {}


def load_index() -> dict:
    """讀索引；缺檔／壞 JSON 一律 degrade 成空，絕不 raise（pipeline 不能因此掛掉）。"""
    hit = _cache.get("index")
    now = time.monotonic()
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        if not isinstance(index, dict) or not isinstance(index.get("notes"), list):
            raise ValueError("bad index")
    except Exception:
        index = {"built_at": None, "notes": [], "by_ticker": {}, "pending": 0}
    _cache["index"] = (now, index)
    return index


def notes_for_tickers(tickers, days: int = WINDOW_DAYS,
                      per_ticker: int = 2) -> dict[str, list[dict]]:
    """{ticker: [近期筆記]}，每檔最多 per_ticker 篇（新到舊）。"""
    index = load_index()
    if not index["notes"]:
        return {}
    by_id = {n["id"]: n for n in index["notes"]}
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out: dict[str, list[dict]] = {}
    for t in {str(x).strip().upper() for x in tickers if str(x).strip()}:
        picked = [n for nid in index.get("by_ticker", {}).get(t, [])
                  if (n := by_id.get(nid)) and n["date"] >= cutoff]
        # 綜合型週報會把十幾檔列進 stocks:，論點其實跟其中大多數無關。
        # 專門寫這檔的筆記（標題有它、或整篇只談少數幾檔）優先，日期其次。
        picked.sort(key=lambda n: (_relevance(n, t), n["date"]), reverse=True)
        if picked:
            out[t] = picked[:per_ticker]
    return out


def _relevance(note: dict, ticker: str) -> int:
    """這篇筆記有多「就是在談這檔」：標題點名 2 分，只談 ≤3 檔再 1 分。"""
    score = 0
    if re.search(rf"(?<![A-Z]){re.escape(ticker)}(?![A-Z])", note.get("title", "").upper()):
        score += 2
    if len(note.get("stocks") or []) <= 3:
        score += 1
    return score


# 宏觀策略/ 底下混著真正的 regime 筆記與個人理財、商業個案（教養支出、Jellycat…）。
# 只有會影響「今天大盤怎麼看」的才該進戰情官簡報，用關鍵詞過一道。
_MACRO_KEYWORDS = re.compile(
    r"(聯準會|FED|FOMC|升息|降息|利率|殖利率|美債|公債|CPI|PCE|通膨|就業|非農|"
    r"大盤|指數|資金|輪動|流動性|回檔|risk[- ]?off|regime|財政|美元|關稅|景氣|衰退)",
    re.I)


def macro_notes(days: int = 21, limit: int = 3) -> list[dict]:
    """近期宏觀/策略筆記（我對大盤 regime 的看法），給戰情官跟今天的數據對照。"""
    index = load_index()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    picked = []
    for n in index["notes"]:
        if n["date"] < cutoff or n["folder"] not in ("宏觀策略", "（根目錄）", "量化交易"):
            continue
        haystack = n["title"] + " " + " ".join(n.get("tags") or []) + " " + n["thesis"][:120]
        if _MACRO_KEYWORDS.search(haystack):
            picked.append(n)
    return picked[:limit]


def vault_tickers(days: int = 60) -> set[str]:
    """近 N 天筆記提到的標的 —— opportunity_scanner 用來擴充 Stage-1 universe。

    我寫過的東西就是我在關注的東西；沒持倉也沒在 Mimi 名單裡的，本來完全掃不到。
    """
    index = load_index()
    if not index["notes"]:
        return set()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out: set[str] = set()
    for n in index["notes"]:
        if n["date"] >= cutoff:
            out.update(n["stocks"])
    return out - NON_STOCK_TICKERS


def build_thesis_section(tickers, days: int = WINDOW_DAYS, max_chars: int = 1800,
                         header: str | None = None,
                         empty_hint: str | None = None) -> list[str]:
    """「我的觀點庫」簡報段落：只列跟這批 ticker 有關的筆記，總長受 max_chars 限制。"""
    header = header or "## 🧠 我的觀點庫（Obsidian 投資筆記；這是我自己寫的，不是外部研報）"
    lines = [header]
    hits = notes_for_tickers(tickers, days=days)
    if not hits:
        lines.append(empty_hint or
                     f"- （近 {days} 天我的筆記沒寫到這批標的——不代表沒機會，只代表我還沒研究過）")
        lines.append("")
        return lines

    budget = max_chars - len(header)
    lines.append("> 這些是我過去的判斷。**與它一致要說一致、不一致要明講哪裡變了**，不要假裝沒看到。")
    for t in sorted(hits):
        for n in hits[t]:
            bits = [f"- **{t}**｜[{n['date']}] {n['title']}"]
            if n.get("action"):
                bits.append(f"我當時的動作：{n['action']}")
            if n.get("stop_loss"):
                bits.append(f"停損 ${n['stop_loss']}")
            row = "｜".join(bits) + f"\n  └ 論點：{n['thesis']}"
            if len(row) + 1 > budget:
                lines.append("- （其餘筆記因長度略過）")
                budget = 0
                break
            lines.append(row)
            budget -= len(row) + 1
        if budget <= 0:
            break
    lines.append("")
    return lines


def refresh(window_days: int = WINDOW_DAYS, materialize: bool = True,
            verbose: bool = False) -> dict:
    """鏡像 + 重建索引。pipeline 每天開場呼叫一次；永不 raise。"""
    try:
        m = mirror(window_days=window_days, materialize=materialize, verbose=verbose)
    except Exception as e:                      # noqa: BLE001 — 觀點層掛掉不該拖垮整條管線
        m = {"copied": 0, "skipped": 0, "pending": [], "error": str(e)}
    try:
        idx = build_index(window_days=window_days)
    except Exception as e:                      # noqa: BLE001
        idx = {"notes": [], "by_ticker": {}, "error": str(e)}
    _cache.pop("index", None)
    return {"mirror": m, "notes": len(idx.get("notes") or []),
            "tickers": len(idx.get("by_ticker") or {}),
            "pending": len(m.get("pending") or [])}
