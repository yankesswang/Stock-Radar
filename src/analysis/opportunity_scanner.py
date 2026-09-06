"""
Stage-1 量化預篩器 — 純 Python，零 claude 成本。

對整個追蹤股 universe（config.STOCK_LIST ∪ Mimi 觀察名單，約 225 檔）逐檔算出
「機會訊號」，分類到 setup buckets，給 signal_score 綜合排名，收斂到 top N。

setup buckets：
  - oversold_bounce      RSI 低 + 觸及/接近強支撐 + 非崩跌
  - undervalued_catalyst PE < 5年中位 或 DCF 折價 ≥15%，且（財報 ≤21 天 或 forecast 上修）
  - momentum_breakout    突破近期壓力 + MA 多頭排列 + 放量
  - mimi_entry           現價進入/接近 Mimi 進場區
  - breakdown_short      跌破關鍵支撐 + 空頭排列 + RSI 轉弱（direction=short）

一檔可命中多個 bucket；signal_score = 各 bucket 子分（0-1）加權綜合。

防禦式：個股任何資料抓不到就跳過該訊號，不中斷整體。並行 + TTL 快取，避免
每次重打 yfinance。所有可復用的計算都直接 import backend router 的內部函式。
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

# ── 復用既有計算（backend router 內部函式）─────────────────────────────────────
from backend.app.config import settings
from backend.app.routers.portfolio import _fetch_technicals_batch, get_csv_portfolio
from backend.app.database import SessionLocal
from backend.app.models import Stock, Industry, Valuation, Forecast
from sqlalchemy import func

# Mimi 解析與進場區判定（daily_analysis 已有，直接復用不重造）
from scripts.daily_analysis import (
    parse_mimi_doc,
    extract_mimi_entry_zone,
    extract_mimi_stop_loss,
    extract_mimi_position_size,
    _is_near_entry_zone,
)

# universe 來源
from src.config import STOCK_LIST
from src.analysis.strength import BENCHMARK, build_strength_board


SETUP_TYPES = (
    "oversold_bounce",
    "undervalued_catalyst",
    "momentum_breakout",
    "mimi_entry",
    "breakdown_short",
    "rs_leader",
)

DCF_DIR = settings.DATA_DIR / "dcf"

# ── TTL 快取（整份預篩結果，10 分鐘）───────────────────────────────────────────
_CACHE: dict[str, dict] = {}
_CACHE_TTL = 600  # 秒


def _round(v, n=2):
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, n)
    except (TypeError, ValueError):
        return None


# ── universe 組裝 ─────────────────────────────────────────────────────────────

VAULT_UNIVERSE_DAYS = 60   # 筆記多舊還算「我現在在關注」


def _clean(tickers) -> set[str]:
    """去掉明顯非個股的殼（純數字或過短雜訊）。"""
    return {t.upper() for t in tickers if re.match(r"^[A-Z][A-Z0-9\.\-]{0,9}$", t.upper())}


def build_curated_universe(mimi_sections: dict[str, str] | None = None) -> set[str]:
    """第一層：追蹤股 ∪ Mimi 名單 ∪ 近期 Obsidian 筆記提到的標的 —— 我研究過的。

    第三份來源是「我自己寫過的東西」：研究過但還沒買、也不在 Mimi 名單裡的標的
    本來完全掃不到。讀的是鏡像索引，讀不到就當沒有。
    """
    tracked: set[str] = set()
    for bucket, tickers in STOCK_LIST.items():
        tracked |= set(tickers)
    mimi = set((mimi_sections or {}).keys())
    try:
        from src.analysis.vault_notes import vault_tickers
        vault = vault_tickers(VAULT_UNIVERSE_DAYS)
    except Exception:
        vault = set()
    return _clean(tracked | mimi | vault)


_DISCOVERY_META: dict[str, dict] | None = None


def _discovery_meta() -> dict[str, dict]:
    """發現層的 {ticker: meta}，同一次掃描只讀一次快取。"""
    global _DISCOVERY_META
    if _DISCOVERY_META is None:
        try:
            from src.analysis import discovery_universe
            _DISCOVERY_META = discovery_universe.load()
        except Exception:
            _DISCOVERY_META = {}
    return _DISCOVERY_META


def _discovery_name(ticker: str) -> str | None:
    """發現層個股不在 DB 裡，公司名只能從快取拿 —— 榜上只有代號沒有名字，
    對「我沒研究過的股票」等於什麼都沒說。"""
    meta = _discovery_meta().get(ticker.upper()) or {}
    name = (meta.get("name") or "").strip()
    # Nasdaq 的 name 帶 "Common Stock" / "Class A Common Stock" 這種尾巴，砍掉才讀得下去
    for suffix in (" Common Stock", " Ordinary Shares", " Class A", " Class B",
                   " American Depositary Shares", " Common Shares"):
        name = name.replace(suffix, "")
    return name.strip(" ,") or None


def build_discovery_universe() -> set[str]:
    """第二層：全市場過三道品質門檻的個股 —— 我沒研究過的。

    這一層才是「不會錯過機會」的關鍵：只掃手寫名單，永遠只會掃到自己已經想到的。
    抓不到就回空集合，主掃描照跑（見 discovery_universe 模組頭的說明）。
    """
    try:
        from src.analysis import discovery_universe
        return _clean(discovery_universe.tickers())
    except Exception as exc:
        print(f"[scanner] 發現層載入失敗（{type(exc).__name__}: {exc}），只掃 curated")
        return set()


def build_universe(mimi_sections: dict[str, str] | None = None,
                   include_discovery: bool = True) -> tuple[list[str], dict[str, str]]:
    """回 (全 universe, {ticker: tier})。tier 是 "curated" 或 "discovery"。

    tier 只影響榜上的標記與排序決勝，訊號計算兩層完全一樣 —— 發現層股票該進榜就
    進榜，只是提醒你那是還沒做過功課的名字。
    """
    curated = build_curated_universe(mimi_sections)
    discovery = build_discovery_universe() - curated if include_discovery else set()
    tiers = {t: "curated" for t in curated}
    tiers.update({t: "discovery" for t in discovery})
    return sorted(curated | discovery), tiers


# ── DB 估值/財報快照（一次查全 universe）───────────────────────────────────────

def _load_db_snapshot(tickers: list[str]) -> dict[str, dict]:
    """一次撈 universe 每檔的最新 Valuation + Forecast。缺資料的 ticker 不在回傳裡。"""
    out: dict[str, dict] = {}
    db = SessionLocal()
    try:
        upper = {t.upper() for t in tickers}

        # 最新 valuation per stock
        subq = (
            db.query(Valuation.stock_id, func.max(Valuation.snapshot_date).label("md"))
            .group_by(Valuation.stock_id)
            .subquery()
        )
        rows = (
            db.query(Stock, Industry, Valuation)
            .join(Industry, Stock.industry_id == Industry.id)
            .join(subq, Stock.id == subq.c.stock_id)
            .join(Valuation, (Valuation.stock_id == Stock.id) & (Valuation.snapshot_date == subq.c.md))
            .all()
        )
        for stock, industry, val in rows:
            t = stock.ticker.upper()
            if t not in upper:
                continue
            out[t] = {
                "company": stock.company_name or t,
                "industry": industry.name,
                "current_price": val.current_price,
                "pe_median_5y": val.pe_median_5y,
                "estimated_pe": val.estimated_pe,
                "pe_median_price": val.pe_median_price,
                "valuation_status": val.valuation_status,
                "valuation_diff_pct": val.valuation_diff_pct,
            }

        # 最新 forecast per stock（拿 eps 成長 → 上修判定的粗略代理）
        fsubq = (
            db.query(Forecast.stock_id, func.max(Forecast.snapshot_date).label("md"))
            .group_by(Forecast.stock_id)
            .subquery()
        )
        frows = (
            db.query(Stock.ticker, Forecast)
            .join(fsubq, Stock.id == fsubq.c.stock_id)
            .join(Forecast, (Forecast.stock_id == Stock.id) & (Forecast.snapshot_date == fsubq.c.md))
            .all()
        )
        for ticker, fc in frows:
            t = ticker.upper()
            if t not in upper:
                continue
            out.setdefault(t, {})
            out[t]["next_year_eps_growth"] = fc.next_year_eps_growth
            out[t]["current_eps_growth"] = fc.current_eps_growth
    finally:
        db.close()
    return out


# ── DCF 折價（只讀既有檔案，不 on-demand 重算，避免 225 檔太慢）─────────────────

def _dcf_discount_pct(ticker: str) -> float | None:
    """讀最新 DCF JSON 的 base(moderate) 情境 margin_of_safety（%，正=折價）。
    無檔案回 None（不觸發 on-demand 重算）。"""
    for pat in (f"{ticker.upper()}_dcf_*.json", f"{ticker.lower()}_dcf_*.json"):
        files = sorted(DCF_DIR.glob(pat), reverse=True)
        if files:
            try:
                d = json.loads(files[0].read_text(encoding="utf-8"))
                sc = d.get("scenarios", {})
                base = sc.get("moderate") or sc.get("base") or {}
                return _round(base.get("margin_of_safety"))
            except (json.JSONDecodeError, OSError, KeyError):
                return None
    return None


# ── 財報日臨近（yfinance calendar，並行）───────────────────────────────────────

def _days_to_earnings(ticker: str) -> int | None:
    """距下次財報天數（未來），抓不到回 None。"""
    try:
        import yfinance as yf

        cal = yf.Ticker(ticker).calendar
        if not cal:
            return None
        if not isinstance(cal, dict):
            cal = dict(cal) if hasattr(cal, "items") else {}
        raw = cal.get("Earnings Date") or cal.get("earningsDate") or []
        if not isinstance(raw, list):
            raw = [raw]
        parsed: list[date] = []
        for d in raw:
            try:
                if isinstance(d, datetime):
                    parsed.append(d.date())
                elif isinstance(d, date):
                    parsed.append(d)
                elif hasattr(d, "date"):
                    parsed.append(d.date())
            except Exception:
                continue
        future = [d for d in parsed if d >= date.today()]
        if not future:
            return None
        return (min(future) - date.today()).days
    except Exception:
        return None


def _earnings_map(tickers: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_days_to_earnings, t): t for t in tickers}
        for f in as_completed(futs):
            t = futs[f]
            try:
                d = f.result()
                if d is not None:
                    out[t.upper()] = d
            except Exception:
                pass
    return out


# ── 持倉標記 ──────────────────────────────────────────────────────────────────

def _held_map() -> dict[str, list[str]]:
    """{TICKER: [portfolio 名稱, ...]}，供標記 in_portfolio。失敗回 {}。"""
    out: dict[str, list[str]] = {}
    db = SessionLocal()
    try:
        data = get_csv_portfolio(name=None, db=db)
        for pname, block in (data.get("portfolios") or {}).items():
            for h in block.get("holdings") or []:
                t = str(h.get("ticker") or "").upper()
                if not t or t == "CASH":
                    continue
                out.setdefault(t, []).append(pname)
    except Exception:
        return {}
    finally:
        db.close()
    return out


# ── 各 bucket 子分（0-1）──────────────────────────────────────────────────────
#
# 每個 scorer 拿 (tech, db, mimi_section, days_to_earn, dcf_disc)，回 (score, evidence)
# 或 None（未命中）。evidence 是一句話關鍵訊號，寫進結果供前端/stage-2。


def _ma_alignment(price, ma5, ma20, ma50) -> str:
    if price and ma5 and ma20 and ma50:
        if price > ma5 > ma20 > ma50:
            return "bull"      # 完美多頭
        if price < ma5 < ma20 < ma50:
            return "bear"      # 空頭排列
        if ma5 > ma20:
            return "short_bull"
        return "short_bear"
    return "unknown"


def _score_oversold_bounce(tech, db, *_):
    rsi = tech.get("rsi_14")
    if rsi is None or rsi > 32:
        return None
    align = _ma_alignment(tech.get("price"), tech.get("ma5"), tech.get("ma20"), tech.get("ma50"))
    # 崩跌型態（完美空頭排列）不算反彈機會 → 交給 breakdown_short
    if align == "bear":
        return None
    near_sup = tech.get("near_support") or []
    # RSI 越低分越高（32→0.4, 20→~0.85, 15→1.0）
    rsi_sub = max(0.0, min(1.0, (32 - rsi) / 17 + 0.4))
    sup_bonus = 0.0
    sup_txt = ""
    if near_sup:
        lvl = near_sup[0]
        sup_bonus = 0.25 + 0.05 * lvl.get("strength", 1)
        sup_txt = f"，觸及支撐 ${lvl['price']}（距{lvl['dist_pct']}%）"
    score = min(1.0, 0.55 * rsi_sub + sup_bonus)
    ev = f"RSI {rsi:.0f} 超賣{sup_txt}"
    return score, ev


def _score_undervalued_catalyst(tech, db, mimi_section, days_to_earn, dcf_disc):
    """低估 + 催化劑。

    估值語意（已用真實 DB 驗證）：valuation_diff_pct **> 0 = 低估**（合理價高於現價），
    < 0 = 高估；valuation_status 字串是權威判定。raw estimated_pe vs 5年中位不可靠
    （pe_median_price 用次年 EPS 成長，NVDA/CMG 會出現 est_pe 遠高於中位卻仍判低估、
    或 est_pe 為負），故 **不**用 raw PE 比較當觸發，只用 status / diff_pct / DCF。
    """
    if not db:
        return None
    val_status = (db.get("valuation_status") or "").lower()
    val_diff = db.get("valuation_diff_pct")   # >0 = 低估

    undervalued = False
    val_bits = []
    disc_mag = 0.0
    # 估值狀態低估（DB 權威判定）+ 折價幅度
    if "under" in val_status and val_diff is not None and val_diff > 0:
        undervalued = True
        val_bits.append(f"低估 {val_diff:.0f}%（合理價 vs 現價）")
        disc_mag = max(disc_mag, min(0.45, val_diff / 80))
    # DCF 折價 ≥15%（moderate 情境 margin_of_safety）
    if dcf_disc is not None and dcf_disc >= 15:
        undervalued = True
        val_bits.append(f"DCF 折價 {dcf_disc:.0f}%")
        disc_mag = max(disc_mag, min(0.45, dcf_disc / 80))

    if not undervalued:
        return None

    # 催化劑：財報 ≤21 天 或 forecast 上修（次年 EPS 成長 ≥10%）
    cat_bits = []
    has_catalyst = False
    cat_bonus = 0.0
    if days_to_earn is not None and days_to_earn <= 21:
        has_catalyst = True
        cat_bits.append(f"財報 {days_to_earn} 天內")
        cat_bonus = max(cat_bonus, 0.3)
    ny_growth = db.get("next_year_eps_growth")
    if ny_growth is not None and ny_growth >= 10:
        has_catalyst = True
        cat_bits.append(f"次年 EPS 上修 +{ny_growth:.0f}%")
        cat_bonus = max(cat_bonus, 0.25)

    if not has_catalyst:
        return None

    # 估值折價（尤其 valuation_diff_pct，來自 pe_median_price，受次年 EPS 成長影響而偏噪）
    # 單一估值訊號不該直接打滿分。base 0.25 + 折價 + 催化，再對「status 與 DCF 同時同向」
    # 給共振加分，讓多重確認的低估標的才接近上限。
    score = 0.25 + disc_mag + cat_bonus
    both_disc = (
        "under" in val_status and val_diff and val_diff > 0
        and dcf_disc is not None and dcf_disc >= 15
    )
    if both_disc:
        score += 0.12   # PE 中位法 + DCF 兩套估值同時看便宜 → 加分
    score = min(0.85, score)   # 純基本面單 bucket 封頂 0.85，留空間給技術面共振
    ev = "；".join(val_bits + cat_bits)

    # 價值陷阱降權：報告裡一再出現「低估訊號被基本面惡化打臉」的那些（NXPI、ENTG、
    # PVH…），共同點是個股與所屬主題雙雙落在相對強度後段。便宜而且沒人要，多半是
    # 市場已經先知道了 —— 不刪掉它，但要排在真正的機會後面，並把理由寫在證據裡。
    rank = tech.get("rs_rank")
    theme_rank = tech.get("theme_rs_rank")
    if rank is not None and rank <= 25:
        theme = tech.get("theme")
        theme_weak = theme_rank is not None and theme_rank <= 40
        score *= 0.6 if theme_weak else 0.75      # 個股弱就打折，連主題也弱再打一次
        ev += f"；⚠️ 相對強弱百分位僅 {rank:.0f}"
        ev += f"、{theme}主題同樣弱勢" if theme_weak else ""
        ev += "（價值陷阱風險）"

    return score, ev


def _score_momentum_breakout(tech, *_):
    align = _ma_alignment(tech.get("price"), tech.get("ma5"), tech.get("ma20"), tech.get("ma50"))
    breakouts = tech.get("breakouts") or []
    vol_ratio = tech.get("volume_ratio")
    # 需：突破近期壓力 + MA 多頭排列 + 放量
    if not breakouts:
        return None
    if align not in ("bull", "short_bull"):
        return None
    if vol_ratio is None or vol_ratio < 1.5:
        return None
    bo = breakouts[0]
    align_sub = 0.5 if align == "bull" else 0.3
    vol_sub = min(0.3, (vol_ratio - 1.5) / 5 + 0.15)
    str_sub = 0.05 * bo.get("strength", 1)
    score = min(1.0, align_sub + vol_sub + str_sub + 0.1)
    ev = (f"突破壓力 ${bo['price']}（+{bo['move_pct']:.1f}%）"
          f"｜{'完美多頭' if align == 'bull' else '短多'}｜量比 {vol_ratio:.1f}×")
    return score, ev


def _score_mimi_entry(tech, db, mimi_section, *_):
    if not mimi_section:
        return None
    price = tech.get("price") or (db or {}).get("current_price")
    if price is None:
        return None
    entry = extract_mimi_entry_zone(mimi_section)
    if entry in ("—", "") or not _is_near_entry_zone(price, entry):
        return None
    # 進入區內比接近區給更高分（近似：現價越靠近區間中心越高，這裡簡化給固定高分）
    score = 0.7
    ev = f"現價 ${price:.2f} 進入/接近 Mimi 進場區「{entry[:40]}」"
    return score, ev


def _score_breakdown_short(tech, *_):
    align = _ma_alignment(tech.get("price"), tech.get("ma5"), tech.get("ma20"), tech.get("ma50"))
    breakdowns = tech.get("breakdowns") or []
    rsi = tech.get("rsi_14")
    if not breakdowns:
        return None
    if align not in ("bear", "short_bear"):
        return None
    # RSI 轉弱（< 45）才算破位做空，避免超賣區追空
    if rsi is None or rsi >= 45 or rsi <= 25:
        return None
    bd = breakdowns[0]
    align_sub = 0.5 if align == "bear" else 0.3
    rsi_sub = min(0.25, (45 - rsi) / 40 + 0.1)
    str_sub = 0.05 * bd.get("strength", 1)
    score = min(1.0, align_sub + rsi_sub + str_sub)
    ev = (f"跌破支撐 ${bd['price']}（{bd['move_pct']:.1f}%）"
          f"｜{'空頭排列' if align == 'bear' else '短空'}｜RSI {rsi:.0f} 轉弱")
    return score, ev


def _score_rs_leader(tech, *_):
    """強勢產業裡的回檔龍頭。

    前五種 setup 都只看個股自己（超賣、低估、突破…），完全不知道它站在哪個產業裡、
    相對大盤是強是弱 —— 掃出來的「低估」很容易是弱勢股的價值陷阱。這一種反過來：
    先要求相對強度（個股 vs SPY 的 1 個月超額報酬排前 20%、所屬主題桶中上），
    再要求它現在是**回檔**而不是追高（貼近 MA20、且沒跌破 MA50）。
    """
    rank = tech.get("rs_rank")
    theme_rank = tech.get("theme_rs_rank")
    if rank is None or rank < 80:
        return None
    if theme_rank is not None and theme_rank < 50:
        return None
    price, ma20, ma50 = tech.get("price"), tech.get("ma20"), tech.get("ma50")
    if not (price and ma20 and ma50) or price < ma50:
        return None            # 跌破季線就不是回檔，是趨勢壞掉
    gap = tech.get("price_vs_ma20_pct")
    if gap is None or gap > 6 or gap < -10:
        return None            # 追高（離月線太遠）或已經摔下來的都不算

    rs_1m = (tech.get("rs") or {}).get("1m")
    # 相對強度越極端、離月線越近，分數越高
    rank_sub = min(1.0, (rank - 80) / 20 * 0.6 + 0.4)
    pull_sub = 1.0 - min(1.0, abs(gap) / 10)
    score = min(1.0, 0.65 * rank_sub + 0.35 * pull_sub)
    theme = tech.get("theme")
    theme_txt = f"，{theme}主題強弱百分位 {theme_rank:.0f}" if theme and theme_rank is not None else ""
    rs_txt = f"相對 SPY 一個月超額 {rs_1m:+.1f}%（強弱百分位 {rank:.0f}）" if rs_1m is not None \
        else f"相對強弱百分位 {rank:.0f}"
    return score, f"{rs_txt}{theme_txt}，現價距 MA20 {gap:+.1f}%（回檔不破季線）"


_SCORERS = {
    "oversold_bounce": _score_oversold_bounce,
    "undervalued_catalyst": _score_undervalued_catalyst,
    "momentum_breakout": _score_momentum_breakout,
    "mimi_entry": _score_mimi_entry,
    "breakdown_short": _score_breakdown_short,
    "rs_leader": _score_rs_leader,
}

# 綜合排名時各 bucket 的權重（同分時偏好基本面+動能，超賣次之）
_BUCKET_WEIGHT = {
    "undervalued_catalyst": 1.15,
    "momentum_breakout": 1.05,
    "mimi_entry": 1.0,
    "oversold_bounce": 0.95,
    "breakdown_short": 0.9,
    # 相對強度是「別跟趨勢作對」的那一票，權重比照動能
    "rs_leader": 1.05,
}


def _evaluate_ticker(ticker, tech, db, mimi_section, days_to_earn, dcf_disc,
                     tier: str = "curated") -> dict | None:
    """對單一 ticker 跑五個 scorer，回一筆機會記錄（未命中任何 bucket 回 None）。"""
    if not tech:
        return None
    buckets: dict[str, dict] = {}
    for name, scorer in _SCORERS.items():
        try:
            res = scorer(tech, db, mimi_section, days_to_earn, dcf_disc)
        except Exception:
            res = None
        if res:
            sub, ev = res
            buckets[name] = {"sub_score": _round(sub, 3), "evidence": ev}

    if not buckets:
        return None

    # signal_score = 最強 bucket 主導 + 其他 bucket 疊加（多角度共振加分）
    weighted = [buckets[b]["sub_score"] * _BUCKET_WEIGHT[b] for b in buckets]
    weighted.sort(reverse=True)
    signal_score = weighted[0] + sum(w * 0.25 for w in weighted[1:])
    signal_score = round(min(1.0, signal_score), 3)

    direction = "short" if set(buckets) == {"breakdown_short"} or (
        "breakdown_short" in buckets and buckets["breakdown_short"]["sub_score"] == max(
            b["sub_score"] for b in buckets.values())
    ) else "long"

    db = db or {}
    price = tech.get("price") or db.get("current_price")
    mimi_entry = extract_mimi_entry_zone(mimi_section) if mimi_section else None
    return {
        "ticker": ticker,
        "company": db.get("company") or _discovery_name(ticker),
        "industry": db.get("industry"),
        "tier": tier,
        "direction": direction,
        "signal_score": signal_score,
        "setups": sorted(buckets.keys(), key=lambda b: -buckets[b]["sub_score"]),
        "bucket_detail": buckets,
        "key_numbers": {
            "price": _round(price),
            "rsi_14": tech.get("rsi_14"),
            "ma5": tech.get("ma5"),
            "ma20": tech.get("ma20"),
            "ma50": tech.get("ma50"),
            "ma_alignment": _ma_alignment(price, tech.get("ma5"), tech.get("ma20"), tech.get("ma50")),
            "volume_ratio": tech.get("volume_ratio"),
            "near_support": tech.get("near_support") or [],
            "near_resistance": tech.get("near_resistance") or [],
            "breakouts": tech.get("breakouts") or [],
            "breakdowns": tech.get("breakdowns") or [],
            "pe_estimated": _round(db.get("estimated_pe")),
            "pe_median_5y": _round(db.get("pe_median_5y")),
            "valuation_status": db.get("valuation_status"),
            "valuation_diff_pct": _round(db.get("valuation_diff_pct")),
            "dcf_discount_pct": dcf_disc,
            "days_to_earnings": days_to_earn,
            "mimi_entry_zone": mimi_entry if mimi_entry not in ("—", "") else None,
            "rs_rank": tech.get("rs_rank"),
            "rs_1m": (tech.get("rs") or {}).get("1m"),
            "rs_3m": (tech.get("rs") or {}).get("3m"),
            "theme": tech.get("theme"),
            "theme_rs_rank": tech.get("theme_rs_rank"),
        },
    }


# ── 主入口 ────────────────────────────────────────────────────────────────────

def prescreen(limit: int = 20, setup: str = "all", use_cache: bool = True) -> dict:
    """Stage-1 量化預篩：掃全 universe，分類 + 排名，回 top `limit`。

    setup: "all" 或單一 bucket 名稱（只回命中該 bucket 的機會）。
    回傳 dict：{generated_at, universe_size, evaluated, returned, setup, opportunities[]}。
    """
    cache_key = "prescreen"
    if use_cache:
        c = _CACHE.get(cache_key)
        if c and (time.time() - c["ts"]) < _CACHE_TTL:
            full = c["data"]
            return _slice(full, limit, setup)

    mimi_sections = {}
    try:
        mimi_sections = parse_mimi_doc()
    except Exception:
        mimi_sections = {}

    universe, tiers = build_universe(mimi_sections)

    # 一次撈 DB 估值/財報
    db_snap = _load_db_snapshot(universe)
    # 並行抓技術面（復用 portfolio._fetch_technicals_batch，6mo，含支撐壓力/突破/破位）
    # SPY 一起抓：相對強度要有基準，多這一檔比為了它另外打一輪 yfinance 便宜。
    tech_map = _fetch_technicals_batch(universe + [BENCHMARK])

    # 強弱榜。算完把每檔的 RS 掛回 tech，scorer 才看得到「它站在哪個產業裡、
    # 相對大盤是強是弱」—— rs_leader 靠這個成立，價值陷阱降權也靠這個。
    board = build_strength_board(tech_map, with_sectors=False)
    theme_rank = {t["theme"]: t.get("rs_rank") for t in board["themes"]}
    for t, meta in board["stocks"]["by_ticker"].items():
        tech = tech_map.get(t)
        if not tech:
            continue
        tech["rs"] = meta["rs"]
        tech["rs_rank"] = meta["rs_rank"]
        tech["theme"] = meta["theme"]
        tech["theme_rs_rank"] = theme_rank.get(meta["theme"])
    tech_map.pop(BENCHMARK, None)          # 基準本身不是機會
    # 並行抓財報日
    earn_map = _earnings_map(universe)
    held = _held_map()

    opportunities: list[dict] = []
    for t in universe:
        tech = tech_map.get(t)
        if not tech:
            continue
        db = db_snap.get(t)
        mimi_section = mimi_sections.get(t)
        days_to_earn = earn_map.get(t)
        dcf_disc = _dcf_discount_pct(t)
        rec = _evaluate_ticker(t, tech, db, mimi_section, days_to_earn, dcf_disc,
                               tier=tiers.get(t, "curated"))
        if rec is None:
            continue
        rec["in_portfolio"] = t in held
        rec["held_portfolios"] = held.get(t, [])
        opportunities.append(rec)

    # signal_score 天生會撞頂（低估 0.85 + 任一 bucket 疊加就滿分），一堆並列時
    # 原本等於按字母排 —— 同分改用相對強弱決勝，強勢的排前面。
    # 同分時研究過的排前面：發現層是還沒做功課的名字，要更強才該擠掉手上的標的
    opportunities.sort(key=lambda x: (-x["signal_score"],
                                      0 if x.get("tier") == "curated" else 1,
                                      -(x["key_numbers"].get("rs_rank") or 0)))

    full = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "universe_size": len(universe),
        "universe_tiers": {
            "curated": sum(1 for v in tiers.values() if v == "curated"),
            "discovery": sum(1 for v in tiers.values() if v == "discovery"),
        },
        "evaluated": len(tech_map),
        "opportunities": opportunities,
        "strength": board,
    }
    # 只快取「健康」的掃描結果：yfinance 偶爾整批被限流（evaluated 遠低於 universe），
    # 若把這種退化結果寫進快取會毒化後續 10 分鐘查詢，故低於 30% 覆蓋率就不寫快取。
    healthy = len(tech_map) >= max(1, int(len(universe) * 0.3))
    if use_cache and healthy:
        _CACHE[cache_key] = {"data": full, "ts": time.time()}
    return _slice(full, limit, setup)


# 發現層在 top N 裡的保障名額。signal_score 會在 1.0 撞頂，滿分並列動輒二三十檔，
# 純按排名切的話「同分 curated 優先」會把發現層整批擠出去 —— 掃了一千多檔沒研究過的
# 股票，卻一檔都送不進 Stage-2，等於白掃。所以保障一部分席次給發現層最強的幾檔。
DISCOVERY_QUOTA = 0.25


def _slice(full: dict, limit: int, setup: str) -> dict:
    opps = full["opportunities"]
    if setup and setup != "all":
        opps = [o for o in opps if setup in o["setups"]]
    limit = max(1, limit)
    returned = opps[:limit]

    quota = int(limit * DISCOVERY_QUOTA)
    if quota and len(opps) > limit:
        short_by = quota - sum(1 for o in returned if o.get("tier") == "discovery")
        if short_by > 0:
            extra = [o for o in opps[limit:] if o.get("tier") == "discovery"][:short_by]
            if extra:
                # 換掉排名最後的 curated，名次高的不動
                keep = [o for o in returned if o.get("tier") == "discovery"]
                curated = [o for o in returned if o.get("tier") != "discovery"]
                curated = curated[: max(0, limit - len(keep) - len(extra))]
                returned = sorted(
                    keep + curated + extra,
                    key=lambda x: (-x["signal_score"],
                                   0 if x.get("tier") == "curated" else 1,
                                   -(x["key_numbers"].get("rs_rank") or 0)),
                )
    return {
        "generated_at": full["generated_at"],
        "universe_size": full["universe_size"],
        "universe_tiers": full.get("universe_tiers") or {},
        "evaluated": full["evaluated"],
        "screened": len(returned),
        "total_candidates": len(full["opportunities"]),
        "setup": setup,
        "setup_types": list(SETUP_TYPES),
        "opportunities": returned,
        "strength": full.get("strength"),
    }


def clear_cache() -> None:
    _CACHE.clear()
