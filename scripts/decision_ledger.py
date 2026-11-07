#!/usr/bin/env python3
"""
決策帳本（計分卡 2.0）— 讓盤後復盤有跨日記憶，不再只看「昨天」。

score_yesterday()（daily_analysis.py）只對「上一份 journal」評分，於是：
  - 前天說「跌破 $34 停損」，昨天沒觸發就從計分卡消失，今天真的跌破也沒人追。
  - 沒有任何滾動統計——戰情官的 add/trim 到底準不準，30 天下來完全沒有帳。

本模組讀 data/agent/journal/*.json 的歷史決策，補兩塊：
  1. 未結決策追蹤：近 N 天每檔「最新一筆」有價格觸發條件的決策，逐日檢查
     high/low 是否已觸發（含觸發日期）、未觸發的現價距觸發價多遠、論點已持續幾份日誌。
  2. 決策績效（2026-08-23 重做）：以**實際觸發日**起算 5 個交易日前瞻報酬，主指標是
     相對同主題 ETF／SPY 的**超額報酬**；分「進場類／維持類／防禦類」統計樣本數、勝率、
     平均超額與最大不利走勢（MAE），另做「建議 vs 實際成交」與 confidence calibration。
     樣本數 < MIN_SAMPLE 的分組一律標「樣本不足」，不得據以推導系統性結論。

計算（compute_*，回結構化 dict）與渲染（render_*，回 markdown）分離：
  - war_room.py：compute_ledger() → render_ledger_md() 餵簡報 + save_ledger_json()
    落地 data/agent/ledger/{date}.json，給 /api/war-room/review 前端畫圖用。
  - CLI：uv run python scripts/decision_ledger.py [--date 2026-08-23]
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yfinance as yf  # noqa: E402

from scripts.zh_terms import zh_action  # noqa: E402

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
JOURNAL_DIR = PROJECT_ROOT / "data" / "agent" / "journal"
LEDGER_DIR = PROJECT_ROOT / "data" / "agent" / "ledger"

OPEN_WINDOW_DAYS = 10        # 未結追蹤回看天數
STATS_WINDOW_DAYS = 30       # 戰績統計回看天數
FWD_TRADING_DAYS = 5         # 前瞻報酬窗口（交易日）
MAX_SECTION_CHARS = 2800     # 兩區塊合計硬上限（簡報要控長度）

# 方向性：看多類 action 前瞻漲=call 對；防禦類 action 前瞻跌=call 對（躲掉下跌）
BULLISH_ACTIONS = {"add", "open", "hold", "buy", "accumulate"}
DEFENSIVE_ACTIONS = {"trim", "exit", "stop_loss", "sell_short", "avoid"}


def load_journals(start: date, end: date) -> list[tuple[date, dict]]:
    """[start, end] 區間內的 journal（依日期排序）。壞檔直接跳過。"""
    out: list[tuple[date, dict]] = []
    if not JOURNAL_DIR.exists():
        return out
    for p in sorted(JOURNAL_DIR.glob("*.json")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if not (start <= d <= end):
            continue
        try:
            out.append((d, json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def _decisions_of(journal: dict) -> list[dict]:
    ds = journal.get("decisions")
    return [d for d in ds if isinstance(d, dict) and d.get("ticker")] if isinstance(ds, list) else []


def _fetch_history(tickers: set[str], start: date, end: date) -> dict[str, dict]:
    """{ticker: {"dates": [date,...], "high": [...], "low": [...], "close": [...]}}，抓不到就缺席。

    一次 yf.download 批量抓（war_room 流程前面已對 yfinance 打過大量單檔請求，
    這裡再逐檔 .history() 很容易整批被限流；批量只算一次請求）。
    """
    out: dict[str, dict] = {}
    ordered = sorted(tickers)
    try:
        df = yf.download(
            ordered,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            group_by="ticker",
            progress=False,
            threads=True,
        )
        if df is None or df.empty:
            return out
        for t in ordered:
            try:
                sub = df[t] if len(ordered) > 1 else df
                sub = sub[sub["Close"].notna()]
                if sub.empty:
                    continue
                out[t] = {
                    "dates": [d.date() for d in sub.index],
                    "high": [float(x) for x in sub["High"]],
                    "low": [float(x) for x in sub["Low"]],
                    "close": [float(x) for x in sub["Close"]],
                }
            except Exception:
                continue
    except Exception as e:
        print(f"[ledger] 批量取價失敗（帳本將缺價格）：{e}")
    return out


def _first_idx_on_or_after(dates: list[date], d: date) -> int | None:
    for i, x in enumerate(dates):
        if x >= d:
            return i
    return None


def _trigger_hit(series: dict, from_idx: int, tp: float, direction: str) -> date | None:
    """從 from_idx（含）起找第一個觸發日；未觸發回 None。"""
    for i in range(from_idx, len(series["dates"])):
        if direction == "above" and series["high"][i] >= tp:
            return series["dates"][i]
        if direction == "below" and series["low"][i] <= tp:
            return series["dates"][i]
    return None


# ── 區塊 1：未結決策追蹤（compute → dict）──────────────────────────────────────

def compute_open_decisions(analysis_date: date, histories: dict[str, dict],
                           journals: list[tuple[date, dict]]) -> list[dict]:
    """近 OPEN_WINDOW_DAYS 天、每檔「最新一筆」帶價格觸發的決策的觸發狀態。"""
    latest: dict[str, tuple[date, dict]] = {}
    streak: dict[str, int] = {}
    for jd, journal in journals:
        if jd >= analysis_date:      # 當天的決策今晚才開始追（回溯重產時也不可選到當天）
            continue
        for dec in _decisions_of(journal):
            t = str(dec["ticker"]).upper()
            prev = latest.get(t)
            if prev and prev[1].get("action") == dec.get("action"):
                streak[t] = streak.get(t, 1) + 1
            else:
                streak[t] = 1
            latest[t] = (jd, dec)

    rows: list[dict] = []
    for t, (jd, dec) in sorted(latest.items(), key=lambda kv: kv[1][0]):
        tp = dec.get("trigger_price")
        direction = dec.get("direction")
        if not isinstance(tp, (int, float)) or direction not in ("above", "below"):
            continue
        row = {
            "ticker": t,
            "action": dec.get("action", "—"),
            "trigger_price": float(tp),
            "direction": direction,
            "proposed_date": jd.isoformat(),
            "streak": streak.get(t, 1),
            "thesis": str(dec.get("thesis") or "")[:120],
            "risk_level": dec.get("risk_level"),
            "status": "no_data",
            "hit_date": None,
            "last_close": None,
            "distance_pct": None,
        }
        series = histories.get(t)
        if series:
            idx = _first_idx_on_or_after(series["dates"], jd + timedelta(days=1))
            if idx is None:
                row["status"] = "pending"
            else:
                hit = _trigger_hit(series, idx, float(tp), str(direction))
                last_close = series["close"][-1]
                row["last_close"] = round(last_close, 2)
                if hit:
                    row["status"] = "triggered"
                    row["hit_date"] = hit.isoformat()
                else:
                    row["status"] = "pending"
                    row["distance_pct"] = round((float(tp) - last_close) / last_close * 100, 2)
        rows.append(row)
    return rows


NEAR_TRIGGER_PCT = 8.0     # 距觸發價多少 % 以內才值得在簡報裡逐筆列出


def render_open_decisions(rows: list[dict]) -> list[str]:
    """只逐筆列「該處理的」：已觸發、接近觸發、或不是 hold 的決策。

    30 幾檔持倉每檔一行、其中 25 行都是「hold，距觸發還很遠」，會把真正要看的三行淹掉
    ——那些併成一行計數即可（完整清單在 data/agent/ledger/{date}.json）。
    """
    if not rows:
        return []
    def _worth_listing(r: dict) -> bool:
        if r["status"] in ("triggered", "no_data"):
            return True
        if str(r.get("action") or "").lower() != "hold":
            return True
        d = r.get("distance_pct")
        return d is not None and abs(d) <= NEAR_TRIGGER_PCT

    listed = [r for r in rows if _worth_listing(r)]
    quiet = [r for r in rows if r not in listed]

    lines = [
        f"## 🧾 未結決策追蹤（近 {OPEN_WINDOW_DAYS} 天，每檔最新一筆）",
        "> ✅已觸發＝你之前設的條件已經發生。今天必須逐筆回應：確認執行、或明確說明為何放棄，不可裝沒看到。",
        f"> 只列已觸發／非「維持」／距觸發 ≤{NEAR_TRIGGER_PCT:.0f}% 的決策；其餘平靜的維持併成最後一行。",
        "",
    ]
    for r in listed:
        jd = date.fromisoformat(r["proposed_date"])
        arrow = "漲破" if r["direction"] == "above" else "跌破"
        base = (f"- {r['ticker']}｜{zh_action(r['action'])}｜{arrow} {r['trigger_price']}"
                f"（{jd.month}/{jd.day} 提出，論點已持續 {r['streak']} 份日誌）")
        if r["status"] == "triggered":
            hd = date.fromisoformat(r["hit_date"])
            lines.append(base + f"｜✅ {hd.month}/{hd.day} 已觸發（最新收盤 {r['last_close']}）")
        elif r["status"] == "pending" and r["distance_pct"] is not None:
            lines.append(base + f"｜⏳ 未觸發，現價 {r['last_close']} 距觸發 {r['distance_pct']:+.1f}%")
        elif r["status"] == "pending":
            lines.append(base + "｜⏳ 尚無後續交易日")
        else:
            lines.append(base + "｜⚠️ 無價格資料")
    if quiet:
        lines.append(f"- 另有 {len(quiet)} 檔維持 距觸發價 >{NEAR_TRIGGER_PCT:.0f}%，無新事件："
                     + "、".join(r["ticker"] for r in quiet))
    lines.append("")
    return lines


# ── 區塊 2：決策績效（重做）───────────────────────────────────────────────────
#
# 舊版的問題（2026-08-23 檢討）：
#   1. 從**報告日期**起算前瞻報酬 —— 但決策是條件單，真正生效是在觸發那天。
#   2. hold 有 125 筆、trim 只有 3 筆，全部混在一起算，樣本嚴重偏斜還照樣下結論。
#   3. 「最佳 call +35%」只看絕對漲幅，相對 SPY 其實只有 +0.4%。
#   4. 沒有分「建議」與「實際成交」——沒成交的建議算對算錯都跟帳戶無關。
#   5. 沒有勝率、沒有最大不利走勢（MAE）、沒有信心校準，看不出系統性偏差。
#
# 新版：以**觸發日**起算，主指標是超額報酬（vs SPY 與同主題 ETF），分組統計
# 勝率／平均超額／MAE／樣本數，建議與成交分開，並做 confidence calibration。

MIN_SAMPLE = 5               # 低於此樣本數只報數字、不下結論
CONFIDENCE_BUCKETS = [(0.0, 0.5, "低 <0.5"), (0.5, 0.7, "中 0.5–0.7"),
                      (0.7, 0.85, "高 0.7–0.85"), (0.85, 1.01, "極高 ≥0.85")]

# 決策分組：進場類 / 維持類 / 防禦類 —— 三種的「對」定義不同，混在一起算沒有意義
GROUP_ENTRY = "進場類（建倉／加倉／買進）"
GROUP_HOLD = "維持類（維持不動）"
GROUP_DEFENSIVE = "防禦類（減碼／出場／停損）"
_GROUP_OF = {
    "open": GROUP_ENTRY, "add": GROUP_ENTRY, "buy": GROUP_ENTRY, "accumulate": GROUP_ENTRY,
    "hold": GROUP_HOLD,
    "trim": GROUP_DEFENSIVE, "reduce": GROUP_DEFENSIVE, "exit": GROUP_DEFENSIVE,
    "stop_loss": GROUP_DEFENSIVE, "sell": GROUP_DEFENSIVE, "sell_short": GROUP_DEFENSIVE,
}

# 主題 → 同業 ETF：只跟 SPY 比會把「整條 AI 鏈一起跌」誤判成選股失敗
THEME_BENCH = {
    "AI／半導體鏈": "SMH",
    "大型科技平台": "QQQ",
    "企業軟體／SaaS": "XLK",
    "機器人／無人機／太空": "XLI",
    "能源／電力": "XLE",
    "醫療／防禦": "XLV",
    "金融": "XLF",
    "台股（原幣 TWD）": "EWT",
    "大盤／避險 ETF": "SPY",
}
BENCH_TICKERS = {"SPY"} | set(THEME_BENCH.values())


def _bench_for(ticker: str) -> str:
    try:
        from scripts.portfolio_risk import theme_of
        return THEME_BENCH.get(theme_of(ticker), "SPY")
    except Exception:
        return "SPY"


def _effective_start(dec: dict, jd: date, series: dict) -> tuple[date | None, str]:
    """決策真正生效的日期：有價格觸發條件就用**觸發日**，否則用決策日。"""
    tp = dec.get("trigger_price")
    direction = dec.get("direction")
    if isinstance(tp, (int, float)) and direction in ("above", "below") and series:
        idx = _first_idx_on_or_after(series["dates"], jd + timedelta(days=1))
        if idx is None:
            return None, "pending"
        hit = _trigger_hit(series, idx, float(tp), str(direction))
        if hit is None:
            return None, "never_triggered"
        return hit, "triggered"
    return jd, "proposed"


def _window(series: dict, start: date) -> tuple[int, int] | None:
    idx = _first_idx_on_or_after(series["dates"], start)
    if idx is None:
        return None
    end_idx = min(idx + FWD_TRADING_DAYS, len(series["close"]) - 1)
    if end_idx <= idx:
        return None
    return idx, end_idx


def _ret(series: dict, idx: int, end_idx: int) -> float | None:
    c0, c1 = series["close"][idx], series["close"][end_idx]
    return (c1 - c0) / c0 * 100 if c0 else None


def _mae(series: dict, idx: int, end_idx: int, bullish: bool) -> float | None:
    """最大不利走勢：看多看的是期間最低點，防禦看的是期間最高點（賣飛了多少）。"""
    c0 = series["close"][idx]
    if not c0:
        return None
    if bullish:
        worst = min(series["low"][idx:end_idx + 1])
        return (worst - c0) / c0 * 100
    worst = max(series["high"][idx:end_idx + 1])
    return (worst - c0) / c0 * 100


def _agg(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"count": 0}
    wins = sum(1 for r in rows if (r.get("score") or 0) > 0)
    def avg(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None
    return {
        "count": n,
        "win_rate_pct": round(wins / n * 100, 1),
        "avg_alpha_spy_pct": avg("alpha_spy_pct"),
        "avg_alpha_sector_pct": avg("alpha_sector_pct"),
        "avg_fwd_pct": avg("fwd_pct"),
        "avg_mae_pct": avg("mae_pct"),
        "sufficient": n >= MIN_SAMPLE,
    }


def compute_track_record(histories: dict[str, dict],
                         journals: list[tuple[date, dict]],
                         exec_state: dict | None = None) -> dict | None:
    """以觸發日起算的績效帳；建議 vs 實際成交分開，主指標是超額報酬。"""
    executed_keys: set[tuple[str, str]] = set()
    for entry in ((exec_state or {}).get("decisions") or {}).values():
        if entry.get("state") in ("executed", "reconciled"):
            executed_keys.add((entry.get("proposed_date"), str(entry.get("ticker")).upper()))

    rows: list[dict] = []
    skipped = {"no_price": 0, "never_triggered": 0, "pending": 0}

    for jd, journal in journals:
        for dec in _decisions_of(journal):
            t = str(dec["ticker"]).upper()
            action = str(dec.get("action") or "—").lower()
            group = _GROUP_OF.get(action)
            series = histories.get(t)
            if not series or group is None:
                skipped["no_price"] += 1
                continue
            start, basis = _effective_start(dec, jd, series)
            if start is None:
                skipped[basis if basis in skipped else "pending"] += 1
                continue
            win = _window(series, start)
            if not win:
                skipped["pending"] += 1
                continue
            idx, end_idx = win
            fwd = _ret(series, idx, end_idx)
            if fwd is None:
                continue
            bullish = group in (GROUP_ENTRY, GROUP_HOLD)

            def bench_alpha(bench_ticker: str) -> float | None:
                bs = histories.get(bench_ticker)
                if not bs:
                    return None
                bwin = _window(bs, start)
                if not bwin:
                    return None
                bret = _ret(bs, bwin[0], bwin[1])
                return round(fwd - bret, 2) if bret is not None else None

            alpha_spy = bench_alpha("SPY")
            sector_bench = _bench_for(t)
            alpha_sector = bench_alpha(sector_bench)
            primary_alpha = alpha_sector if alpha_sector is not None else alpha_spy
            score = (primary_alpha if bullish else -primary_alpha) if primary_alpha is not None else None

            rows.append({
                "date": jd.isoformat(), "effective_date": start.isoformat(), "basis": basis,
                "ticker": t, "action": action, "group": group,
                "fwd_pct": round(fwd, 2),
                "alpha_spy_pct": alpha_spy,
                "alpha_sector_pct": alpha_sector,
                "sector_bench": sector_bench,
                "mae_pct": round(_mae(series, idx, end_idx, bullish) or 0, 2),
                "score": score,
                "confidence": dec.get("confidence"),
                "executed": (jd.isoformat(), t) in executed_keys,
            })

    if not rows:
        return None

    by_group = {}
    for g in (GROUP_ENTRY, GROUP_HOLD, GROUP_DEFENSIVE):
        by_group[g] = _agg([r for r in rows if r["group"] == g])

    calibration = []
    for lo, hi, label in CONFIDENCE_BUCKETS:
        bucket = [r for r in rows if isinstance(r.get("confidence"), (int, float))
                  and lo <= float(r["confidence"]) < hi]
        if bucket:
            calibration.append({"bucket": label, **_agg(bucket)})

    scored = [r for r in rows if r["score"] is not None]
    scored.sort(key=lambda r: -r["score"])

    return {
        "window_days": STATS_WINDOW_DAYS,
        "fwd_trading_days": FWD_TRADING_DAYS,
        "min_sample": MIN_SAMPLE,
        "n_total": len(rows),
        "n_triggered_basis": sum(1 for r in rows if r["basis"] == "triggered"),
        "skipped": skipped,
        "by_group": by_group,
        "recommended_vs_executed": {
            "已成交（持倉對帳確認）": _agg([r for r in rows if r["executed"]]),
            "僅建議（未對帳到成交）": _agg([r for r in rows if not r["executed"]]),
        },
        "confidence_calibration": calibration,
        "best_call": scored[0] if scored else None,
        "worst_call": scored[-1] if scored else None,
        "rows": rows,
    }


def _fmt_agg(a: dict) -> str:
    if not a.get("count"):
        return "n=0"
    bits = [f"n={a['count']}", f"勝率 {a['win_rate_pct']:.0f}%"]
    if a.get("avg_alpha_sector_pct") is not None:
        bits.append(f"超額(同業) {a['avg_alpha_sector_pct']:+.2f}%")
    if a.get("avg_alpha_spy_pct") is not None:
        bits.append(f"超額(SPY) {a['avg_alpha_spy_pct']:+.2f}%")
    if a.get("avg_mae_pct") is not None:
        bits.append(f"平均最大不利 {a['avg_mae_pct']:+.2f}%")
    if not a.get("sufficient"):
        bits.append("⚠️ 樣本不足，不可推導系統性結論")
    return "｜".join(bits)


def render_track_record(tr: dict | None) -> list[str]:
    if not tr:
        return []
    lines = [
        f"## 📈 決策績效（近 {tr['window_days']} 天，{tr['fwd_trading_days']} 交易日前瞻）",
        "> 起算點＝**實際觸發日**（無價格條件才用決策日）；主指標＝**相對同主題 ETF 的超額報酬**，"
        "不是絕對漲幅。看多類跑贏＝對，防禦類標的隨後跑輸＝躲對了。",
        f"> 樣本：{tr['n_total']} 筆（其中 {tr['n_triggered_basis']} 筆以觸發日起算）；"
        f"未觸發 {tr['skipped'].get('never_triggered', 0)} 筆、窗口未滿 {tr['skipped'].get('pending', 0)} 筆已排除。",
        "",
    ]
    for group, agg in tr["by_group"].items():
        if agg.get("count"):
            lines.append(f"- **{group}**：{_fmt_agg(agg)}")
    lines.append("")
    rve = tr.get("recommended_vs_executed") or {}
    if any(a.get("count") for a in rve.values()):
        lines.append("**建議 vs 實際成交**（沒成交的建議再準也沒進到帳戶）")
        for label, agg in rve.items():
            if agg.get("count"):
                lines.append(f"- {label}：{_fmt_agg(agg)}")
        lines.append("")
    if tr.get("confidence_calibration"):
        lines.append("**信心校準**（0.8 的決策真的比 0.5 準嗎？）")
        for c in tr["confidence_calibration"]:
            lines.append(f"- 信心 {c['bucket']}：{_fmt_agg(c)}")
        lines.append("")
    def _call_line(icon: str, label: str, c: dict) -> str:
        d0 = date.fromisoformat(c["effective_date"])
        bits = [f"絕對 {c['fwd_pct']:+.1f}%"]
        if c.get("alpha_sector_pct") is not None and c.get("sector_bench") != "SPY":
            bits.append(f"vs {c['sector_bench']} {c['alpha_sector_pct']:+.1f}%")
        if c.get("alpha_spy_pct") is not None:
            bits.append(f"vs SPY {c['alpha_spy_pct']:+.1f}%")
        basis = "觸發日" if c.get("basis") == "triggered" else "決策日"
        return (f"- {icon} {label}：{d0.month}/{d0.day}（{basis}起算）{c['ticker']} {c['action']}"
                f"｜" + "｜".join(bits))

    b, w = tr.get("best_call"), tr.get("worst_call")
    if b:
        lines.append(_call_line("🏆", "最佳判斷（按超額排序，非絕對漲幅）", b))
    if w:
        lines.append(_call_line("💀", "最差判斷", w))
    lines.append("")
    lines.append("> 用這份帳檢視系統性偏差（減碼太早？加倉接刀？高信心是否真的較準？）。"
                 "樣本不足的分組只看數字，不要拿來下結論。")
    lines.append("")
    return lines


# ── 對外主函式 ────────────────────────────────────────────────────────────────

def compute_ledger(analysis_date: date) -> dict | None:
    """完整帳本結構化資料；無 journal 或全面失敗回 None，不可炸掉主流程。"""
    try:
        open_journals = load_journals(analysis_date - timedelta(days=OPEN_WINDOW_DAYS), analysis_date)
        stats_journals = load_journals(analysis_date - timedelta(days=STATS_WINDOW_DAYS), analysis_date)
        if not stats_journals:
            return None
        tickers = {
            str(dec["ticker"]).upper()
            for _, journal in stats_journals
            for dec in _decisions_of(journal)
        }
        if not tickers:
            return None
        earliest = stats_journals[0][0]
        # 同業 ETF 一起抓：只跟 SPY 比會把「整條 AI 鏈一起跌」誤判成選股失敗
        needed = tickers | BENCH_TICKERS
        histories = _fetch_history(needed, earliest, analysis_date)
        if not histories:
            # war_room 前面對 yfinance 的大量單檔請求常把整個 IP 打進限流；
            # 帳本沒價格＝整段白做，值得等一下重試一次。
            import time
            print("[ledger] 取價全空（疑似限流），75 秒後重試一次...")
            time.sleep(75)
            histories = _fetch_history(needed, earliest, analysis_date)

        open_rows = compute_open_decisions(analysis_date, histories, open_journals)

        # 執行狀態機：把價格觸發（本模組算的）與實際成交（持倉對帳）分開
        exec_state = None
        try:
            from scripts.exec_state import sync_states
            exec_state = sync_states(analysis_date, open_rows, stats_journals)
        except Exception as e:
            print(f"[ledger] 執行狀態同步失敗（略過）：{e}")

        return {
            "date": analysis_date.isoformat(),
            "generated_at": datetime.now(TAIPEI_TZ).isoformat(),
            "open_decisions": open_rows,
            "track_record": compute_track_record(histories, stats_journals, exec_state),
            "exec_state": exec_state,
        }
    except Exception as e:
        print(f"[ledger] 決策帳本生成失敗（略過）：{e}")
        return None


def render_ledger_md(data: dict | None) -> str:
    if not data:
        return ""
    parts = render_open_decisions(data.get("open_decisions") or [])
    parts += render_track_record(data.get("track_record"))
    md = "\n".join(parts).rstrip()
    if len(md) > MAX_SECTION_CHARS:
        md = md[:MAX_SECTION_CHARS].rstrip() + "\n…（帳本過長已截斷）"
    return md


def save_ledger_json(data: dict | None) -> Path | None:
    """落地 data/agent/ledger/{date}.json，給 /api/war-room/review 前端畫圖。"""
    if not data:
        return None
    try:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        path = LEDGER_DIR / f"{data['date']}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ledger] Saved: {path}")
        return path
    except Exception as e:
        print(f"[ledger] sidecar 寫入失敗（略過）：{e}")
        return None


def build_ledger_md(analysis_date: date) -> str:
    """一步到位：compute + 落地 sidecar + 渲染 markdown（war_room / CLI 用）。"""
    data = compute_ledger(analysis_date)
    save_ledger_json(data)
    return render_ledger_md(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="決策帳本 — 未結追蹤 + 30 天戰績")
    parser.add_argument("--date", help="分析日期 YYYY-MM-DD（預設今天）")
    args = parser.parse_args()
    d = date.fromisoformat(args.date) if args.date else date.today()
    md = build_ledger_md(d)
    print(md if md else "（無可用 journal 決策，帳本為空）")


if __name__ == "__main__":
    main()
