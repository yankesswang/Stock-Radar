#!/usr/bin/env python3
"""
市場機會掃描引擎（Stage-2 Agent 掃描器）— 讓 claude -p 對量化預篩入圍的機會做定性盡調。

兩段漏斗：
  Stage-1（純 Python，零 claude 成本）：src.analysis.opportunity_scanner.prescreen 掃全
    universe（追蹤股 ∪ Mimi 名單，約 225-260 檔），分類到 setup buckets、signal_score 排名，
    收斂到 top N（預設 20）。
  Stage-2（本檔）：把 top N 的量化 context 組成精簡簡報（<8k chars），交給 claude -p 帶
    curl / WebSearch 工具，對每個入圍機會自主查 news/earnings/chart/dcf/peers + 查催化劑，
    技術面+基本面+催化劑三者綜合，輸出結構化機會 JSON + 一段 Telegram 精華文字。

輸出：解析 JSON → data/agent/opportunities/{date}.json；Telegram 發精華；可選 Obsidian。

架構比照 scripts/war_room.py（claude -p 帶工具權限、在 agent/ 工作、復用 daily_analysis
輸出管線），但這裡是「機會獵手」而非「持倉戰情官」。

用法：
    uv run python scripts/market_scan.py                    # 今日，正常跑
    uv run python scripts/market_scan.py --dry-run          # 只印簡報，不跑 claude
    uv run python scripts/market_scan.py --limit 20 --model sonnet --max-turns 35
    uv run python scripts/market_scan.py --limit 2          # 小煙霧測試（省 claude 錢）
    uv run python scripts/market_scan.py --no-telegram --no-obsidian
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

# 復用 daily_analysis 的輸出管線（Telegram / LINE）
from scripts.daily_analysis import send_telegram, send_line
# Stage-1 預篩（直接 import，不打自己的 HTTP）
from src.analysis.opportunity_scanner import prescreen
from backend.app.config import settings
# 財經 digest（每日 Substack 投資筆記）— 直接 import 讀檔，不打自己的 HTTP
from backend.app.routers.finance_digest import load_recent_items

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
AGENT_DIR = PROJECT_ROOT / "agent"
BACKEND_BASE = "http://127.0.0.1:6000"
OPP_DIR = Path(settings.DATA_DIR) / "agent" / "opportunities"

SETUP_ZH = {
    "oversold_bounce": "超賣反彈",
    "undervalued_catalyst": "低估+催化",
    "momentum_breakout": "動能突破",
    "mimi_entry": "Mimi進場區",
    "breakdown_short": "破位做空",
}


def _backend_alive() -> bool:
    try:
        return requests.get(f"{BACKEND_BASE}/api/health", timeout=4).status_code == 200
    except Exception:
        return False


# ── 市場 regime 一行定性（復用 macro/market endpoint，degrade 成 mixed）──────────

def _market_regime() -> dict:
    try:
        r = requests.get(f"{BACKEND_BASE}/api/market/overview?symbols=SPY,QQQ,IWM,^VIX", timeout=8)
        items = (r.json() or {}).get("items", []) if r.ok else []
        vix = next((it for it in items if str(it.get("symbol")) == "^VIX"), {})
        spy = next((it for it in items if str(it.get("symbol")) == "SPY"), {})
        vix_price = vix.get("price")
        spy_chg = spy.get("change_pct")
        bits = []
        for it in items:
            sym = str(it.get("symbol", ""))
            chg = it.get("change_pct")
            if chg is not None:
                bits.append(f"{sym} {chg:+.2f}%")
        regime = "mixed"
        if vix_price is not None and spy_chg is not None:
            if vix_price < 18 and spy_chg >= 0:
                regime = "risk-on"
            elif vix_price > 24 or spy_chg <= -1:
                regime = "risk-off"
        return {"regime": regime, "snapshot": "｜".join(bits) or "—", "vix": vix_price}
    except Exception:
        return {"regime": "mixed", "snapshot": "—", "vix": None}


# ── 財經 digest（質化脈絡）──────────────────────────────────────────────────────

DIGEST_DAYS = 14
DIGEST_MAX_CHARS = 1500


def _one_line_tldr(tldr: str, limit: int = 120) -> str:
    text = re.sub(r"[*#\-`>]+", " ", tldr or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def load_digest_index() -> tuple[list[dict], dict[str, int]]:
    """回傳 (近 N 天 digest 文章, {ticker: 提及篇數})。讀檔失敗一律 degrade 成空。"""
    try:
        items = load_recent_items(DIGEST_DAYS)
    except Exception:
        return [], {}
    counts: dict[str, int] = {}
    for it in items:
        for t in it["tickers"]:
            counts[t] = counts.get(t, 0) + 1
    return items, counts


def build_digest_section(items: list[dict], tickers: set[str]) -> list[str]:
    """入圍標的相關的 digest 摘要區塊（≤DIGEST_MAX_CHARS）。"""
    header = "## 📰 相關財經 digest（質化脈絡；不可取代技術面/估值判斷）"
    lines = [header]
    related = [it for it in items if tickers.intersection(it["tickers"])] if items else []
    if not related:
        lines.append(f"- （近 {DIGEST_DAYS} 天 digest 未提及任何入圍標的——不代表沒機會，只是沒被寫到）")
        lines.append("")
        return lines

    budget = DIGEST_MAX_CHARS - len(header)

    def _emit(s: str) -> bool:
        nonlocal budget
        if len(s) + 1 > budget:
            return False
        lines.append(s)
        budget -= len(s) + 1
        return True

    _emit("> 想看更多：curl `\"http://127.0.0.1:6000/api/finance-digest/by-ticker?tickers=X,Y&days=21\"`")
    for it in related:
        hits = ", ".join(sorted(tickers.intersection(it["tickers"])))
        if not _emit(f"- [{it['date']}] {it['title']}（{it['sector']}）｜相關入圍: {hits}"
                     f"｜{_one_line_tldr(it['tldr'])}"):
            break
    lines.append("")
    return lines


# ── 簡報組裝（目標 <8k chars）──────────────────────────────────────────────────

def build_briefing(scan_date: date, prescreen_result: dict, regime: dict, backend_ok: bool) -> str:
    now_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M 台北時間")
    opps = prescreen_result.get("opportunities", [])
    lines: list[str] = []

    def L(s: str = "") -> None:
        lines.append(s)

    L(f"# 🔭 市場機會掃描簡報｜{scan_date.isoformat()}")
    L(f"> {now_str}")
    if not backend_ok:
        L("> ⚠️ backend :6000 離線——curl 工具可能不可用，請改用 WebSearch 補技術面/新聞/催化劑。")
    L(f"> Stage-1 量化預篩：universe {prescreen_result.get('universe_size')} 檔 → "
      f"命中 {prescreen_result.get('total_candidates')} 檔 → 本次入圍 {len(opps)} 檔")
    L()

    # 市場 regime
    L("## 📈 大盤 regime（起點，你需 curl macro/market 自行確認）")
    L(f"定性：{regime.get('regime')}｜快照：{regime.get('snapshot')}")
    L()

    # 財經 digest 索引（哪些入圍標的有近期新聞）
    digest_items, digest_counts = load_digest_index()

    # 入圍機會一行式摘要
    L("## 🎯 入圍機會（Stage-1 量化排名；你的任務是逐檔定性盡調）")
    L("> 欄位：# ticker（公司）｜setup｜signal_score｜方向｜關鍵量化訊號｜是否持有")
    L()
    for i, o in enumerate(opps, 1):
        kn = o.get("key_numbers", {})
        setups = "＋".join(SETUP_ZH.get(s, s) for s in o.get("setups", []))
        # 關鍵訊號：取各 bucket 的 evidence 串起（已是精簡一句）
        ev = "｜".join(
            o.get("bucket_detail", {}).get(s, {}).get("evidence", "")
            for s in o.get("setups", [])
        )
        held = ""
        if o.get("in_portfolio"):
            held = f"｜🔵持有({','.join(o.get('held_portfolios') or [])})"
        price = kn.get("price")
        p_str = f"${price}" if price is not None else "—"
        rsi = kn.get("rsi_14")
        rsi_str = f"RSI{rsi:.0f}" if rsi is not None else ""
        earn = kn.get("days_to_earnings")
        earn_str = f"｜財報{earn}天" if earn is not None else ""
        mimi = kn.get("mimi_entry_zone")
        mimi_str = f"｜Mimi進場 {mimi[:24]}" if mimi else ""
        n_news = digest_counts.get(str(o.get("ticker", "")).upper(), 0)
        news_str = f"｜📰 有近期新聞({n_news})" if n_news else ""
        L(f"{i}. {o['ticker']}（{o.get('company') or '—'}）｜{setups}｜"
          f"score {o['signal_score']:.2f}｜{o.get('direction')}｜{p_str} {rsi_str}{earn_str}{mimi_str}{held}{news_str}")
        if ev:
            L(f"   └ 訊號：{ev}")
    L()

    # 近期財經 digest（只列與入圍標的相關的）
    opp_tickers = {str(o.get("ticker", "")).upper() for o in opps if o.get("ticker")}
    for line in build_digest_section(digest_items, opp_tickers):
        L(line)

    # 任務指令
    L("---")
    L("## 🎯 你的任務（機會獵手 opportunity scout）")
    L("以上是 Stage-1 純量化預篩的入圍名單，**不是最終答案**。請依 `agent/CLAUDE.md` 的")
    L("「🔭 市場機會狩獵 SOP」對每個入圍機會做定性盡調：")
    L("1. curl `/api/stocks/{t}/chart?period=6m` 確認技術型態；curl `/api/stocks/{t}/news` 與")
    L("   `/api/earnings/calendar` 找催化劑；curl `/api/stocks/{t}/dcf`、`/pe` 確認估值。")
    L("2. 必要時 WebSearch 查最新消息、財報結果、產業動態、分析師評級變化。")
    L("3. 技術面+基本面+催化劑三者綜合，給明確 action（buy/accumulate/watch/avoid/sell_short）")
    L("   + 進場區/目標/停損/風報比。訊號矛盾或催化劑不明就給 watch，**不要每檔都喊買**。")
    L("4. 標明已在持倉的標的（避免重複建倉建議）。")
    L("5. 標記 📰 的標的：先 curl")
    L("   `\"http://127.0.0.1:6000/api/finance-digest/by-ticker?tickers=X,Y&days=21\"` 讀 TLDR，")
    L("   把新聞/敘事脈絡納入 thesis / catalyst（引用註明日期）。digest 是質化補充，")
    L("   digest 沒提到的標的不代表沒機會。")
    L("完成後**嚴格輸出機會 JSON**（schema 見 agent/CLAUDE.md「輸出契約」），並在 JSON 前附一段")
    L("`<<<BRIEF>>>` 標記的 Telegram 精華文字（top 機會摘要）。")

    return "\n".join(lines)


# ── Claude runner（自主 agent 模式，比照 war_room）──────────────────────────────

def run_scan_claude(briefing: str, model: str, max_turns: int) -> tuple[str, str | None]:
    cmd = [
        "claude", "-p", briefing,
        "--allowed-tools", "Bash(curl http://127.0.0.1:6000/*) WebSearch WebFetch",
        "--max-turns", str(max_turns),
        "--model", model,
        "--output-format", "json",
    ]
    print(f"[claude] 啟動機會獵手（model={model}, max-turns={max_turns}, 簡報 {len(briefing):,} chars）...")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800, cwd=str(AGENT_DIR),
        )
    except subprocess.TimeoutExpired:
        return "機會掃描超時（>30 分鐘），本次略過。", None
    except FileNotFoundError:
        return "找不到 `claude` 命令，請確認 Claude Code CLI 已安裝並在 PATH。", None
    except Exception as e:
        return f"機會掃描執行例外：{e}", None

    if result.returncode != 0:
        print(f"[claude] 非零退出（{result.returncode}）：{(result.stderr or '')[:300]}")
    stdout = (result.stdout or "").strip()
    if not stdout:
        return f"機會獵手無輸出（exit {result.returncode}）：{(result.stderr or '')[:200]}", None
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return stdout, None
    text = payload.get("result")
    session_id = payload.get("session_id")
    if not text:
        err = payload.get("error") or payload.get("subtype") or "unknown"
        return f"機會獵手回應無 result 欄位（{err}），本次略過。", session_id
    return text, session_id


# ── 解析 claude 輸出：機會 JSON + <<<BRIEF>>> 文字 ─────────────────────────────

def extract_opportunity_json(text: str) -> dict | None:
    """從 claude 回應抽出機會 JSON（容忍 code fence 與前後雜訊）。失敗回 None。"""
    if not text:
        return None
    body = text
    m = re.search(r"<<<BRIEF>>>", body)
    if m:
        # BRIEF 之後才是給程式吃的 JSON（BRIEF 本身是 Telegram 文字）
        after = body[m.end():]
    else:
        after = body
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", after, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        start, end = after.find("{"), after.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = after[start:end + 1]
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or "opportunities" not in data:
        return None
    return data


def extract_brief(text: str) -> str:
    """抽 <<<BRIEF>>> 與下一個標記/JSON fence 之間的 Telegram 精華文字。"""
    if not text:
        return ""
    m = re.search(r"<<<BRIEF>>>(.*?)(?=```|<<<[A-Z_]+>>>|\Z)", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text[:600].strip()


def write_opportunity_journal(scan_date: date, record: dict) -> Path | None:
    try:
        OPP_DIR.mkdir(parents=True, exist_ok=True)
        path = OPP_DIR / f"{scan_date.isoformat()}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[journal] Saved: {path}")
        return path
    except Exception as e:
        print(f"[journal] 寫入失敗（略過）：{e}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="市場機會掃描引擎 — Stage-2 agent 定性盡調")
    parser.add_argument("--date", help="掃描日期 YYYY-MM-DD（預設今天）")
    parser.add_argument("--dry-run", action="store_true", help="只印簡報，不跑 claude")
    parser.add_argument("--no-telegram", action="store_true", help="跳過 Telegram / LINE")
    parser.add_argument("--no-obsidian", action="store_true", help="跳過 Obsidian 存檔")
    parser.add_argument("--model", default="sonnet", help="claude 模型（預設 sonnet，可 opus）")
    parser.add_argument("--max-turns", type=int, default=35, help="claude 最大回合數（預設 35）")
    parser.add_argument("--limit", type=int, default=20, help="Stage-1 入圍標的數（預設 20）")
    args = parser.parse_args()

    scan_date = date.fromisoformat(args.date) if args.date else date.today()
    print(f"\n{'='*60}\n  🔭 Market Scan: {scan_date.isoformat()}\n{'='*60}\n")

    backend_ok = _backend_alive()
    if not backend_ok:
        print("[warn] backend :6000 離線——agent 少了 curl 工具，改靠 WebSearch。仍繼續。")

    # Stage-1：量化預篩（直接 import，零 claude 成本）
    print(f"[1/3] Stage-1 量化預篩 top {args.limit}...")
    prescreen_result = prescreen(limit=args.limit, setup="all")
    n_in = len(prescreen_result.get("opportunities", []))
    print(f"      universe {prescreen_result.get('universe_size')} → "
          f"命中 {prescreen_result.get('total_candidates')} → 入圍 {n_in}（evaluated {prescreen_result.get('evaluated')}）")
    if n_in == 0:
        print("[warn] Stage-1 無入圍標的（可能 yfinance 限流）。略過本次。")
        return

    # regime
    regime = _market_regime()

    # Stage-2 簡報
    print("[2/3] 組裝機會簡報...")
    briefing = build_briefing(scan_date, prescreen_result, regime, backend_ok)
    print(f"      簡報：{len(briefing):,} chars（目標 <8000）")

    if args.dry_run:
        print("\n" + "=" * 25 + " 簡報預覽（dry-run）" + "=" * 25)
        print(briefing)
        print("=" * 70)
        print(f"\n總長度：{len(briefing):,} chars（目標 <8000）｜入圍 {n_in} 檔")
        return

    # Stage-2：claude 自主盡調
    print("[3/3] 交給機會獵手自主盡調...")
    analysis, session_id = run_scan_claude(briefing, args.model, args.max_turns)
    print(f"[claude] 回應：{len(analysis):,} chars｜session_id={session_id or '（無）'}")

    opp_json = extract_opportunity_json(analysis)
    generated_at = datetime.now(TAIPEI_TZ).isoformat()
    if opp_json:
        n = len(opp_json.get("opportunities", []))
        print(f"[opportunities] 解析出 {n} 個機會")
        record = {
            "date": scan_date.isoformat(),
            "generated_at": generated_at,
            "session_id": session_id,
            "universe_size": prescreen_result.get("universe_size"),
            "screened": n_in,
            "market_context": opp_json.get("market_context", {"regime": regime.get("regime"), "note": regime.get("snapshot")}),
            "opportunities": opp_json.get("opportunities", []),
        }
    else:
        print("[opportunities] 未解析出有效機會 JSON（仍落地 session_id + 原始文字供追查）")
        record = {
            "date": scan_date.isoformat(),
            "generated_at": generated_at,
            "session_id": session_id,
            "universe_size": prescreen_result.get("universe_size"),
            "screened": n_in,
            "market_context": {"regime": regime.get("regime"), "note": regime.get("snapshot")},
            "opportunities": [],
            "raw": analysis[:4000],
        }
    write_opportunity_journal(scan_date, record)

    # Telegram：發 BRIEF 精華
    if not args.no_telegram:
        brief = extract_brief(analysis)
        telegram_text = f"🔭 市場機會掃描｜{scan_date.isoformat()}\n\n{brief}"
        print("[telegram/line] 發送機會精華...")
        send_telegram(telegram_text)
        send_line(telegram_text)

    # Obsidian（可選，簡單存機會 JSON 摘要）
    if not args.no_obsidian:
        try:
            _save_obsidian(scan_date, record, extract_brief(analysis))
        except Exception as e:
            print(f"[obsidian] 存檔失敗（略過）：{e}")

    print("\n✅ Market Scan 完成！")


def _save_obsidian(scan_date: date, record: dict, brief: str) -> None:
    obs_dir = Path("/Users/yankesswang/Documents/arthurwang_DB/投資/Stock Digest") / scan_date.isoformat()
    obs_dir.mkdir(parents=True, exist_ok=True)
    path = obs_dir / "🔭 Market Scan.md"
    opps = record.get("opportunities", [])
    tickers = [o.get("ticker") for o in opps if o.get("ticker")]
    lines = [
        "---",
        f"title: Market Scan {scan_date.isoformat()}",
        f"date: {scan_date.isoformat()}",
        "tags:",
        "  - 投資",
        "  - 機會掃描",
        "  - claude-analysis",
        "stocks:",
        *[f"  - {t}" for t in tickers],
        "---",
        "",
        f"# 🔭 市場機會掃描 — {scan_date.isoformat()}",
        "",
        f"> Stage-1 universe {record.get('universe_size')} 檔 → 入圍 {record.get('screened')} 檔｜"
        f"regime: {record.get('market_context', {}).get('regime', '—')}",
        "",
        "## 精華",
        "",
        brief,
        "",
        "## 機會清單",
        "",
    ]
    for o in opps:
        ez = o.get("entry_zone")
        ez_str = f"${ez[0]}-{ez[1]}" if isinstance(ez, list) and len(ez) == 2 else "—"
        lines.append(
            f"### {o.get('ticker')}（{o.get('company', '')}）— {SETUP_ZH.get(o.get('setup'), o.get('setup'))}"
        )
        lines.append(
            f"- **{o.get('action', '—')}**｜方向 {o.get('direction', '—')}｜信心 {o.get('conviction', '—')}｜"
            f"score {o.get('signal_score', '—')}｜RR {o.get('rr', '—')}"
        )
        lines.append(f"- 進場 {ez_str}｜目標 {o.get('target', '—')}｜停損 {o.get('stop', '—')}")
        lines.append(f"- 論點：{o.get('thesis', '—')}")
        if o.get("catalyst"):
            lines.append(f"- 催化：{o['catalyst']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[obsidian] Saved: {path}")


if __name__ == "__main__":
    main()
