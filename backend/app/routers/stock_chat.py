"""個股問答 —— 在個股頁直接問技術面 / 財報 / 估值，答案踩在 stock-radar 自己的資料上。

跟 ai_analysis 的「決策卡」不同：那張卡是固定格式、每日一張；這裡是自由問答，
問什麼就餵什麼資料。核心是一張**資料區塊路由表**（SECTIONS）：問題裡出現
「毛利率」就去撈三大財報，出現「支撐」就去撈技術面與 S/R，沒點到的區塊不撈
也不進 prompt —— 個股頁十幾個資料源全塞給模型，既慢又貴，還會稀釋重點。

續問走 `claude --resume`：第一問把資料包送進去，之後只送新問題（外加這輪才點到
的新區塊）。同一條對話的脈絡由 claude 自己保存，我們不重送。

所有問答（不分 ticker）寫進 chat_messages 一張表，`/qa` 頁面直接查它 ——
「統一紀錄問過的問題」不需要另一份儲存。

範圍（scope）有三種，同一套管線都吃：
  單檔    ticker=NVDA           → 逐檔區塊（技術面/財報/…）＋需要時的大盤背景
  跨標的  問句點名多個代號       → 每檔各撈一份，prompt 裡照 ticker 分段
  大盤    ticker 留空            → 指數/總經/板塊/市場新聞/我的組合曝險

Endpoints（ticker 一律是可選參數，不在路徑上）:
  GET    /api/stock-chat/stream?q=&ticker=      — SSE 串流問答（前端用）
  POST   /api/stock-chat/ask                    — 同上但一次回完（腳本 / curl 用）
  GET    /api/stock-chat/suggestions?ticker=    — 建議問題
  GET    /api/stock-chat/sessions?ticker=       — 這個範圍的歷史對話
  GET    /api/stock-chat/sessions/{sid}         — 單一對話的全部訊息
  DELETE /api/stock-chat/sessions/{sid}         — 刪一整條對話
  GET    /api/stock-chat/log                    — 統一問答紀錄（依對話收合）
  GET    /api/stock-chat/log/facets             — 紀錄頁的側欄統計
  PATCH  /api/stock-chat/messages/{mid}         — 標星 / 加註記
"""
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterator
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..models import ChatMessage, ChatSession, Stock

_LIB_DIR = settings.PROJECT_ROOT.parent / "scripts" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))
import claude_guard as _guard  # noqa: E402

router = APIRouter()


def _ensure_schema() -> None:
    """補上舊 DB 缺的欄位。

    create_all 只建新表、不會改既有表，所以「跨標的問答」加的 chat_messages.tickers
    在已經聊過天的機器上不會自己長出來。這裡開機時補一次（SQLite ADD COLUMN 很便宜）。
    """
    from sqlalchemy import inspect, text

    from ..database import engine

    try:
        cols = {c["name"] for c in inspect(engine).get_columns("chat_messages")}
    except Exception:
        return                       # 表還沒建 —— create_all 等一下就會用新結構建好
    if "tickers" not in cols:
        with engine.begin() as con:
            con.execute(text("ALTER TABLE chat_messages ADD COLUMN tickers VARCHAR DEFAULT ''"))


_ensure_schema()

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
_TICKER_RE = re.compile(r"[A-Z0-9.\-]{1,12}")

# 一則回答最多等這麼久。claude -p 打 sonnet 的問答通常 5-25 秒，
# 逾時多半代表它跑去用工具而不是卡住。
_TIMEOUT_S = 180
_MODEL = "sonnet"


# ══ 資料區塊 ══════════════════════════════════════════════════════════════════
# 每個 builder 回一段 markdown（沒資料就回 None，prompt 裡會標「無資料」）。
# builder 在 thread pool 裡跑，要 DB 的自己開 session —— Session 不是 thread-safe。

def _fmt(v, unit: str = "", nd: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, str):
        return v
    a = abs(v)
    if unit == "money":
        for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if a >= div:
                return f"{v / div:.2f}{suf}"
        return f"{v:.2f}"
    return f"{v:.{nd}f}"


def _last_two_closes(symbol: str) -> tuple[float, float | None] | None:
    """最新收盤與前一收盤。

    Yahoo chart API 優先、yfinance 當後備：yfinance 的 `.info` / `download` 這陣子
    動不動回 YFRateLimitError，撞到時它內部還會重試（實測一輪指數報價要 11 秒）。
    chart API 這條路穩定且快一個量級，只拿最後兩根收盤也用不到 yfinance 的加值。
    """
    from .chart import _fetch_yahoo_chart_df
    import yfinance as yf

    for fetch in (lambda: _fetch_yahoo_chart_df(symbol, "5d", "1d"),
                  lambda: yf.Ticker(symbol).history(period="5d", auto_adjust=True)):
        try:
            df = fetch()
        except Exception:
            continue
        if df.empty:
            continue
        closes = df["Close"].dropna().astype(float)
        if len(closes) >= 1:
            return float(closes.iloc[-1]), (float(closes.iloc[-2]) if len(closes) >= 2 else None)
    return None


def _sec_quote(ticker: str) -> str | None:
    labels = {ticker: ticker, "SPY": "S&P 500", "QQQ": "Nasdaq 100", "^VIX": "VIX"}
    with ThreadPoolExecutor(max_workers=4) as ex:
        quotes = dict(zip(labels, ex.map(_last_two_closes, labels)))

    L = ["## 最新報價與大盤"]
    for sym, label in labels.items():
        q = quotes.get(sym)
        if not q:
            L.append(f"- {label}: 取不到報價")
            continue
        last, prev = q
        chg = f"（{(last - prev) / prev * 100:+.2f}%）" if prev else ""
        L.append(f"- {label}: {last:.2f}{chg}")
    return "\n".join(L)


def _sec_technical(ticker: str) -> str | None:
    """技術面：均線/動能/量能/支撐壓力，外加 chart 頁那套 TradingView 式綜合評級。"""
    from .chart import get_chart
    from .portfolio import _fetch_technicals

    t = _fetch_technicals(ticker)
    chart = None
    try:
        chart = get_chart(ticker, period="1y")
    except Exception:
        pass
    if not t and not chart:
        return None

    L = ["## 技術面（近一年日線）"]
    if t:
        r = t.get("returns") or {}
        L.append(f"- 收盤 ${t.get('price')}｜1日 {_fmt(r.get('1d'))}%｜1週 {_fmt(r.get('1w'))}%"
                 f"｜1月 {_fmt(r.get('1m'))}%｜3月 {_fmt(r.get('3m'))}%")
        L.append(f"- RSI-14 {t.get('rsi_14')}｜MA5/20/50 {t.get('ma5')}/{t.get('ma20')}/{t.get('ma50')}"
                 f"｜現價相對 MA20 {_fmt(t.get('price_vs_ma20_pct'))}%")
        if t.get("ma_cross"):
            L.append(f"- 均線交叉：{'黃金交叉' if t['ma_cross'] == 'golden' else '死亡交叉'}")
        if t.get("volume_ratio"):
            L.append(f"- 量能 {t['volume_ratio']}× 20 日均量")
        if t.get("vol_regime"):
            L.append(f"- 波動狀態 {t['vol_regime']}（年化 EWMA {_fmt(t.get('ewma_vol'))}%"
                     f"，一年百分位 {t.get('vol_pct_rank')}）")
        for lvl in t.get("near_support", []):
            L.append(f"- 近支撐 ${lvl['price']}（距 {lvl['dist_pct']}%，強度 {lvl['strength']}）")
        for lvl in t.get("near_resistance", []):
            L.append(f"- 近壓力 ${lvl['price']}（距 {lvl['dist_pct']}%，強度 {lvl['strength']}）")
        for b in t.get("breakouts", []):
            L.append(f"- 昨日突破壓力 ${b['price']}（{b['move_pct']:+.2f}%）")
        for b in t.get("breakdowns", []):
            L.append(f"- 昨日跌破支撐 ${b['price']}（{b['move_pct']:+.2f}%）")

    if chart:
        bars = chart.get("bars") or []
        last = bars[-1] if bars else {}
        keys = [("macd", "MACD"), ("signal", "MACD Signal"), ("hist", "MACD 柱"),
                ("k", "KD-K"), ("d", "KD-D"), ("bb_upper", "布林上"),
                ("bb_mid", "布林中"), ("bb_lower", "布林下"), ("atr", "ATR"),
                ("adx", "ADX"), ("ema20", "EMA20"), ("ema50", "EMA50"), ("ema200", "EMA200")]
        vals = [f"{lab} {_fmt(last.get(k))}" for k, lab in keys if last.get(k) is not None]
        if vals:
            L.append("- 指標最新值：" + "｜".join(vals))
        rating = ((chart.get("analytics") or {}).get("rating") or {})
        if rating.get("overall"):
            o, ma, osc = rating["overall"], rating.get("moving_averages", {}), rating.get("oscillators", {})
            L.append(f"- 綜合評級 {o['label']}（{o['score']}）｜均線 {ma.get('label')}"
                     f"（買{ma.get('buy')}/中{ma.get('neutral')}/賣{ma.get('sell')}）"
                     f"｜擺盪 {osc.get('label')}（買{osc.get('buy')}/中{osc.get('neutral')}/賣{osc.get('sell')}）")
        stats = ((chart.get("analytics") or {}).get("stats") or {})
        pos = [(f"{lab} {stats[k]}") for k, lab in
               (("high_52w", "52週高"), ("low_52w", "52週低"), ("pct_from_high", "距高點%"),
                ("pct_from_low", "距低點%"), ("current_drawdown", "目前回檔%"),
                ("max_drawdown", "最大回檔%")) if stats.get(k) is not None]
        if pos:
            L.append("- 位置：" + "｜".join(pos))
        risk = [(f"{lab} {stats[k]}") for k, lab in
                (("atr", "ATR"), ("atrp", "ATR%"), ("hv20", "20日歷史波動%"), ("rvol", "相對量能"),
                 ("beta", "Beta"), ("alpha", "Alpha%"), ("sharpe", "Sharpe"),
                 ("rs_mansfield", "Mansfield RS"), ("stop_2atr", "2ATR 停損參考"),
                 ("target_3r", "3R 目標參考")) if stats.get(k) is not None]
        if risk:
            L.append("- 風險統計：" + "｜".join(risk))
        fib = ((chart.get("analytics") or {}).get("fib") or {})
        if fib.get("levels"):
            lv = "｜".join(f"{x.get('label')} ${_fmt(x.get('price'))}" for x in fib["levels"][:7])
            L.append(f"- 斐波那契（{fib.get('direction')}，"
                     f"波段 ${fib.get('swing_low')}–${fib.get('swing_high')}）：{lv}")
        vp = ((chart.get("analytics") or {}).get("volume_profile") or {})
        if vp.get("poc"):
            L.append(f"- 成交量分布：POC ${_fmt(vp.get('poc'))}"
                     f"｜價值區 ${_fmt(vp.get('val'))}–${_fmt(vp.get('vah'))}")
    return "\n".join(L)


def _sec_valuation(ticker: str) -> str | None:
    from .ai_analysis import _valuation_context

    db = SessionLocal()
    try:
        company, v = _valuation_context(ticker, db)
    finally:
        db.close()
    if not v:
        return None
    L = [f"## 估值（stock-radar DB，{company}）"]
    if v.get("industry"):
        L.append(f"- 產業分組：{v['industry']}")
    if v.get("pe_median_price") is not None:
        L.append(f"- 5 年中位 PE {v.get('pe_median_5y')} → 合理價 ${v['pe_median_price']}")
    if v.get("next_year_fair_price") is not None:
        L.append(f"- 明年 EPS ${v.get('next_year_eps')} → 合理價 ${v['next_year_fair_price']}")
    if v.get("current_year_eps") is not None:
        L.append(f"- 今年 EPS ${v['current_year_eps']}")
    if v.get("valuation_status"):
        diff = v.get("valuation_diff_pct")
        L.append(f"- 估值狀態：{v['valuation_status']}"
                 + (f"（{diff:+.1f}%）" if diff is not None else ""))
    if v.get("eps_growth_5y") is not None:
        L.append(f"- 5 年 EPS 成長 {v['eps_growth_5y']}%｜營收成長 {v.get('revenue_growth_5y')}%")
    return "\n".join(L)


def _sec_pe_history(ticker: str) -> str | None:
    from .pe import get_pe

    try:
        data = get_pe(ticker)
    except HTTPException:
        return None
    hist = data.get("pe_history") or data.get("history") or []
    L = ["## PE 歷史（fullratio）"]
    for k in ("current_pe", "median_pe", "min_pe", "max_pe", "median_5y", "average_pe"):
        if data.get(k) is not None:
            L.append(f"- {k}: {data[k]}")
    rows = hist[-12:] if isinstance(hist, list) else []
    for r in rows:
        if isinstance(r, dict):
            year = r.get("year") or r.get("date") or r.get("period")
            pe = r.get("pe") or r.get("pe_ratio") or r.get("value")
            if year is not None and pe is not None:
                L.append(f"- {year}: PE {pe}")
    return "\n".join(L) if len(L) > 1 else None


def _sec_dcf(ticker: str) -> str | None:
    from .dcf import get_dcf

    try:
        d = get_dcf(ticker)
    except Exception:
        return None
    L = ["## DCF 估值（stock-radar 自算）"]
    for k, label in (("fair_value", "每股內在價值"), ("current_price", "現價"),
                     ("upside_pct", "上檔空間 %"), ("wacc", "WACC"),
                     ("terminal_growth", "永續成長率"), ("fcf_growth", "FCF 成長假設"),
                     ("scenario", "情境")):
        if d.get(k) is not None:
            L.append(f"- {label}: {d[k]}")
    # 不同版本的 DCF JSON 欄位名不完全一樣，其餘純量欄位一併帶上（避免漏掉關鍵假設）
    for k, v in d.items():
        if k not in {"fair_value", "current_price", "upside_pct", "wacc", "terminal_growth",
                     "fcf_growth", "scenario"} and isinstance(v, (int, float, str)):
            L.append(f"- {k}: {v}")
    return "\n".join(L) if len(L) > 1 else None


_FIN_MAX_ROWS = 22
_FIN_MAX_PERIODS = 6


def _fin_block(ticker: str, statement: str, freq: str) -> list[str]:
    from .financials import statement as fin_statement

    try:
        data = fin_statement(ticker, statement, freq=freq, limit=_FIN_MAX_PERIODS)
    except HTTPException:
        return []
    periods = data.get("periods") or []
    if not periods:
        return []
    rows = [r for r in data["line_items"] if not r.get("is_header")]
    # 小計列（營收、毛利、營業利益、淨利…）本來就是要看的；其餘按申報順序補到上限
    picked = [r for r in rows if r.get("is_total")][:_FIN_MAX_ROWS]
    if len(picked) < _FIN_MAX_ROWS:
        seen = {r["concept"] for r in picked}
        picked += [r for r in rows if r["concept"] not in seen][: _FIN_MAX_ROWS - len(picked)]
        picked.sort(key=lambda r: rows.index(r))
    label = data["label"] + ("（年度）" if freq == "annual" else "（單季）")
    out = [f"### {label}　期間：{' | '.join(periods)}"]
    for r in picked:
        unit = "money" if (r.get("unit") or "").upper() in {"USD", "TWD", "EUR", "JPY"} else ""
        vals = " | ".join(_fmt(r["values"].get(p), unit) for p in periods)
        out.append(f"- {r['label']}（{r.get('unit') or ''}）: {vals}")
    return out


# derived metrics 的 pct 存的是比率（0.71），不是百分數 —— 這裡要 ×100 才不會
# 把「毛利率 71%」印成「0.7%」餵給模型
_METRIC_FMT = {"pct": lambda v: f"{v * 100:.1f}%", "money": lambda v: _fmt(v, "money"),
               "per_share": lambda v: f"{v:.2f}", "shares": lambda v: _fmt(v, "money"),
               "ratio": lambda v: f"{v:.2f}"}


def _metrics_block(ticker: str, freq: str) -> list[str]:
    """derive_metrics.py 算好的基本面指標（毛利率、ROE、FCF…）—— 比原始科目好讀。"""
    from .financials import metrics as fin_metrics

    try:
        d = fin_metrics(ticker, freq=freq, limit=_FIN_MAX_PERIODS)
    except HTTPException:
        return []
    periods = d.get("periods") or []
    if not periods:
        return []
    out = [f"### 基本面指標（{'年度' if freq == 'annual' else '單季'}）"
           f"　期間：{' | '.join(periods)}"]
    for g in d.get("groups") or []:
        out.append(f"**{g['group']}**")
        for item in g["items"]:
            fmt = _METRIC_FMT.get(item.get("format"), lambda v: _fmt(v))
            vals = " | ".join(fmt(item["values"][p]) if item["values"].get(p) is not None else "—"
                              for p in periods)
            out.append(f"- {item['label']}: {vals}")
    return out


def _sec_financials(ticker: str) -> str | None:
    L = ["## 財報（SEC XBRL；數字新到舊，橫向對齊期間）"]
    body: list[str] = []
    body += _metrics_block(ticker, "annual")
    body += _metrics_block(ticker, "quarterly")
    if not body:      # 沒跑過 derive_metrics.py 時退回原始三大表
        for stmt in ("income", "balance", "cashflow"):
            body += _fin_block(ticker, stmt, "annual")
        body += _fin_block(ticker, "income", "quarterly")
    if not body:
        return None
    return "\n".join(L + body)


def _sec_segments(ticker: str) -> str | None:
    from .segments import latest as seg_latest

    try:
        d = seg_latest(ticker, months=3)
    except HTTPException:
        try:
            d = seg_latest(ticker, months=12)
        except HTTPException:
            return None
    segs = d.get("segments") or []
    if not segs:
        return None
    L = [f"## 分部門（{d.get('report_name')}；本期 {d.get('period_end')}"
         f"，去年同期 {d.get('prior_period_end')}，幣別 {d.get('currency')}）"]
    for s in segs:
        bits = [f"- {s['segment']}：營收 {_fmt(s.get('revenue'), 'money')}"]
        if s.get("revenue_yoy_pct") is not None:
            bits.append(f"YoY {s['revenue_yoy_pct']:+.1f}%")
        if s.get("profit") is not None:
            bits.append(f"利潤 {_fmt(s['profit'], 'money')}")
        if s.get("profit_yoy_pct") is not None:
            bits.append(f"利潤 YoY {s['profit_yoy_pct']:+.1f}%")
        L.append("｜".join(bits))
    return "\n".join(L)


def _sec_peers(ticker: str) -> str | None:
    from .compare import compare_with_peers

    db = SessionLocal()
    try:
        d = compare_with_peers(ticker, db)
    except HTTPException:
        return None
    finally:
        db.close()
    L = [f"## 同業比較（{d.get('industry')}，{d.get('peer_count')} 檔）"]
    imp = d.get("implied") or {}
    if imp.get("peer_median_pe"):
        L.append(f"- 同業中位 PE {imp['peer_median_pe']}（n={imp.get('peer_n')}）"
                 f"｜本檔自己 5 年中位 PE {imp.get('own_pe_median_5y')}")
        for k, label in (("current_year", "今年 EPS 推算"), ("next_year", "明年 EPS 推算")):
            b = imp.get(k)
            if b:
                L.append(f"- 用同業中位 PE 的{label}合理價 ${b['fair_price']}"
                         f"（現價偏離 {b.get('diff_pct')}%）")
    for s in d.get("stats") or []:
        if s.get("target") is None or not s.get("n"):
            continue
        unit = "x" if s.get("unit") == "x" else (s.get("unit") or "")
        line = (f"- {s['label']}：本檔 {_fmt(s['target'])}{unit}"
                f"｜同業中位 {_fmt(s.get('median'))}{unit}"
                f"（p25 {_fmt(s.get('p25'))} / p75 {_fmt(s.get('p75'))}）")
        if s.get("rank") and s.get("rank_total"):
            line += f"｜排名 {s['rank']}/{s['rank_total']}"
        L.append(line)
    return "\n".join(L) if len(L) > 1 else None


def _sec_news(ticker: str) -> str | None:
    from .news import _fetch_news

    try:
        news = _fetch_news(f"{ticker.split('.')[0]} stock", limit=12)
    except HTTPException:
        return None
    if not news:
        return None
    L = ["## 近期新聞標題（Google News）"]
    for i, n in enumerate(news, 1):
        meta = " · ".join(x for x in [n.get("source"), (n.get("published_at") or "")[:10]] if x)
        L.append(f"- [新聞{i}] {n['title']}" + (f"（{meta}）" if meta else ""))
    return "\n".join(L)


_NOTE_MAX_CHARS = 6000


def _sec_earnings_notes(ticker: str) -> str | None:
    """最近一季的財報筆記全文（Obsidian，我自己寫的）＋前一季的摘要。"""
    from .earnings_notes import get_note, list_notes

    try:
        quarters = (list_notes(ticker) or {}).get("quarters") or []
    except Exception:
        return None
    if not quarters:
        return None
    L = ["## 我的財報筆記（Obsidian 投資/財報筆記；這是我自己寫的，不是外部研報）"]
    budget = _NOTE_MAX_CHARS
    for q in quarters[:2]:
        try:
            note = get_note(ticker, q["quarter"])
        except HTTPException:
            continue
        md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", note.get("markdown") or "")  # 圖片對模型無意義
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
        if len(md) > budget:
            md = md[:budget] + "\n…（後略）"
        budget -= len(md)
        L.append(f"### {q['quarter']}（報告日 {q.get('report_date') or '—'}）\n{md}")
        if budget <= 0:
            break
    return "\n".join(L)


def _sec_my_notes(ticker: str) -> str | None:
    """Obsidian 投資筆記裡跟這檔有關的論點（vault_notes 索引）。"""
    root = str(settings.PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from src.analysis import vault_notes
    except Exception:
        return None
    try:
        hits = vault_notes.notes_for_tickers([ticker], days=180, per_ticker=3)
    except Exception:
        return None
    notes = hits.get(ticker.upper()) or []
    if not notes:
        return None
    L = ["## 我的投資筆記論點（Obsidian，近 180 天）"]
    for n in notes:
        bits = [f"- [{n['date']}] {n['title']}"]
        if n.get("action"):
            bits.append(f"當時動作：{n['action']}")
        if n.get("stop_loss"):
            bits.append(f"停損 ${n['stop_loss']}")
        L.append("｜".join(bits) + f"\n  └ 論點：{n.get('thesis', '')}")
    return "\n".join(L)


def _sec_position(ticker: str) -> str | None:
    from .earnings_notes import _positions

    try:
        pos = _positions().get(ticker.upper())
    except Exception:
        return None
    if not pos:
        return None
    return ("## 我的持倉\n"
            f"- 股數 {pos.get('shares')}｜平均成本 ${pos.get('avg_cost')}"
            f"｜帳戶 {', '.join(pos.get('accounts') or []) or '—'}")




# ══ 大盤 / 總經資料區塊 ═════════════════════════════════════════════════════════
# 這些不吃 ticker：問「今天大盤怎樣」「殖利率倒掛了嗎」「我的部位曝險在哪」時用。

_INDEX_SYMBOLS = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "DIA": "Dow 30",
                  "IWM": "Russell 2000", "^VIX": "VIX"}


def _sec_market_quote() -> str | None:
    with ThreadPoolExecutor(max_workers=len(_INDEX_SYMBOLS)) as ex:
        quotes = dict(zip(_INDEX_SYMBOLS, ex.map(_last_two_closes, _INDEX_SYMBOLS)))
    L = ["## 指數快照"]
    for sym, label in _INDEX_SYMBOLS.items():
        q = quotes.get(sym)
        if not q:
            L.append(f"- {label}: 取不到報價")
            continue
        last, prev = q
        L.append(f"- {label}: {last:.2f}" + (f"（{(last - prev) / prev * 100:+.2f}%）" if prev else ""))
    return "\n".join(L)


def _sec_macro() -> str | None:
    """利率 / 匯率 / 原物料快照 + 殖利率利差（macro 頁那一整排）。"""
    from .macro import get_macro_overview

    try:
        d = get_macro_overview()
    except Exception:
        return None
    items = d.get("items") or []
    if not items:
        return None
    L = ["## 總經指標"]
    for it in items:
        if it.get("price") is None:
            continue
        chg = f"（{it['change_pct']:+.2f}%）" if it.get("change_pct") is not None else ""
        L.append(f"- {it.get('label') or it.get('symbol')}: {it['price']}{chg}")
    yc = d.get("yield_curve") or {}
    if yc.get("spread_10y_3m") is not None:
        L.append(f"- 殖利率利差 10Y-3M: {yc['spread_10y_3m']}"
                 f"（{'倒掛' if yc.get('inverted_10y_3m') else '正斜率'}）"
                 f"｜10Y-5Y: {yc.get('spread_10y_5y')}")
    return "\n".join(L)


def _sec_sector_rs() -> str | None:
    """11 個板塊 ETF 相對 SPY 的強弱排名（RS rating 新到舊）。"""
    from .sectors import get_sector_rs

    try:
        d = get_sector_rs()
    except Exception:
        return None
    items = d.get("items") or []
    if not items:
        return None
    br = d.get("benchmark_returns") or {}
    L = [f"## 板塊相對強弱（基準 {d.get('benchmark')}：1週 {br.get('1w')}% / 1月 {br.get('1m')}%"
         f" / 3月 {br.get('3m')}% / 6月 {br.get('6m')}%）"]
    for it in items:
        r, rs = it.get("returns") or {}, it.get("rs") or {}
        L.append(f"- {it.get('zh') or it.get('label')}（{it['symbol']}，{it.get('group')}）"
                 f"：RS評分 {it.get('rs_rating')}｜報酬 1週 {r.get('1w')}% / 1月 {r.get('1m')}%"
                 f" / 3月 {r.get('3m')}%｜超額 1月 {rs.get('1m')}% / 3月 {rs.get('3m')}%")
    return "\n".join(L)


def _sec_sector_exposure() -> str | None:
    """我的持股與觀察清單按板塊分組 —— 問「我壓太多在哪」時用。"""
    from .sectors import get_sector_exposure

    try:
        d = get_sector_exposure()
    except Exception:
        return None
    groups = d.get("sectors") or {}
    if not groups:
        return None
    L = ["## 我的板塊曝險（依成本權重）"]
    for etf, g in sorted(groups.items(), key=lambda kv: -(kv[1].get("cost_weight_pct") or 0)):
        held = [t["symbol"] for t in g.get("tickers", []) if t.get("held")]
        L.append(f"- {etf}: 成本權重 {g.get('cost_weight_pct')}%｜持有 {g.get('held_count')} 檔"
                 + (f"（{', '.join(held[:8])}）" if held else ""))
    return "\n".join(L)


def _sec_market_news() -> str | None:
    """每日市場新聞（scripts 產的那份，含我自己的持倉視角摘要）。"""
    from .market_news import latest_news

    try:
        d = latest_news()
    except HTTPException:
        return None
    L = [f"## 今日市場新聞（{d.get('date')}，{d.get('count')}/{d.get('total')} 則經篩選）"]
    if d.get("market_summary"):
        L.append(f"- 大盤主線：{d['market_summary']}")
    for i, it in enumerate((d.get("items") or [])[:12], 1):
        title = it.get("title") or ""
        why = it.get("why") or it.get("summary") or ""
        tick = ",".join(it.get("tickers") or []) if isinstance(it.get("tickers"), list) else ""
        L.append(f"- [新聞{i}] {title}" + (f"（{tick}）" if tick else "")
                 + (f" — {why[:120]}" if why else ""))
    return "\n".join(L)


def _sec_my_portfolio() -> str | None:
    """整個持倉組合（不是單一檔）—— 問「我現在整體部位如何」時用。"""
    from .earnings_notes import _positions

    try:
        pos = _positions()
    except Exception:
        return None
    if not pos:
        return None
    rows = sorted(pos.items(), key=lambda kv: -(kv[1].get("shares") or 0) * (kv[1].get("avg_cost") or 0))
    L = [f"## 我的持倉組合（{len(rows)} 檔，成本口徑）"]
    for t, p in rows[:40]:
        cost = (p.get("shares") or 0) * (p.get("avg_cost") or 0)
        L.append(f"- {t}: {p.get('shares')} 股｜均價 ${p.get('avg_cost')}｜成本 {_fmt(cost, 'money')}")
    if len(rows) > 40:
        L.append(f"- （其餘 {len(rows) - 40} 檔略）")
    return "\n".join(L)


def _sec_macro_notes() -> str | None:
    """Obsidian 裡我自己寫的總經 / 大盤筆記。"""
    root = str(settings.PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from src.analysis import vault_notes
        notes = vault_notes.macro_notes(days=45, limit=4)
    except Exception:
        return None
    if not notes:
        return None
    L = ["## 我的總經筆記（Obsidian，近 45 天；我自己寫的）"]
    for n in notes:
        L.append(f"- [{n.get('date')}] {n.get('title')}\n  └ {n.get('thesis', '')}")
    return "\n".join(L)


# ══ 區塊登記表 ═════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SectionDef:
    key: str
    label: str
    keywords: tuple[str, ...]
    build: Callable[..., str | None]   # scope=stock 吃 ticker；scope=market 不吃參數
    topic: str                          # 歸到哪個主題（問答紀錄頁的分類）
    scope: str = "stock"                # stock（逐檔）| market（大盤共用）
    default: bool = False               # 問題沒點到任何區塊時要不要帶


STOCK_SECTIONS: tuple[SectionDef, ...] = (
    SectionDef("quote", "即時報價", (), _sec_quote, "行情", default=True),
    SectionDef(
        "technical", "技術面", (
            "技術", "均線", "ma", "rsi", "macd", "kd", "布林", "支撐", "壓力", "壓力位",
            "突破", "跌破", "量能", "成交量", "型態", "趨勢", "動能", "超買", "超賣",
            "波動", "beta", "回檔", "斐波", "atr", "adx", "訊號", "買點", "賣點",
            "進場", "出場", "停損", "區間", "背離",
        ), _sec_technical, "技術面", default=True,
    ),
    SectionDef(
        "valuation", "估值", (
            "估值", "合理價", "貴", "便宜", "pe", "本益比", "目標價", "eps", "溢價", "折價",
            "值不值", "多少錢", "評價",
        ), _sec_valuation, "估值", default=True,
    ),
    SectionDef("pe_history", "PE 歷史", ("pe", "本益比", "歷史區間", "估值區間", "評價區間"),
               _sec_pe_history, "估值"),
    SectionDef("dcf", "DCF", ("dcf", "現金流折現", "內在價值", "wacc", "折現"), _sec_dcf, "估值"),
    SectionDef(
        "financials", "三大財報", (
            "財報", "營收", "毛利", "毛利率", "營益", "營業利益", "淨利", "獲利", "利潤率",
            "現金流", "自由現金流", "fcf", "資產", "負債", "存貨", "應收", "股東權益",
            "資本支出", "capex", "折舊", "eps", "roe", "roa", "利息", "稅",
            "損益表", "資產負債表", "現金流量表", "季報", "年報", "yoy", "qoq", "成長",
        ), _sec_financials, "財報",
    ),
    SectionDef("segments", "分部門", ("部門", "分部", "segment", "產品線", "業務線", "地區", "營收結構"),
               _sec_segments, "財報"),
    SectionDef("peers", "同業比較", ("同業", "競爭", "對手", "peer", "產業", "比較", "相對", "誰比較"),
               _sec_peers, "估值"),
    SectionDef("news", "新聞", ("新聞", "消息", "事件", "催化", "利多", "利空", "為什麼跌", "為什麼漲"),
               _sec_news, "新聞"),
    SectionDef("earnings_notes", "財報筆記", ("財報筆記", "筆記", "法說", "逐字稿", "電話會議", "guidance", "指引", "展望"),
               _sec_earnings_notes, "財報"),
    SectionDef("my_notes", "我的論點", ("我的", "論點", "當初", "之前", "看法", "觀點", "我寫"),
               _sec_my_notes, "我的觀點", default=True),
    SectionDef("position", "我的持倉", ("持倉", "部位", "成本", "加碼", "減碼", "賣掉", "抱", "套牢", "獲利了結"),
               _sec_position, "我的觀點", default=True),
)

MARKET_SECTIONS: tuple[SectionDef, ...] = (
    SectionDef("market_quote", "指數快照", ("大盤", "指數", "spy", "qqq", "nasdaq", "標普", "道瓊",
                                            "羅素", "vix", "恐慌", "市場"),
               _sec_market_quote, "大盤", scope="market", default=True),
    SectionDef("macro", "總經指標", ("總經", "經濟", "利率", "殖利率", "公債", "聯準會", "fed", "升息",
                                     "降息", "通膨", "cpi", "匯率", "美元", "油價", "黃金", "曲線",
                                     "倒掛", "景氣", "衰退"),
               _sec_macro, "總經", scope="market", default=True),
    SectionDef("sector_rs", "板塊強弱", ("板塊", "類股", "sector", "輪動", "強勢", "弱勢", "rs",
                                         "科技股", "金融股", "能源", "醫療", "防禦"),
               _sec_sector_rs, "大盤", scope="market"),
    SectionDef("sector_exposure", "我的板塊曝險", ("曝險", "集中", "分散", "壓太多", "配置", "權重", "組合"),
               _sec_sector_exposure, "我的觀點", scope="market"),
    SectionDef("market_news", "市場新聞", ("新聞", "消息", "今天發生", "昨天發生", "事件", "頭條", "為什麼跌", "為什麼漲"),
               _sec_market_news, "新聞", scope="market", default=True),
    SectionDef("my_portfolio", "我的持倉組合", ("我的持倉", "我的部位", "組合", "整體部位", "全部持股",
                                                "資產配置", "曝險", "風險"),
               _sec_my_portfolio, "我的觀點", scope="market"),
    SectionDef("macro_notes", "我的總經筆記", ("我的", "筆記", "觀點", "看法", "當初", "之前"),
               _sec_macro_notes, "我的觀點", scope="market"),
)

SECTIONS: tuple[SectionDef, ...] = STOCK_SECTIONS + MARKET_SECTIONS
_BY_KEY = {s.key: s for s in SECTIONS}

MARKET = "MARKET"          # 沒有個股範圍時的 session scope 標記


# ══ 問題 → 範圍與資料區塊 ═══════════════════════════════════════════════════════

# 看起來像代號、其實不是的常見大寫詞。DB 比對已經擋掉大部分，這些是真的有同名
# 股票、但在問句裡幾乎都不是在講那檔的（ON=ON Semi、IT=Gartner、ALL=Allstate…）。
_NOT_TICKERS = {
    "AI", "IT", "ON", "ALL", "IS", "AT", "BE", "BY", "GO", "SO", "OR", "AN", "AS", "AM",
    "PE", "PS", "PB", "EPS", "ROE", "ROA", "ROI", "DCF", "FCF", "EV", "CEO", "CFO", "IPO",
    "GDP", "CPI", "PPI", "PMI", "FED", "ETF", "USD", "TWD", "YOY", "QOQ", "RSI", "MACD",
    "KD", "MA", "ADX", "ATR", "VIX", "US", "TW", "Q1", "Q2", "Q3", "Q4", "FY", "AND", "THE",
}
_CAND_RE = re.compile(r"\b([A-Za-z]{1,5}(?:\.[A-Za-z]{2})?)\b")


def resolve_tickers(question: str, page_ticker: str | None) -> list[str]:
    """問題裡點名了哪些股票。

    「NVDA 跟 AMD 誰便宜」要能同時撈兩檔的資料，所以代號從問句裡抽，再拿 DB 的
    stocks 表驗證 —— 純靠正則會把「PE」「CEO」當成股票，靠 DB 驗證就只剩真的
    有在追蹤的標的。頁面上那檔永遠排第一（問題沒點名時它就是唯一範圍）。
    """
    cands: list[str] = []
    for raw in _CAND_RE.findall(question):
        t = raw.upper()
        # 小寫寫法（nvda）也認，但太短的小寫詞（is、on）不認，免得誤判
        if t in _NOT_TICKERS or (raw.islower() and len(raw) < 3):
            continue
        if t not in cands:
            cands.append(t)
    known: list[str] = []
    if cands:
        db = SessionLocal()
        try:
            rows = db.query(Stock.ticker).filter(func.upper(Stock.ticker).in_(cands)).all()
            found = {r[0].upper() for r in rows}
            known = [c for c in cands if c in found]
        except Exception:
            known = []
        finally:
            db.close()

    out = [page_ticker.upper()] if page_ticker and page_ticker != MARKET else []
    for t in known:
        if t not in out:
            out.append(t)
    return out[:4]          # 四檔已經是一個很長的 prompt 了


def route_sections(question: str, has_ticker: bool) -> tuple[list[str], list[str]]:
    """問題 → (逐檔要撈的區塊, 大盤要撈的區塊)。

    兩張表各自比對：問個股也可能要大盤背景（「這檔在現在的利率環境下…」），
    問大盤也可能點到個股。都沒命中時，有標的就用個股預設組、沒標的就用大盤預設組。
    """
    q = question.lower()

    def hits(table):
        return [s.key for s in table if s.keywords and any(k in q for k in s.keywords)]

    stock_keys = hits(STOCK_SECTIONS) if has_ticker else []
    market_keys = hits(MARKET_SECTIONS)

    if has_ticker and not stock_keys:
        stock_keys = [s.key for s in STOCK_SECTIONS if s.default]
    if not has_ticker:
        # 大盤範圍的預設組（指數 / 總經 / 市場新聞）一律帶上，不是只在沒命中時才帶：
        # 問「今天大盤怎樣」只回指數漲跌是沒用的，得知道今天發生什麼、利率在哪。
        # 逐檔區塊沒這樣做 —— 那邊每一塊都是一次外部抓取，帶多了就慢。
        defaults = [s.key for s in MARKET_SECTIONS if s.default]
        market_keys = defaults + [k for k in market_keys if k not in defaults]

    if has_ticker:
        stock_keys = ["quote"] + [k for k in stock_keys if k != "quote"]
    else:
        market_keys = ["market_quote"] + [k for k in market_keys if k != "market_quote"]
    return stock_keys, market_keys


def gather(scoped_keys: list[str]) -> dict[str, str]:
    """平行撈區塊。key 是 `TICKER:key`（逐檔）或 `*:key`（大盤），回傳同樣的 key。

    單一區塊掛掉不影響其他 —— 少一段總比整個問答 500 好。
    """
    out: dict[str, str] = {}
    if not scoped_keys:
        return out

    def one(scoped: str) -> tuple[str, str | None]:
        owner, key = scoped.split(":", 1)
        try:
            sec = _BY_KEY[key]
            return scoped, (sec.build() if sec.scope == "market" else sec.build(owner))
        except Exception:
            return scoped, None

    with ThreadPoolExecutor(max_workers=min(8, len(scoped_keys))) as ex:
        for key, text in ex.map(one, scoped_keys):
            if text:
                out[key] = text
    return out


def _key_of(scoped: str) -> str:
    return scoped.split(":", 1)[1]


def _label_of(scoped: str) -> str:
    owner, key = scoped.split(":", 1)
    label = _BY_KEY[key].label if key in _BY_KEY else key
    return label if owner == "*" else f"{owner} {label}"


# ══ Prompt ════════════════════════════════════════════════════════════════════

_RULES = """你是 Arthur 的投資研究助理，在 stock-radar 裡回答問題。範圍可能是單一個股、
幾檔股票的比較，或整個大盤與總經。

規則：
1. 只根據下面提供的資料回答。資料裡沒有的數字**絕對不要編**——沒有就直說「這裡沒有這項資料」，
   並指出該去哪找（例如「三大財報要先跑 scripts/earnings/fetch_financials.py」）。
2. 每個數字都要帶單位與期間（例如「FY2025 毛利率 46.2%」而不是「毛利率 46」）。
3. 先給結論，再給依據。用繁體中文、條列為主，除非我要求否則控制在 400 字內。
4. 比較多檔時要直接給排序或勝負，不要每檔各講一段就結束。
5. 提到我自己的筆記／論點／持倉時要標明那是我的，並且明講現在的數據跟當初的判斷是否一致。
6. 不要重述整段資料，我看得到頁面；只講能回答問題的那部分。
7. 結尾用一行「依據：」列出你用到的資料區塊名稱。
8. 不要使用任何工具或讀取檔案，直接根據提供的資料回答。"""


def _build_prompt(tickers: list[str], question: str, blocks: dict[str, str],
                  missing: list[str], first_turn: bool) -> str:
    now = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M 台北時間")
    scope = "、".join(tickers) if tickers else "大盤 / 總經（不限單一個股）"
    L: list[str] = []
    if first_turn:
        L += [_RULES, "", f"# 這次的範圍：{scope}", f"資料時間：{now}", ""]
    else:
        # 追問時別讓模型去描述「這輪收到了什麼資料」—— 使用者要的是答案，
        # 不是我們送了什麼進去的說明（實測它真的會拿這件事當回答開頭）
        L += [f"（追問，範圍 {scope}，資料時間 {now}。先前給過的資料與規則仍然有效；"
              "下面若有區塊，是這次才補上的。直接回答問題，不要說明你收到哪些資料。）", ""]

    # 逐檔的資料照 ticker 分段，多檔時標題分隔 —— 不然模型會把兩家的數字混在一起
    for t in tickers:
        mine = [k for k in blocks if k.startswith(f"{t}:")]
        if not mine:
            continue
        if len(tickers) > 1:
            L.append(f"# ══ {t} ══")
        for s in SECTIONS:
            k = f"{t}:{s.key}"
            if k in blocks:
                L += [blocks[k], ""]

    market = [k for k in blocks if k.startswith("*:")]
    if market:
        if tickers:
            L.append("# ══ 大盤 / 總經背景 ══")
        for s in SECTIONS:
            k = f"*:{s.key}"
            if k in blocks:
                L += [blocks[k], ""]

    if missing:
        L.append("## 沒有資料的區塊")
        L.append("- " + "、".join(_label_of(k) for k in missing)
                 + "（這幾項目前沒有資料，回答時要說明，不要臆測）")
        L.append("")
    L.append("---")
    L.append(f"我的問題：{question}")
    return "\n".join(L)


# ══ claude 串流 ═══════════════════════════════════════════════════════════════

def _stream_claude(prompt: str, resume_id: str | None = None) -> Iterator[tuple[str, object]]:
    """跑 claude -p，逐字吐出。yield ("delta", str) / ("done", dict) / ("error", str)。

    不走 claude_guard.run（那支是阻塞式、一次回完），但共用它的額度 state：
    第一行 rate_limit_event 一樣 record 進去，撞牆一樣照它的判準中止。
    """
    if until := _guard.blocked_until():
        reset = datetime.fromtimestamp(until, TAIPEI_TZ).strftime("%H:%M")
        yield "error", f"claude 額度用罄，{reset} 才恢復（依全系統共用 state 判定）"
        return

    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--include-partial-messages", "--model", _MODEL,
           "--disallowed-tools", "Bash", "Edit", "Write", "NotebookEdit", "Task"]
    if resume_id:
        cmd += ["--resume", resume_id]

    started = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                                cwd=str(settings.PROJECT_ROOT))
    except FileNotFoundError:
        yield "error", "找不到 claude CLI，請確認已安裝並在 PATH 中"
        return

    session_id, cost, got_text, hit_limit = None, None, False, None
    try:
        for line in proc.stdout or []:
            if time.monotonic() - started > _TIMEOUT_S:
                proc.kill()
                yield "error", f"claude 回應逾時（>{_TIMEOUT_S}s）"
                return
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            kind = obj.get("type")
            if kind == "rate_limit_event":
                info = obj.get("rate_limit_info") or {}
                _guard.record(info)
                if info.get("status") not in {"allowed", "allowed_warning"}:
                    hit_limit = info.get("resetsAt")
                    break
            elif kind == "stream_event":
                ev = obj.get("event") or {}
                if ev.get("type") == "content_block_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        got_text = True
                        yield "delta", delta["text"]
            elif kind == "system" and obj.get("session_id"):
                session_id = obj["session_id"]
            elif kind == "result" or "total_cost_usd" in obj:
                session_id = obj.get("session_id") or session_id
                cost = obj.get("total_cost_usd")
    finally:
        if proc.poll() is None:
            proc.kill()
        stderr = (proc.stderr.read() if proc.stderr else "") or ""
        proc.wait()

    if hit_limit:
        reset = datetime.fromtimestamp(hit_limit, TAIPEI_TZ).strftime("%H:%M") if hit_limit else "稍後"
        yield "error", f"claude 額度用罄，{reset} 恢復"
        return
    if not got_text:
        yield "error", f"claude 沒有回應內容{('：' + stderr[:200]) if stderr.strip() else ''}"
        return
    yield "done", {"claude_session_id": session_id, "cost_usd": cost,
                   "duration_ms": int((time.monotonic() - started) * 1000)}


# ══ 對話 / 訊息的存取 ══════════════════════════════════════════════════════════

def _clean_ticker(ticker: str | None) -> str | None:
    """None / 空字串 = 大盤範圍。其餘要長得像代號。"""
    if not ticker:
        return None
    t = ticker.upper().strip()
    if t == MARKET:
        return None
    if not _TICKER_RE.fullmatch(t):
        raise HTTPException(status_code=400, detail="invalid ticker")
    return t


def _topics_for(scoped_keys: list[str]) -> list[str]:
    seen: list[str] = []
    for sk in scoped_keys:
        key = _key_of(sk)
        if key in {"quote", "market_quote"}:
            continue
        topic = _BY_KEY[key].topic if key in _BY_KEY else None
        if topic and topic not in seen:
            seen.append(topic)
    return seen or ["行情"]


def _msg_out(m: ChatMessage) -> dict:
    return {
        "id": m.id, "session_id": m.session_id, "ticker": m.ticker, "role": m.role,
        "content": m.content, "topics": [t for t in (m.topics or "").split(",") if t],
        "tickers": [t for t in (m.tickers or "").split(",") if t],
        "sections": [_label_of(s) for s in (m.sections or "").split(",") if ":" in s],
        "duration_ms": m.duration_ms, "cost_usd": m.cost_usd,
        "starred": bool(m.starred), "note": m.note or "", "error": m.error or "",
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _session_out(s: ChatSession, preview: str = "", count: int = 0) -> dict:
    return {
        "id": s.id, "ticker": s.ticker, "title": s.title,
        "scope": "market" if s.ticker == MARKET else "stock",
        "message_count": count, "preview": preview,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _prepare(page_ticker: str | None, question: str, session_id: int | None,
             force_sections: list[str] | None) -> dict:
    """決定範圍與區塊 → 撈資料 → 組 prompt → 存使用者那則訊息。"""
    db = SessionLocal()
    try:
        scope_label = page_ticker or MARKET
        sess = None
        if session_id:
            sess = db.query(ChatSession).filter_by(id=session_id).first()
            if sess and sess.ticker != scope_label:
                raise HTTPException(status_code=400, detail="session 屬於另一個範圍")
        if sess is None:
            sess = ChatSession(ticker=scope_label, title=question[:60])
            db.add(sess)
            db.commit()
            db.refresh(sess)

        tickers = resolve_tickers(question, page_ticker)
        forced = [k for k in (force_sections or []) if k in _BY_KEY]
        if forced:
            stock_keys = [k for k in forced if _BY_KEY[k].scope == "stock"]
            market_keys = [k for k in forced if _BY_KEY[k].scope == "market"]
        else:
            stock_keys, market_keys = route_sections(question, bool(tickers))

        want = [f"{t}:{k}" for t in tickers for k in stock_keys] + [f"*:{k}" for k in market_keys]
        already = [k for k in (sess.sent_sections or "").split(",") if k]
        first_turn = not sess.claude_session_id
        # 續問只補這輪新點到的區塊；報價每輪都重撈（價格會動）
        todo = want if first_turn else [
            k for k in want if _key_of(k) in {"quote", "market_quote"} or k not in already]

        blocks = gather(todo)
        missing = [k for k in todo if k not in blocks]
        prompt = _build_prompt(tickers, question, blocks, missing, first_turn)

        user_msg = ChatMessage(session_id=sess.id, ticker=scope_label, role="user",
                               content=question, topics=",".join(_topics_for(want)),
                               tickers=",".join(tickers), sections=",".join(want))
        db.add(user_msg)
        sess.sent_sections = ",".join(sorted(set(already) | set(blocks)))
        sess.updated_at = datetime.utcnow()
        db.commit()
        return {
            "session_pk": sess.id,
            "scope_label": scope_label,
            "tickers": tickers,
            "resume_id": sess.claude_session_id or None,
            "prompt": prompt,
            "keys": want,
            "used": list(blocks),
            "missing": missing,
            "user_message_id": user_msg.id,
        }
    finally:
        db.close()


def _finish(session_pk: int, scope_label: str, tickers: list[str], keys: list[str],
            used: list[str], answer: str, meta: dict, error: str = "") -> int:
    db = SessionLocal()
    try:
        msg = ChatMessage(
            session_id=session_pk, ticker=scope_label, role="assistant", content=answer,
            topics=",".join(_topics_for(keys)), tickers=",".join(tickers),
            sections=",".join(used), duration_ms=meta.get("duration_ms"),
            cost_usd=meta.get("cost_usd"), error=error,
        )
        db.add(msg)
        sess = db.query(ChatSession).filter_by(id=session_pk).first()
        if sess:
            if meta.get("claude_session_id"):
                sess.claude_session_id = meta["claude_session_id"]
            sess.updated_at = datetime.utcnow()
        db.commit()
        return msg.id
    finally:
        db.close()


# ══ Endpoints ═════════════════════════════════════════════════════════════════

@router.get("/log")
def qa_log(
    q: str | None = Query(None, description="全文搜尋（問題與回答）"),
    ticker: str | None = None,
    topic: str | None = None,
    starred: bool = False,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """統一問答紀錄，**以對話（session）為單位**。

    一段對話是一個問題脈絡：第一問加上追問，拆開看會失去意義（追問「那停損呢」
    離開上下文根本讀不懂）。所以這裡一列 = 一段對話，訊息整串帶回，前端收合。

    篩選一律作用在「整段對話」：搜尋字串命中對話裡任何一則、或該股／該主題／
    有標記過的訊息，整段就進來。ticker=MARKET 只看大盤 / 總經那些對話。
    """
    sess_q = db.query(ChatSession)
    if ticker:
        t = ticker.upper().strip()
        if t == MARKET:
            sess_q = sess_q.filter(ChatSession.ticker == MARKET)
        else:
            # 在大盤對話裡點名過這檔的也算 —— 「NVDA 跟 AMD 誰便宜」開在大盤範圍時
            # session.ticker 是 MARKET，但訊息的 tickers 有它
            named = {r[0] for r in db.query(ChatMessage.session_id)
                     .filter(ChatMessage.tickers.like(f"%{_clean_ticker(t)}%")).all()}
            sess_q = sess_q.filter(or_(ChatSession.ticker == t,
                                       ChatSession.id.in_(named or {-1})))

    def _sessions_where(*conds):
        return {r[0] for r in db.query(ChatMessage.session_id).filter(*conds).all()}

    if q:
        sess_q = sess_q.filter(ChatSession.id.in_(
            _sessions_where(ChatMessage.content.like(f"%{q}%")) or {-1}))
    if topic:
        sess_q = sess_q.filter(ChatSession.id.in_(
            _sessions_where(ChatMessage.topics.like(f"%{topic}%")) or {-1}))
    if starred:
        sess_q = sess_q.filter(ChatSession.id.in_(
            _sessions_where(ChatMessage.starred == 1) or {-1}))

    total = sess_q.count()
    sessions = (sess_q.order_by(desc(ChatSession.updated_at))
                .offset(offset).limit(limit).all())

    by_session: dict[int, list[ChatMessage]] = {s.id: [] for s in sessions}
    if by_session:
        for m in (db.query(ChatMessage)
                  .filter(ChatMessage.session_id.in_(list(by_session)))
                  .order_by(ChatMessage.created_at, ChatMessage.id).all()):
            by_session[m.session_id].append(m)

    items = []
    for s in sessions:
        msgs = by_session.get(s.id, [])
        topics: list[str] = []
        tickers: list[str] = []
        for m in msgs:
            for t in (m.topics or "").split(","):
                if t and t not in topics:
                    topics.append(t)
            for t in (m.tickers or "").split(","):
                if t and t not in tickers:
                    tickers.append(t)
        first_q = next((m for m in msgs if m.role == "user"), None)
        items.append({
            **_session_out(s, count=len(msgs)),
            "title": (first_q.content if first_q else s.title) or "（空對話）",
            "question_count": sum(1 for m in msgs if m.role == "user"),
            "topics": topics,
            "tickers": tickers,
            "starred": any(m.starred for m in msgs),
            "has_error": any(m.error for m in msgs),
            "messages": [_msg_out(m) for m in msgs],
        })
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/log/facets")
def qa_facets(db: Session = Depends(get_db)) -> dict:
    """紀錄頁側欄：每個範圍問過幾次、每個主題幾次、標星幾則。

    範圍計數用訊息實際點名的 tickers（不是 session 的 scope）—— 在大盤對話裡問過
    NVDA，NVDA 的計數就該加一。
    """
    scopes: dict[str, int] = {}
    topics: dict[str, int] = {}
    for raw_t, raw_topic, scope in db.query(
            ChatMessage.tickers, ChatMessage.topics, ChatMessage.ticker
    ).filter(ChatMessage.role == "user").all():
        names = [t for t in (raw_t or "").split(",") if t] or [scope or MARKET]
        for n in names:
            scopes[n] = scopes.get(n, 0) + 1
        for t in (raw_topic or "").split(","):
            if t:
                topics[t] = topics.get(t, 0) + 1
    return {
        "by_ticker": sorted(({"ticker": k, "count": v} for k, v in scopes.items()),
                            key=lambda x: -x["count"]),
        "by_topic": sorted(({"topic": k, "count": v} for k, v in topics.items()),
                           key=lambda x: -x["count"]),
        "total_questions": db.query(ChatMessage).filter(ChatMessage.role == "user").count(),
        "total_sessions": db.query(ChatSession).count(),
        "starred": db.query(ChatMessage).filter(ChatMessage.starred == 1).count(),
    }


@router.patch("/messages/{mid}")
def update_message(mid: int, body: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    m = db.query(ChatMessage).filter_by(id=mid).first()
    if not m:
        raise HTTPException(status_code=404, detail="message not found")
    if "starred" in body:
        m.starred = 1 if body["starred"] else 0
    if "note" in body:
        m.note = str(body["note"])[:2000]
    db.commit()
    return _msg_out(m)


@router.get("/sessions/{sid}")
def get_session(sid: int, db: Session = Depends(get_db)) -> dict:
    sess = db.query(ChatSession).filter_by(id=sid).first()
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    msgs = (db.query(ChatMessage).filter_by(session_id=sid)
            .order_by(ChatMessage.created_at, ChatMessage.id).all())
    return {**_session_out(sess, count=len(msgs)), "messages": [_msg_out(m) for m in msgs]}


@router.delete("/sessions/{sid}")
def delete_session(sid: int, db: Session = Depends(get_db)) -> dict:
    db.query(ChatMessage).filter_by(session_id=sid).delete()
    db.query(ChatSession).filter_by(id=sid).delete()
    db.commit()
    return {"deleted": sid}


@router.get("/sessions")
def list_sessions(ticker: str | None = Query(None, description="留空 = 大盤 / 總經那條線"),
                  limit: int = Query(20, ge=1, le=100),
                  db: Session = Depends(get_db)) -> dict:
    scope = _clean_ticker(ticker) or MARKET
    sessions = (db.query(ChatSession).filter_by(ticker=scope)
                .order_by(desc(ChatSession.updated_at)).limit(limit).all())
    counts = dict(db.query(ChatMessage.session_id, func.count(ChatMessage.id))
                  .filter(ChatMessage.ticker == scope).group_by(ChatMessage.session_id).all())
    return {"scope": scope,
            "sessions": [_session_out(s, count=counts.get(s.id, 0)) for s in sessions]}


_STOCK_SUGGESTIONS = [
    ("技術面", ["{T} 現在的技術面位置是偏多還偏空？關鍵價位在哪？",
                "最近的量價結構有沒有異常？",
                "如果要進場，支撐與停損該設在哪？"]),
    ("財報", ["最近一季財報最重要的三個變化是什麼？",
              "毛利率與營益率的趨勢往哪走？原因是什麼？",
              "自由現金流跟淨利的落差在哪？",
              "哪個部門在拉動成長、哪個在拖累？"]),
    ("估值", ["現在的估值相對自己的歷史和同業算貴還是便宜？",
              "要多少 EPS 成長才撐得起現在的價格？"]),
    ("我的觀點", ["我當初的論點現在還成立嗎？哪裡變了？",
                  "以我的成本，現在該加碼、續抱還是減碼？"]),
]

_MARKET_SUGGESTIONS = [
    ("大盤", ["今天大盤在幹嘛？主線是什麼？",
              "現在哪些板塊在強、哪些在弱？輪動往哪走？",
              "VIX 與指數位置合起來看，風險偏高還偏低？"]),
    ("總經", ["殖利率曲線現在什麼形狀？對股市的意涵是什麼？",
              "利率、美元、油價這三個現在指向什麼情境？"]),
    ("跨標的", ["NVDA 跟 AMD 現在誰的估值比較合理？",
                "我持倉裡哪幾檔的技術面最弱？"]),
    ("我的部位", ["我的板塊曝險集中在哪？風險是什麼？",
                  "以現在的大盤環境，我的組合該調整什麼？"]),
]


@router.get("/suggestions")
def suggestions(ticker: str | None = None) -> dict:
    """建議問題。有 ticker 給個股題目，沒有就給大盤 / 總經題目。"""
    t = _clean_ticker(ticker)
    table = _STOCK_SUGGESTIONS if t else _MARKET_SUGGESTIONS
    return {
        "scope": t or MARKET,
        "groups": [{"topic": topic, "items": [q.replace("{T}", t or "") for q in items]}
                   for topic, items in table],
        "sections": [{"key": s.key, "label": s.label, "topic": s.topic, "scope": s.scope}
                     for s in SECTIONS],
    }


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/stream")
def stream_ask(
    q: str = Query(..., min_length=2, max_length=2000, description="問題"),
    ticker: str | None = Query(None, description="所在個股頁；留空 = 大盤 / 總經"),
    session_id: int | None = Query(None, description="續問既有對話"),
    sections: str | None = Query(None, description="逗號分隔，手動指定資料區塊"),
):
    """SSE 問答。事件：meta（範圍與用了哪些資料）→ delta（逐字）→ done / error。"""
    t = _clean_ticker(ticker)
    forced = [s.strip() for s in sections.split(",")] if sections else None
    prep = _prepare(t, q, session_id, forced)

    def gen():
        yield _sse("meta", {
            "session_id": prep["session_pk"],
            "question_id": prep["user_message_id"],
            "scope": prep["scope_label"],
            "tickers": prep["tickers"],
            "sections": [_label_of(k) for k in prep["keys"]],
            "used": [_label_of(k) for k in prep["used"]],
            "missing": [_label_of(k) for k in prep["missing"]],
        })
        chunks: list[str] = []
        meta: dict = {}
        error = ""
        for kind, payload in _stream_claude(prep["prompt"], prep["resume_id"]):
            if kind == "delta":
                chunks.append(payload)
                yield _sse("delta", {"text": payload})
            elif kind == "error":
                error = str(payload)
            elif kind == "done":
                meta = payload
        answer = "".join(chunks)
        mid = _finish(prep["session_pk"], prep["scope_label"], prep["tickers"],
                      prep["keys"], prep["used"], answer, meta, error)
        if error:
            yield _sse("error", {"detail": error, "message_id": mid})
        else:
            yield _sse("done", {"message_id": mid, "duration_ms": meta.get("duration_ms"),
                                "cost_usd": meta.get("cost_usd")})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",   # 別讓反向代理把 SSE 緩衝起來
    })


@router.post("/ask")
def ask(body: dict = Body(...)) -> dict:
    """一次回完的版本（給腳本 / curl；瀏覽器請用 /stream）。"""
    t = _clean_ticker(body.get("ticker"))
    question = (body.get("question") or "").strip()
    if len(question) < 2:
        raise HTTPException(status_code=400, detail="question 太短")
    prep = _prepare(t, question, body.get("session_id"), body.get("sections"))

    chunks, meta, error = [], {}, ""
    for kind, payload in _stream_claude(prep["prompt"], prep["resume_id"]):
        if kind == "delta":
            chunks.append(payload)
        elif kind == "error":
            error = str(payload)
        elif kind == "done":
            meta = payload
    answer = "".join(chunks)
    mid = _finish(prep["session_pk"], prep["scope_label"], prep["tickers"],
                  prep["keys"], prep["used"], answer, meta, error)
    if error:
        raise HTTPException(status_code=502, detail=error)
    return {
        "session_id": prep["session_pk"], "message_id": mid,
        "scope": prep["scope_label"], "tickers": prep["tickers"],
        "question": question, "answer": answer,
        "sections": [_label_of(k) for k in prep["used"]],
        "missing": [_label_of(k) for k in prep["missing"]],
        "duration_ms": meta.get("duration_ms"), "cost_usd": meta.get("cost_usd"),
    }
