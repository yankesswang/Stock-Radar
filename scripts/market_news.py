#!/usr/bin/env python3
"""
每日市場新聞策展 —— 「今天我需要知道的新聞」（portal 每日報告「市場新聞」分頁的來源）。

三支日報腳本的分工：
  - war_room.py    盤後復盤：我的持倉今天怎麼了、明天怎麼做（給操作建議）。
  - market_scan.py 機會獵手：universe 裡冒出什麼新機會。
  - market_news.py 本檔：今天大盤與我的股票「發生了什麼事」——只做新聞策展，不給買賣建議。

流程（Python 抓料 → claude 策展 → JSON 落地）：
  1. Python 抓 Google News RSS：大盤/宏觀固定 query + 持倉與 Mimi 觀察名單逐檔 query，
     時間窗內去重後編號成 [1][2][3]… 的素材清單（與 /api/stocks/{t}/news 同源）。
  2. 大盤快照（指數/VIX/債匯金）＋持倉一行表一起進簡報，策展才連得到「對我的意義」。
  3. claude -p 單輪（不開工具、不上網、不多回合）只做三件事：挑出真正重要的、用中文說清楚
     發生什麼、講為什麼對我的持倉重要。連結一律由程式用 refs 編號回填 —— 模型不准自己寫網址
     （同 ai_analysis.py 的來源回填規則，杜絕捏造連結）。
  4. 落地 data/market-news/{date}.json，前端 /api/market-news 直接讀。

claude 沒裝 / 逾時 / 回爛 JSON 時退回「未策展」模式：仍照大盤與 ticker 分組落地當日頭條
（curated=false，前端會標註），分頁永遠有東西可看。

用法：
    uv run python scripts/market_news.py                   # 今日
    uv run python scripts/market_news.py --dry-run         # 只印簡報，不跑 claude、不落地
    uv run python scripts/market_news.py --no-claude       # 只抓新聞不策展（快、零 API 成本）
    uv run python scripts/market_news.py --hours 48 --max-tickers 30 --model sonnet
    uv run python scripts/market_news.py --telegram        # 另外推一則 Telegram 精華
"""
import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import settings
# 個股新聞與 /api/stocks/{ticker}/news 同一個 Google News RSS 來源（含記憶體快取）
from backend.app.routers.news import _fetch_news
from scripts.daily_analysis import (
    MACRO_LABELS,
    fetch_quotes,
    load_portfolio,
    parse_mimi_doc,
    send_telegram,
)

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
NEWS_DIR = Path(settings.DATA_DIR) / "market-news"

# 大盤快照用的代號（比 war_room 精簡：這裡只是給策展當背景，不做技術分析）
SNAPSHOT_INDEX = ["SPY", "QQQ", "IWM", "DIA", "^VIX"]
SNAPSHOT_MACRO = ["TLT", "UUP", "GLD", "BTC-USD"]

# 大盤／宏觀固定 query。兩個關鍵限制：
#   site: 白名單 —— 不限來源的話 Google News 會塞滿孟加拉通膨、英股週報、印度金價這類
#         「關鍵字對、但跟我完全無關」的稿子，佔滿 claude 的 context。
#   when:Nd —— 只回近幾天。
TIER1_SITES = (
    "(site:reuters.com OR site:cnbc.com OR site:bloomberg.com OR site:wsj.com "
    "OR site:ft.com OR site:apnews.com OR site:barrons.com OR site:marketwatch.com)"
)
MARKET_QUERIES = [
    ("大盤", f"{TIER1_SITES} stock market close when:1d"),
    ("大盤", f"{TIER1_SITES} S&P 500 Nasdaq investors when:1d"),
    ("宏觀", f"{TIER1_SITES} Federal Reserve interest rates inflation when:2d"),
    ("宏觀", f"{TIER1_SITES} Treasury yields dollar oil gold when:1d"),
    ("科技/AI", f"{TIER1_SITES} AI chips semiconductor when:1d"),
]

# 內容農場／喊價稿：這些來源在 Google News 佔比極高但幾乎沒有資訊量，先在 Python 端砍掉，
# 不要浪費 claude 的 context 與判斷力。
SOURCE_BLOCKLIST = {
    "zacks.com", "zacks", "the motley fool", "motley fool", "simply wall st",
    "simplywall.st", "insider monkey", "24/7 wall st.", "24/7 wall st", "invezz",
    "benzinga", "stocktwits", "marketbeat", "tipranks", "quiver quantitative",
    "tradingkey", "trefis", "eastern progress", "vinanet.vn", "ad-hoc-news.de",
    "facebook.com", "worldatlas", "mshale",
}
TITLE_BLOCK_RE = re.compile(
    r"(best .* stocks|stocks to (buy|watch)|why .* stock (is|was) (up|down)|"
    r"should you buy|price target|analyst.{0,12}(rating|upgrade|downgrade)s? roundup|"
    r"here's how much|if you invested|dividend .* to buy|portfolio stock holdings|"
    r"(stock|price) (forecast|prediction)|stock sentiment|jim cramer|"
    r"is .{2,30} a (buy|good buy)\?|opinions on|print edition|the 10-point)",
    re.IGNORECASE,
)

# 公司名快取：Google News 用純代號搜尋會抓到同名雜訊（VT → 佛蒙特州、JBL → 醫院），
# 一定要用公司名當關鍵字。名字問過一次就存起來，之後零成本。
NAMES_FILE = NEWS_DIR / "_company_names.json"
TW_SUFFIXES = (".TW", ".TWO")
# 公司名裡沒有辨識度的字，不能拿來當相關性判準
GENERIC_NAME_TOKENS = {
    "inc", "corp", "corporation", "co", "ltd", "plc", "holdings", "holding",
    "company", "group", "the", "class", "shares", "etf", "fund", "index", "trust",
    "limited", "and", "of", "sa", "nv", "ag", "technologies", "technology",
}

DEFAULT_MAX_TICKERS = 35
DEFAULT_HOURS = 30          # 台北早上跑 → 蓋掉整段美股盤中與盤後
PER_QUERY = 10
PER_TICKER = 6
MAX_ITEMS = 15


# ── 公司名解析與個股 query ────────────────────────────────────────────────────

def _significant_tokens(name: str) -> list[str]:
    """公司名裡有辨識度的字（Jabil Inc. → ['Jabil']）。"""
    toks: list[str] = []
    for raw in re.split(r"[^0-9A-Za-z一-鿿]+", name or ""):
        t = raw.strip()
        if not t or t.lower() in GENERIC_NAME_TOKENS:
            continue
        if len(t) >= 3 or re.search(r"[一-鿿]", t):
            toks.append(t)
    return toks


def resolve_names(tickers: list[str], seed: dict[str, str]) -> dict[str, str]:
    """{ticker: 公司名}。先吃 portfolio 給的名字與本地快取，缺的才問 yfinance 一次。"""
    names: dict[str, str] = {}
    try:
        if NAMES_FILE.exists():
            names = {
                k: v for k, v in json.loads(NAMES_FILE.read_text(encoding="utf-8")).items() if v
            }
    except Exception:
        names = {}

    for t, raw in (seed or {}).items():
        n = (raw or "").strip()
        # portfolio 的 company_name 有時就是代號本身（0050.TW → "0050.TW"），那不算名字
        if n and n.upper() != t.upper() and not names.get(t):
            names[t] = n

    missing = [t for t in tickers if not names.get(t)]
    if missing:
        import yfinance as yf

        def _one(t: str) -> tuple[str, str]:
            try:
                info = yf.Ticker(t).info or {}
                return t, str(info.get("shortName") or info.get("longName") or "").strip()
            except Exception:
                return t, ""

        print(f"[news] 查 {len(missing)} 檔公司名（yfinance，之後走快取）...")
        with ThreadPoolExecutor(max_workers=6) as ex:
            for t, n in ex.map(_one, missing):
                if n:
                    names[t] = n
        try:
            NEWS_DIR.mkdir(parents=True, exist_ok=True)
            NAMES_FILE.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[news] 公司名快取寫入失敗（略過）：{e}")
    return names


def ticker_spec(ticker: str, name: str) -> dict:
    """一檔股票的抓取設定：台股走 zh-TW feed，美股用公司名 OR 代號。"""
    code = ticker.split(".")[0]
    if ticker.upper().endswith(TW_SUFFIXES):
        # 台股在美國英文 feed 幾乎沒東西；zh-TW feed 裡代號本身就是最好的關鍵字
        return {"ticker": ticker, "name": name, "lang": "zh-TW", "query": f"{code} 股價 when:2d"}
    if name:
        return {"ticker": ticker, "name": name, "lang": "en",
                "query": f'("{name}" OR "{code} stock") when:2d'}
    return {"ticker": ticker, "name": name, "lang": "en", "query": f"{code} stock when:2d"}


def _relevant_to_ticker(title: str, ticker: str, name: str) -> bool:
    """標題必須真的提到這檔股票 —— 代號（大小寫敏感）或公司名關鍵字。

    這一關比任何 prompt 都可靠：VT 的「Vermont schoolteachers」、JBL 的醫院新聞
    都是這樣混進來的。
    """
    code = ticker.split(".")[0]
    # 代號比對大小寫敏感：新聞標題寫 ticker 一律大寫，"Vt." 這種才不會被當成 $VT
    if re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", title):
        return True
    low = title.lower()
    return any(tok.lower() in low for tok in _significant_tokens(name))


# ── 素材蒐集 ──────────────────────────────────────────────────────────────────

def _norm_title(title: str) -> str:
    """去重用的標題指紋：只留字母數字與中日文，截斷 80 字。"""
    return re.sub(r"[^a-z0-9一-鿿]+", "", (title or "").lower())[:80]


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TAIPEI_TZ)


def _is_noise(item: dict) -> bool:
    source = str(item.get("source") or "").strip().lower()
    if source in SOURCE_BLOCKLIST:
        return True
    return bool(TITLE_BLOCK_RE.search(item.get("title") or ""))


def _fetch_one(job: dict) -> tuple[dict, list[dict]]:
    """job → (job, items)。抓不到一律回空，不中斷整批。"""
    try:
        return job, _fetch_news(job["query"], job["limit"], job.get("lang", "en"))
    except Exception as e:                              # HTTPException / 網路 / XML 都吃掉
        print(f"[news] 抓取失敗（略過）{job['bucket']}：{e}")
        return job, []


def collect_headlines(specs: list[dict], hours: int) -> list[dict]:
    """回傳去重、過濾、依時間新→舊排序並編號的素材清單。"""
    jobs: list[dict] = [
        {"kind": "market", "bucket": label, "query": query, "limit": PER_QUERY, "lang": "en"}
        for label, query in MARKET_QUERIES
    ]
    jobs += [
        {"kind": "ticker", "bucket": spec["ticker"], "query": spec["query"],
         "limit": PER_TICKER, "lang": spec["lang"], "name": spec.get("name") or ""}
        for spec in specs
    ]

    raw: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for job, items in ex.map(_fetch_one, jobs):
            for it in items:
                # 個股新聞：標題沒提到這檔就是同名雜訊，直接丟
                if job["kind"] == "ticker" and not _relevant_to_ticker(
                    it.get("title") or "", job["bucket"], job.get("name") or ""
                ):
                    continue
                raw.append({**it, "kind": job["kind"], "bucket": job["bucket"]})

    cutoff = datetime.now(TAIPEI_TZ) - timedelta(hours=hours)
    pool: list[dict] = []
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    for it in raw:
        title = (it.get("title") or "").strip()
        if not title or _is_noise(it):
            continue
        published = _parse_dt(it.get("published_at"))
        if published and published < cutoff:
            continue
        key = _norm_title(title)
        url = it.get("url") or ""
        if not key or key in seen_titles or (url and url in seen_urls):
            continue
        seen_titles.add(key)
        if url:
            seen_urls.add(url)
        pool.append({
            "title": title,
            "url": url,
            "source": it.get("source") or "",
            "published_at": published.astimezone(TAIPEI_TZ).isoformat() if published else None,
            "kind": it["kind"],
            "bucket": it["bucket"],
            "_sort": published or datetime.min.replace(tzinfo=TAIPEI_TZ),
        })

    pool.sort(key=lambda x: x["_sort"], reverse=True)
    for i, it in enumerate(pool, 1):
        it["idx"] = i
        it.pop("_sort", None)
    return pool


# ── 我的股票（持倉 + Mimi 觀察名單）─────────────────────────────────────────────

def load_universe(max_tickers: int) -> tuple[list[dict], list[str]]:
    """回傳 (持倉一行表資料, 觀察名單 tickers)。任一段失敗都 degrade 成空，不擋整條管線。"""
    holdings: list[dict] = []
    try:
        from backend.app.database import SessionLocal
        db = SessionLocal()
        try:
            payload = load_portfolio(db) or {}
        finally:
            db.close()
        for pname in payload.get("portfolio_names", []) or []:
            pdata = (payload.get("portfolios") or {}).get(pname, {}) or {}
            # get_csv_portfolio 只給 market_value，權重自己除（總值含現金）
            total_value = ((pdata.get("summary") or {}).get("total_value") or 0) or 0
            for h in pdata.get("holdings", []) or []:
                t = str(h.get("ticker") or "").strip().upper()
                if not t or t == "CASH":
                    continue
                mv = h.get("market_value") or 0
                holdings.append({
                    "ticker": t,
                    "portfolio": pname,
                    "weight_pct": (mv / total_value * 100) if total_value else None,
                    "day_change_pct": h.get("day_change_pct"),
                    "return_pct": h.get("return_pct"),
                    "current_price": h.get("current_price"),
                    "company_name": str(h.get("company_name") or "").strip(),
                })
    except Exception as e:
        print(f"[news] 讀持倉失敗（只跑大盤新聞）：{e}")

    # 同一檔可能出現在多個組合（或同組合分筆買）→ 合併成一列，權重相加
    merged: dict[str, dict] = {}
    for h in holdings:
        prev = merged.get(h["ticker"])
        if not prev:
            merged[h["ticker"]] = h
            continue
        prev["weight_pct"] = (prev.get("weight_pct") or 0) + (h.get("weight_pct") or 0)
        if prev["portfolio"] != h["portfolio"]:
            prev["portfolio"] = f"{prev['portfolio']}+{h['portfolio']}"
        prev["company_name"] = prev.get("company_name") or h.get("company_name")
    holdings = sorted(merged.values(), key=lambda h: -(h.get("weight_pct") or 0))

    try:
        watch = [t for t in parse_mimi_doc().keys() if t not in merged]
    except Exception as e:
        print(f"[news] 讀 Mimi 觀察名單失敗（略過）：{e}")
        watch = []

    # 持倉優先佔滿額度，剩下的才留給觀察名單
    room = max(0, max_tickers - len(holdings))
    return holdings, sorted(watch)[:room]


# ── 簡報組裝 ──────────────────────────────────────────────────────────────────

def _fmt_time(iso: str | None) -> str:
    dt = _parse_dt(iso)
    return dt.strftime("%m-%d %H:%M") if dt else "時間不明"


def build_briefing(
    analysis_date: date_cls,
    snapshot: dict,
    holdings: list[dict],
    watch: list[str],
    pool: list[dict],
    hours: int,
) -> str:
    lines: list[str] = []

    def L(s: str = "") -> None:
        lines.append(s)

    L(f"# 📰 今日新聞素材｜{analysis_date.isoformat()}")
    L(f"> 產生於 {datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M 台北時間')}｜時間窗：過去 {hours} 小時")
    L()

    L("## 📈 大盤快照")
    for group, syms in (("指數", SNAPSHOT_INDEX), ("宏觀", SNAPSHOT_MACRO)):
        bits = []
        for sym in syms:
            d = snapshot.get(sym, {})
            if not d:
                continue
            label = MACRO_LABELS.get(sym, sym).split("（")[0]
            pct = f"{d['change_pct']:+.2f}%" if d.get("change_pct") is not None else "—"
            bits.append(f"{label} {pct}")
        if bits:
            L(f"{group}： " + "｜".join(bits))
    L()

    L("## 💼 我的持倉（新聞要連回這裡）")
    if holdings:
        for h in holdings:
            w = f"{h['weight_pct']:.1f}%" if h.get("weight_pct") is not None else "—"
            d = f"{h['day_change_pct']:+.2f}%" if h.get("day_change_pct") is not None else "—"
            r = f"{h['return_pct']:+.1f}%" if h.get("return_pct") is not None else "—"
            p = f"${h['current_price']:.2f}" if h.get("current_price") else "—"
            L(f"- {h['ticker']}｜{h['portfolio']}｜權重 {w}｜今日 {d}｜累計 {r}｜{p}")
    else:
        L("（讀不到持倉，這次只做大盤策展）")
    L()

    if watch:
        L("## 👀 觀察名單（Mimi）")
        L("、".join(watch))
        L()

    by_bucket: dict[str, list[dict]] = {}
    for it in pool:
        by_bucket.setdefault(it["bucket"], []).append(it)

    L(f"## 📰 新聞素材（共 {len(pool)} 則，只有標題、沒有內文）")
    L()
    for label, _q in MARKET_QUERIES:
        items = by_bucket.pop(label, None)
        if not items:
            continue
        L(f"### {label}")
        for it in items:
            L(f"[{it['idx']}] {it['title']} — {it['source'] or '來源不明'}（{_fmt_time(it['published_at'])}）")
        L()

    held = {h["ticker"] for h in holdings}
    for h in holdings:
        items = by_bucket.pop(h["ticker"], None)
        if not items:
            continue
        w = f"{h['weight_pct']:.1f}%" if h.get("weight_pct") is not None else "—"
        d = f"{h['day_change_pct']:+.2f}%" if h.get("day_change_pct") is not None else "—"
        L(f"### ${h['ticker']}（持倉 {w}，今日 {d}）")
        for it in items:
            L(f"[{it['idx']}] {it['title']} — {it['source'] or '來源不明'}（{_fmt_time(it['published_at'])}）")
        L()
    for bucket in sorted(by_bucket):
        items = by_bucket[bucket]
        tag = "觀察名單" if bucket in watch else "其他"
        L(f"### ${bucket}（{tag}）")
        for it in items:
            L(f"[{it['idx']}] {it['title']} — {it['source'] or '來源不明'}（{_fmt_time(it['published_at'])}）")
        L()

    L("---")
    L()
    L("# 你的任務")
    L("你是我的私人市場編輯。上面是今天所有新聞的**標題**（沒有內文，你也不能上網）。")
    L("請挑出「今天我真正需要知道的事」，寫成中文摘要 JSON。")
    L()
    L("規則：")
    L("1. 先大盤／宏觀，再個股。個股順序：我的持倉（權重大的優先）> 觀察名單 > 其他。")
    L("2. 同一件事被多家媒體報 → 只留一則，refs 把相關編號都列上。")
    L("3. **只准引用素材清單裡真的存在的編號**。不准自己寫網址、不准補清單以外的事實、")
    L("   不准從標題腦補數字或細節。標題資訊不足就照標題講，並在句末標「（僅標題）」。")
    L("4. 濾掉：喊價／目標價稿、「N 檔該買的股票」、個股教學稿、跟我持倉與大盤都無關的公司新聞。")
    L("5. 每則 tldr 用 2-3 句：發生什麼 → 為什麼對我重要（連回我的持倉權重／今日漲跌）→ 要注意什麼。")
    L("   **不要給買賣建議**（那是盤後復盤的工作），只講「這件事對我的意義」。")
    L(f"6. 總共 8-{MAX_ITEMS} 則。真的沒大事的日子就少給幾則，不要湊數。")
    L("7. 全程繁體中文（公司名／專有名詞可留英文）。")
    L()
    L("只輸出這個 JSON，不要任何其他文字：")
    L("""{
  "market_summary": "3-4 句話講今天大盤的主線故事（今天在交易什麼、誰在推動）",
  "items": [
    {
      "scope": "market | holding | watchlist",
      "title": "一句話標題：發生了什麼",
      "tickers": ["NVDA"],
      "impact": "利多 | 利空 | 中性",
      "tldr": "2-3 句說明",
      "refs": [12, 40]
    }
  ],
  "skipped": "一句話：這次濾掉了哪類雜訊"
}""")
    return "\n".join(lines)


# ── claude runner（單輪、不開工具）──────────────────────────────────────────────

def run_claude(briefing: str, model: str, timeout: int = 600) -> str | None:
    """單輪 claude -p，只做策展。任何失敗都回 None，由呼叫端退回未策展模式。"""
    cmd = ["claude", "-p", briefing, "--model", model, "--output-format", "json"]
    print(f"[claude] 啟動市場編輯（model={model}, 簡報 {len(briefing):,} chars）...")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(PROJECT_ROOT)
        )
    except subprocess.TimeoutExpired:
        print(f"[claude] 逾時（>{timeout}s）")
        return None
    except FileNotFoundError:
        print("[claude] 找不到 claude CLI")
        return None
    except Exception as e:
        print(f"[claude] 執行例外：{e}")
        return None

    stdout = (result.stdout or "").strip()
    if not stdout:
        print(f"[claude] 無輸出（exit {result.returncode}）：{(result.stderr or '')[:200]}")
        return None
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return stdout                       # 不是 --output-format json 的包裝就當原始文字
    return payload.get("result") or None


def extract_json(raw: str) -> dict | None:
    """容忍 code fence / 前後雜訊地抽出 JSON 物件；失敗回 None（不 raise）。"""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[claude] JSON 解析失敗：{e}")
        return None
    return parsed if isinstance(parsed, dict) else None


# ── 策展結果 → 落地格式（連結一律由程式回填）───────────────────────────────────

SCOPE_LABELS = {"market": "大盤", "holding": "我的持倉", "watchlist": "觀察名單"}
IMPACTS = {"利多", "利空", "中性"}


def normalize_items(parsed: dict, pool: list[dict], held: set[str]) -> list[dict]:
    by_idx = {it["idx"]: it for it in pool}
    out: list[dict] = []
    for raw in parsed.get("items") or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue

        sources: list[dict] = []
        seen: set[str] = set()
        for ref in raw.get("refs") or []:
            try:
                src = by_idx.get(int(ref))
            except (TypeError, ValueError):
                continue
            if not src or src["idx"] in seen:
                continue
            seen.add(src["idx"])
            sources.append({
                "title": src["title"],
                "url": src["url"],
                "source": src["source"],
                "published_at": src["published_at"],
            })

        tickers = [
            str(t).strip().upper() for t in (raw.get("tickers") or []) if str(t).strip()
        ]
        scope = str(raw.get("scope") or "").strip()
        if scope not in SCOPE_LABELS:
            scope = "holding" if any(t in held for t in tickers) else (
                "watchlist" if tickers else "market"
            )
        impact = str(raw.get("impact") or "").strip()
        if impact not in IMPACTS:
            impact = ""

        out.append({
            "scope": scope,
            "label": SCOPE_LABELS[scope],
            "title": title,
            "tickers": tickers,
            "impact": impact,
            "tldr": str(raw.get("tldr") or "").strip(),
            "url": sources[0]["url"] if sources else "",
            "source": sources[0]["source"] if sources else "",
            "published_at": sources[0]["published_at"] if sources else None,
            "sources": sources,
        })

    order = {"market": 0, "holding": 1, "watchlist": 2}
    out.sort(key=lambda x: order.get(x["scope"], 3))
    return out[:MAX_ITEMS]


def fallback_items(pool: list[dict], held: set[str], watch: set[str]) -> list[dict]:
    """未策展模式：直接把頭條照分組吐出來，讓分頁不會開天窗。"""
    out: list[dict] = []
    for it in pool[:30]:
        bucket = it["bucket"]
        if it["kind"] == "market":
            scope = "market"
            tickers: list[str] = []
        else:
            scope = "holding" if bucket in held else ("watchlist" if bucket in watch else "market")
            tickers = [bucket]
        out.append({
            "scope": scope,
            "label": SCOPE_LABELS[scope],
            "title": it["title"],
            "tickers": tickers,
            "impact": "",
            "tldr": "",
            "url": it["url"],
            "source": it["source"],
            "published_at": it["published_at"],
            "sources": [{
                "title": it["title"], "url": it["url"],
                "source": it["source"], "published_at": it["published_at"],
            }],
        })
    order = {"market": 0, "holding": 1, "watchlist": 2}
    out.sort(key=lambda x: order.get(x["scope"], 3))
    return out


def save(payload: dict, analysis_date: date_cls) -> Path:
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    path = NEWS_DIR / f"{analysis_date.isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[save] {path}")
    return path


def build_telegram(payload: dict) -> str:
    lines = [f"📰 今日市場新聞 {payload['date']}",
             "（純事實與來源；買賣結論見 🎖️ War Room 復盤）", ""]
    if not payload.get("curated", True):
        lines.insert(1, "⚠️ 未策展：AI 策展失敗，以下為原始頭條")
    if payload.get("market_summary"):
        lines += [payload["market_summary"], ""]
    for it in payload["items"][:6]:
        tag = f"{it['impact']}｜" if it.get("impact") else ""
        tick = " ".join(f"${t}" for t in it.get("tickers") or [])
        lines.append(f"• [{it['label']}]{tick} {tag}{it['title']}")
        if it.get("tldr"):
            lines.append(f"  {it['tldr']}")
    remaining = max(0, len(payload["items"]) - 6)
    if remaining:
        lines.append(f"\n…另外 {remaining} 則見 portal 每日報告 → 市場新聞")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="每日市場新聞策展（大盤 + 我的持倉）")
    parser.add_argument("--date", help="YYYY-MM-DD（預設今天，台北時間）")
    parser.add_argument("--dry-run", action="store_true", help="只印簡報，不跑 claude、不落地")
    parser.add_argument("--no-claude", action="store_true", help="跳過策展，直接落地未策展頭條")
    parser.add_argument("--model", default="sonnet", help="claude 模型（預設 sonnet）")
    parser.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS,
                        help=f"逐檔抓新聞的股票數上限（預設 {DEFAULT_MAX_TICKERS}）")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS,
                        help=f"新聞時間窗小時數（預設 {DEFAULT_HOURS}）")
    parser.add_argument("--telegram", action="store_true", help="另外推一則 Telegram 精華")
    args = parser.parse_args()

    analysis_date = (
        date_cls.fromisoformat(args.date) if args.date else datetime.now(TAIPEI_TZ).date()
    )

    holdings, watch = load_universe(args.max_tickers)
    tickers = [h["ticker"] for h in holdings] + watch
    print(f"[news] 追蹤 {len(tickers)} 檔（持倉 {len(holdings)}、觀察 {len(watch)}）")

    names = resolve_names(tickers, {h["ticker"]: h.get("company_name", "") for h in holdings})
    specs = [ticker_spec(t, names.get(t, "")) for t in tickers]

    snapshot = {}
    try:
        snapshot = fetch_quotes(SNAPSHOT_INDEX + SNAPSHOT_MACRO)
    except Exception as e:
        print(f"[news] 大盤快照失敗（略過）：{e}")

    pool = collect_headlines(specs, args.hours)
    print(f"[news] 素材 {len(pool)} 則（去重、濾雜訊後）")
    if not pool:
        print("[news] 沒有任何素材，這次不落地。")
        return

    briefing = build_briefing(analysis_date, snapshot, holdings, watch, pool, args.hours)
    if args.dry_run:
        print(briefing)
        print(f"\n[dry-run] 簡報 {len(briefing):,} chars，未呼叫 claude、未落地。")
        return

    held = {h["ticker"] for h in holdings}
    curated, items, summary, skipped = False, [], "", ""
    if not args.no_claude:
        raw = run_claude(briefing, args.model)
        parsed = extract_json(raw) if raw else None
        if parsed:
            items = normalize_items(parsed, pool, held)
            summary = str(parsed.get("market_summary") or "").strip()
            skipped = str(parsed.get("skipped") or "").strip()
            curated = bool(items)
    if not curated:
        print("[news] 退回未策展模式（直接落地頭條）")
        items = fallback_items(pool, held, set(watch))

    payload = {
        "date": analysis_date.isoformat(),
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
        "curated": curated,
        "model": args.model if curated else None,
        "hours": args.hours,
        "count": len(items),
        "total": len(pool),
        "market_summary": summary,
        "skipped": skipped,
        "tickers_tracked": tickers,
        "items": items,
        # 管線定位：這條只給「發生了什麼 + 出處」，不對持倉下任何買賣結論。
        # 前端與 Telegram 都會顯示，避免三條管線在畫面上看起來同等權威。
        "role": "facts_only",
        "role_note": "純事實與來源；買賣結論只由 🎖️ War Room 盤後復盤提供。",
    }
    save(payload, analysis_date)
    print(f"[news] 完成：{len(items)}/{len(pool)} 則{'（AI 策展）' if curated else '（未策展）'}")

    if args.telegram:
        try:
            send_telegram(build_telegram(payload))
        except Exception as e:
            print(f"[telegram] 發送失敗（略過）：{e}")


if __name__ == "__main__":
    main()
