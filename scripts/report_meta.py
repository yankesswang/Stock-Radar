#!/usr/bin/env python3
"""
報告時效（freshness）與幣別標註 — 讓每份報告自己說清楚「這是哪個交易日的資料、什麼時候產出的」。

問題來源（2026-08-23 檢討）：
  8/21 的 War Room 實際在 8/23 16:44 才產出，內文卻還在寫「今日執行」「明日」。
  讀者無從分辨那是即時指令還是事後補報 —— 這是整份報告最危險的語意錯誤。

本模組提供三件事：
  1. `resolve_trade_date()`：**不猜**交易日，直接從 yfinance 回來的最後一根 K 棒日期認定。
     （報告資料夾用的是台北日期，與美股交易日天然差一天；用實際 bar 日期才不會錯位。）
  2. `build_freshness()`：算出產出延遲並分級 LIVE / BACKFILL / STALE。
     超過收盤 12 小時＝事後補報，報告與 prompt 都必須改用過去式，不得輸出即時買賣動詞。
  3. `format_price()`：台股持倉在 portfolio API 已折算成 USD，畫面上 `$1.00` 到底是美金還是台幣
     完全看不出來 —— 一律標成 `US$1.02（NT$32.5）`。

CLI：
    uv run python scripts/report_meta.py            # 印出目前的時效判定（拿 SPY/QQQ 當基準）
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
ET_TZ = ZoneInfo("America/New_York")

US_CLOSE_ET = dt_time(16, 0)          # 美股正常收盤（不處理半日提早收盤，對時效判定不影響）
LIVE_LAG_HOURS = 12                   # 收盤後幾小時內產出才算「即時」
STALE_TRADE_DAYS = 4                  # 資料本身落後超過幾個日曆天＝資料過期，不只是補報

STATUS_LIVE = "LIVE"
STATUS_BACKFILL = "BACKFILL"
STATUS_STALE = "STALE"
STATUS_UNKNOWN = "UNKNOWN"

_STATUS_ZH = {
    STATUS_LIVE: "即時（收盤後產出）",
    STATUS_BACKFILL: "事後補報",
    STATUS_STALE: "資料過期",
    STATUS_UNKNOWN: "時效不明",
}

# 非 LIVE 時禁止出現的即時動詞（report_guard 會拿這份清單掃描產出）
REALTIME_VERBS = [
    "今日執行", "今天執行", "立即執行", "盤中執行", "現在買", "現在賣",
    "今日買進", "今日賣出", "今日加倉", "今日減碼", "今日停損", "今日 trim",
    "明日操作", "明天操作", "明日開盤", "明天開盤",
]


# ── 交易日認定 ────────────────────────────────────────────────────────────────

def resolve_trade_date(quotes: dict) -> date | None:
    """從報價的 `as_of`（最後一根日 K 的日期）認定這份報告涵蓋的美股交易日。

    以 SPY / QQQ / SPX 這種一定有資料的大盤標的優先；都沒有就取全體出現次數最多的日期。
    抓不到（yfinance 整批失敗）回 None —— 呼叫端要據此標 UNKNOWN，而不是拿台北日期硬充。
    """
    if not quotes:
        return None
    for anchor in ("SPY", "QQQ", "^GSPC", "DIA", "IWM"):
        raw = (quotes.get(anchor) or {}).get("as_of")
        d = _parse_date(raw)
        if d:
            return d
    counts: dict[date, int] = {}
    for payload in quotes.values():
        d = _parse_date((payload or {}).get("as_of"))
        if d:
            counts[d] = counts.get(d, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _parse_date(raw) -> date | None:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def estimate_trade_date(now: datetime | None = None) -> date:
    """報價整批失敗時的最後手段：用美東時間往回推最近一個已收盤的平日。

    ⚠️ 這是**推估**（不查美股假日表），呼叫端必須把 `trade_date_source="estimated"`
    傳給 build_freshness，讓報告標明「推估」——比整份 UNKNOWN 什麼都不敢說有用，
    但不能假裝跟報價認定的交易日一樣可信。
    """
    now = (now or datetime.now(TAIPEI_TZ)).astimezone(ET_TZ)
    d = now.date()
    if now.time() < US_CLOSE_ET:
        d -= timedelta(days=1)
    while d.weekday() >= 5:          # 週六(5)/週日(6) 往前退到週五
        d -= timedelta(days=1)
    return d


def us_close_taipei(trade_date: date) -> datetime:
    """該交易日美股收盤（16:00 ET）對應的台北時間。"""
    close_et = datetime.combine(trade_date, US_CLOSE_ET, tzinfo=ET_TZ)
    return close_et.astimezone(TAIPEI_TZ)


# ── 時效判定 ──────────────────────────────────────────────────────────────────

def build_freshness(
    *,
    report_key: date,
    trade_date: date | None,
    produced_at: datetime | None = None,
    kind: str = "盤後復盤",
    trade_date_source: str = "quotes",
) -> dict:
    """回傳一份可序列化的時效描述。任何欄位取不到都 degrade，不 raise。

    report_key  報告的檔案／資料夾鍵（沿用既有台北日期慣例，不改動歷史路徑）
    trade_date  實際涵蓋的美股交易日（來自 resolve_trade_date）
    """
    now = produced_at or datetime.now(TAIPEI_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI_TZ)

    meta: dict = {
        "kind": kind,
        "report_key": report_key.isoformat(),
        "trade_date": trade_date.isoformat() if trade_date else None,
        "trade_date_source": trade_date_source if trade_date else None,
        "produced_at": now.isoformat(),
        "produced_at_text": now.strftime("%Y-%m-%d %H:%M 台北"),
        "data_as_of_et": None,
        "data_as_of_taipei": None,
        "lag_hours": None,
        "status": STATUS_UNKNOWN,
        "status_zh": _STATUS_ZH[STATUS_UNKNOWN],
        "realtime_ok": False,
        "warning": None,
    }

    if trade_date is None:
        meta["warning"] = "取不到任何報價的資料日期（yfinance 可能整批失敗），本份報告的時效無法驗證。"
        return meta

    close_taipei = us_close_taipei(trade_date)
    est = "　⚠️ 推估（報價來源未回應，未查假日表）" if trade_date_source == "estimated" else ""
    meta["data_as_of_et"] = f"{trade_date.isoformat()} 16:00 ET（收盤）{est}"
    meta["data_as_of_taipei"] = close_taipei.strftime("%Y-%m-%d %H:%M 台北")
    lag = (now - close_taipei).total_seconds() / 3600.0
    meta["lag_hours"] = round(lag, 1)

    stale_by_age = (now.date() - trade_date).days > STALE_TRADE_DAYS
    if stale_by_age:
        status = STATUS_STALE
    elif lag > LIVE_LAG_HOURS:
        status = STATUS_BACKFILL
    else:
        status = STATUS_LIVE

    meta["status"] = status
    meta["status_zh"] = _STATUS_ZH[status]
    meta["realtime_ok"] = status == STATUS_LIVE
    if status == STATUS_BACKFILL:
        meta["warning"] = (
            f"距 {trade_date.isoformat()} 收盤已 {lag:.1f} 小時（>{LIVE_LAG_HOURS}h）＝事後補報，"
            "不得作為即時交易指令；文中一律用「該交易日／次一交易日」，不得寫「今日執行」「明日」。"
        )
    elif status == STATUS_STALE:
        meta["warning"] = (
            f"最新可用資料停在 {trade_date.isoformat()}（已 {(now.date() - trade_date).days} 天），"
            "報價來源可能中斷。所有價位數字都要當成過期資料看待，不得據此下單。"
        )
    return meta


def render_freshness_block(meta: dict) -> str:
    """報告最上方那塊固定的時效標頭（人看的）。"""
    icon = {"LIVE": "🟢", "BACKFILL": "🟠", "STALE": "🔴"}.get(meta.get("status"), "⚪")
    lines = [
        "```text",
        f"交易日　：{meta.get('trade_date') or '未知'}"
        + ("（推估）" if meta.get("trade_date_source") == "estimated" else ""),
        f"資料截至：{meta.get('data_as_of_et') or '未知'}"
        + (f"　（{meta['data_as_of_taipei']}）" if meta.get("data_as_of_taipei") else ""),
        f"產出時間：{meta.get('produced_at_text') or '未知'}",
        f"狀　　態：{meta.get('status')}／{meta.get('status_zh')}"
        + (f"（收盤後 {meta['lag_hours']} 小時）" if meta.get("lag_hours") is not None else ""),
        "```",
    ]
    if meta.get("warning"):
        lines.append(f"> {icon} **{meta['warning']}**")
    return "\n".join(lines)


def freshness_one_liner(meta: dict) -> str:
    """Telegram / 摘要用的一行式。LIVE 時回空字串（沒事就不要吵）。"""
    if meta.get("status") == STATUS_LIVE:
        return ""
    if meta.get("status") == STATUS_UNKNOWN:
        return "⚪ 時效不明：取不到報價日期，勿當即時指令"
    icon = "🟠" if meta.get("status") == STATUS_BACKFILL else "🔴"
    return (f"{icon} {meta.get('status_zh')}：涵蓋 {meta.get('trade_date')} 交易日，"
            f"{meta.get('produced_at_text')} 才產出 —— 非即時指令")


def briefing_tense_instruction(meta: dict) -> list[str]:
    """塞進 claude 簡報的時態指令（BACKFILL/STALE 時才有內容）。"""
    if meta.get("status") in (STATUS_LIVE, None):
        return []
    td = meta.get("trade_date") or "該交易日"
    lines = [
        "## ⏰ 時效警告（最高優先，違反即作廢）",
        f"- 本份報告涵蓋的交易日是 **{td}**，但現在已是 {meta.get('produced_at_text')}"
        f"（延遲 {meta.get('lag_hours')} 小時）→ 這是**事後補報**。",
        f"- **禁止**使用「今日執行」「明日操作」「立即」「現在買/賣」這類即時動詞。",
        f"- 一律改寫成：「{td} 當日應…」「次一交易日（若條件仍成立）…」，並在建議後標「需以最新報價重新驗證」。",
        "- 停損／進場價位照給（那是條件，不是即時指令），但要明講「此為 {td} 收盤基準」。".replace("{td}", td),
        "",
    ]
    return lines


# ── 幣別標註 ──────────────────────────────────────────────────────────────────

def is_twd_ticker(ticker: str) -> bool:
    return str(ticker or "").upper().endswith(".TW")


def format_price(ticker: str, price: float | None, usd_twd_rate: float | None = None,
                 *, converted: bool = True) -> str:
    """`$1.00` 到底是美金還是台幣的歧義修正。

    portfolio API 正常情況會把台股價格折算成 USD，畫面只印 `$1.02` 讀者會以為是美股跳水，
    這裡一律標成 `US$1.02（NT$32.5）`；美股則標 `US$243.32`。

    ⚠️ `converted=False`（匯率抓不到、台股維持原幣）時**絕不標 US$** —— 那會讓
    `NT$104` 被當成 `US$104`，權重瞬間放大三十幾倍還沒人看得出來。
    """
    if price is None:
        return "—"
    if is_twd_ticker(ticker):
        if not converted:
            return f"NT${price:,.2f} ⚠️未折算USD"
        if usd_twd_rate:
            return f"US${price:,.2f}（NT${price * usd_twd_rate:,.2f}）"
    return f"US${price:,.2f}"


def format_amount(value_usd: float | None, usd_twd_rate: float | None = None,
                  *, show_twd: bool = False) -> str:
    if value_usd is None:
        return "—"
    if show_twd and usd_twd_rate:
        return f"US${value_usd:,.0f}（NT${value_usd * usd_twd_rate:,.0f}）"
    return f"US${value_usd:,.0f}"


def main() -> None:
    from scripts.daily_analysis import fetch_quotes

    quotes = fetch_quotes(["SPY", "QQQ"])
    td = resolve_trade_date(quotes)
    meta = build_freshness(report_key=date.today(), trade_date=td)
    print(render_freshness_block(meta))
    print()
    print("one-liner:", freshness_one_liner(meta) or "（LIVE，無警告）")


if __name__ == "__main__":
    main()
