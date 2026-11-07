import sys as _sys

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..config import settings
from ..database import get_db
from ..models import Stock, Metric, Forecast, Statistic, Valuation
from ..schemas import StockDetail, MetricOut, ForecastOut, ValuationOut

if str(settings.PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(settings.PROJECT_ROOT))
from src.quotes import get_quotes as _get_quotes  # noqa: E402

router = APIRouter()


@router.get("")
def compare_stocks(
    tickers: str = Query(..., description="Comma-separated tickers, up to 5"),
    db: Session = Depends(get_db),
):
    """Side-by-side comparison of up to 5 stocks."""
    ticker_list = [t.strip().upper() for t in tickers.split(",")][:5]

    result = []
    for ticker in ticker_list:
        stock = db.query(Stock).filter(func.upper(Stock.ticker) == ticker).first()
        if not stock:
            continue

        latest_metric = (
            db.query(Metric)
            .filter_by(stock_id=stock.id)
            .order_by(Metric.snapshot_date.desc())
            .first()
        )
        latest_forecast = (
            db.query(Forecast)
            .filter_by(stock_id=stock.id)
            .order_by(Forecast.snapshot_date.desc())
            .first()
        )
        latest_val = (
            db.query(Valuation)
            .filter_by(stock_id=stock.id)
            .order_by(Valuation.snapshot_date.desc())
            .first()
        )

        detail = StockDetail(
            ticker=stock.ticker,
            company_name=stock.company_name,
            industry=stock.industry.name,
            latest_metric=MetricOut.model_validate(latest_metric) if latest_metric else None,
            latest_forecast=ForecastOut.model_validate(latest_forecast) if latest_forecast else None,
            latest_valuation=_val_out(stock, latest_val) if latest_val else None,
        )
        result.append(detail)

    return {"stocks": result}


# ── 同業比較的指標目錄 ────────────────────────────────────────────────────────
#
# 分組照 Morningstar / Seeking Alpha 的同業比較：
# 估值 → 成長 → 獲利 → 報酬 → 財務健康 → 股東回饋 → 股價風險 → 分析師。
#
# 兩個資料源，各有各的問題，所以逐欄取捨：
#   metrics    每天更新（最新快照 186 檔），但只有二十幾個欄位有值 ——
#              margins / 成長 / 股利 / 動能整片是空的（全表 0 筆非空）。
#   statistics 欄位齊全（190–250 檔涵蓋 margins、成長預估、Altman/Piotroski、
#              RSI/Beta、目標價），但抓得稀疏，最新一批停在兩個月前。
# 規則：有 col（metrics 欄位）且有值就用 metrics，否則退回 statistics，
# 並在回應裡標明每個指標的來源與資料日期 —— 讓畫面說得出「這個數字有多舊」。
#
# higher_better: True 高的好、False 低的好、None 中性（RSI、Beta、分析師數
# 這種沒有好壞方向的，不上色也不排名）。
# stat: (section, label) —— statistics 表的鍵。百分比一律存成整數位（52.7 = 52.7%）。
PEER_GROUPS = [
    {"key": "valuation",   "title": "估值倍數",   "hint": "排名愈前＝相對同業愈便宜"},
    {"key": "growth",      "title": "成長預估",   "hint": "排名愈前＝分析師預期成長愈快"},
    {"key": "profitability", "title": "獲利品質", "hint": "排名愈前＝利潤率愈高"},
    {"key": "returns",     "title": "資本報酬",   "hint": "排名愈前＝資本用得愈有效率"},
    {"key": "health",      "title": "財務健康",   "hint": "排名愈前＝體質愈穩"},
    {"key": "shareholder", "title": "股東回饋",   "hint": "排名愈前＝還給股東愈多"},
    {"key": "momentum",    "title": "股價與風險", "hint": "中性指標，只列數字不排名"},
    {"key": "analyst",     "title": "分析師",     "hint": "排名愈前＝距離目標價空間愈大"},
]

PEER_METRICS = [
    # ── 估值 ──
    {"key": "pe", "sid": "pe", "col": "pe",         "label": "P/E",        "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Valuation Ratios", "PE Ratio")},
    {"key": "pe_fwd", "sid": "peForward", "col": "pe_forward",     "label": "Fwd P/E",    "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Valuation Ratios", "Forward PE")},
    {"key": "peg", "sid": "pegRatio", "col": "peg_ratio",        "label": "PEG",        "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Valuation Ratios", "PEG Ratio")},
    {"key": "ps", "sid": "ps", "col": "ps",         "label": "P/S",        "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Valuation Ratios", "PS Ratio")},
    {"key": "ps_fwd", "sid": "psForward", "col": "ps_forward",     "label": "Fwd P/S",    "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Valuation Ratios", "Forward PS")},
    {"key": "pb", "sid": "pb", "col": "pb",         "label": "P/B",        "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Valuation Ratios", "PB Ratio")},
    {"key": "pfcf", "sid": "pfcf",       "label": "P/FCF",      "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Valuation Ratios", "P/FCF Ratio")},
    {"key": "pocf", "sid": "pocf",       "label": "P/OCF",      "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Valuation Ratios", "P/OCF Ratio")},
    {"key": "ev_sales", "sid": "evSales", "col": "ev_sales",   "label": "EV/Sales",   "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Enterprise Valuation", "EV / Sales")},
    {"key": "ev_ebitda", "sid": "evEbitda", "col": "ev_ebitda",  "label": "EV/EBITDA",  "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Enterprise Valuation", "EV / EBITDA")},
    {"key": "ev_ebit", "sid": "evEbit", "col": "ev_ebit",    "label": "EV/EBIT",    "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Enterprise Valuation", "EV / EBIT")},
    {"key": "ev_fcf", "sid": "evFcf", "col": "ev_fcf",     "label": "EV/FCF",     "group": "valuation", "higher_better": False, "unit": "x", "stat": ("Enterprise Valuation", "EV / FCF")},
    # ── 成長（分析師預估）──
    # stockanalysis 在 2026 年中把「5Y 預估」改名成「3Y 預估」，所以資料庫裡
    # 舊快照（2026-02-01 那批 142 檔）只有 5Y、新快照（07-02 那批 99 檔）只有 3Y。
    # 當成兩個指標的話每檔只會填到一個，整欄看起來半空 —— 這裡合併成一個，
    # 新的優先、舊的後備，並在 horizon 標明每一檔實際是哪個期間。
    {"key": "rev_g", "sid": "revenue3y", "label": "營收成長預估", "group": "growth", "higher_better": True, "unit": "%",
     "stat": ("Analyst Forecast", "Revenue Growth Forecast (3Y)"),
     "stat_alt": ("Analyst Forecast", "Revenue Growth Forecast (5Y)"),
     "sid_alt": "revenue5y",
     "horizons": ("3Y", "5Y")},
    {"key": "eps_g", "sid": "eps3y", "label": "EPS 成長預估", "group": "growth", "higher_better": True, "unit": "%",
     "stat": ("Analyst Forecast", "EPS Growth Forecast (3Y)"),
     "stat_alt": ("Analyst Forecast", "EPS Growth Forecast (5Y)"),
     "sid_alt": "eps5y",
     "horizons": ("3Y", "5Y")},
    # ── 獲利品質 ──
    {"key": "gross_m", "sid": "grossMargin",    "label": "毛利率",     "group": "profitability", "higher_better": True, "unit": "%", "stat": ("Margins", "Gross Margin")},
    {"key": "op_m", "sid": "operatingMargin",       "label": "營益率",     "group": "profitability", "higher_better": True, "unit": "%", "stat": ("Margins", "Operating Margin")},
    {"key": "ebitda_m", "sid": "ebitdaMargin",   "label": "EBITDA 率",  "group": "profitability", "higher_better": True, "unit": "%", "stat": ("Margins", "EBITDA Margin")},
    {"key": "net_m", "sid": "profitMargin",      "label": "淨利率",     "group": "profitability", "higher_better": True, "unit": "%", "stat": ("Margins", "Profit Margin")},
    {"key": "fcf_m", "sid": "fcfMargin",      "label": "FCF 率",     "group": "profitability", "higher_better": True, "unit": "%", "stat": ("Margins", "FCF Margin")},
    # ── 資本報酬 ──
    {"key": "roe", "sid": "roe", "col": "roe",        "label": "ROE",        "group": "returns", "higher_better": True, "unit": "%", "stat": ("Financial Efficiency", "Return on Equity (ROE)")},
    {"key": "roa", "sid": "roa", "col": "roa",        "label": "ROA",        "group": "returns", "higher_better": True, "unit": "%", "stat": ("Financial Efficiency", "Return on Assets (ROA)")},
    {"key": "roic", "sid": "roic", "col": "roic",       "label": "ROIC",       "group": "returns", "higher_better": True, "unit": "%", "stat": ("Financial Efficiency", "Return on Invested Capital (ROIC)")},
    {"key": "roce", "sid": "roce", "col": "roce",       "label": "ROCE",       "group": "returns", "higher_better": True, "unit": "%", "stat": ("Financial Efficiency", "Return on Capital Employed (ROCE)")},
    {"key": "wacc", "sid": "wacc",       "label": "WACC",       "group": "returns", "higher_better": False, "unit": "%", "stat": ("Financial Efficiency", "Weighted Average Cost of Capital (WACC)")},
    {"key": "asset_to", "sid": "assetturnover",   "label": "資產週轉",   "group": "returns", "higher_better": True, "unit": "x", "stat": ("Financial Efficiency", "Asset Turnover")},
    # ── 財務健康 ──
    {"key": "current_r", "sid": "currentRatio", "col": "current_ratio",  "label": "流動比",     "group": "health", "higher_better": True,  "unit": "x", "stat": ("Financial Position", "Current Ratio")},
    {"key": "quick_r", "sid": "quickRatio", "col": "quick_ratio",    "label": "速動比",     "group": "health", "higher_better": True,  "unit": "x", "stat": ("Financial Position", "Quick Ratio")},
    {"key": "de", "sid": "debtEquity", "col": "debt_equity",         "label": "Debt/Equity","group": "health", "higher_better": False, "unit": "x", "stat": ("Financial Position", "Debt / Equity")},
    {"key": "d_ebitda", "sid": "debtEbitda", "col": "debt_ebitda",   "label": "Debt/EBITDA","group": "health", "higher_better": False, "unit": "x", "stat": ("Financial Position", "Debt / EBITDA")},
    {"key": "int_cov", "sid": "interestCoverage", "col": "interest_coverage",    "label": "利息保障",   "group": "health", "higher_better": True,  "unit": "x", "stat": ("Financial Position", "Interest Coverage")},
    {"key": "altman", "sid": "zScore",     "label": "Altman Z",   "group": "health", "higher_better": True,  "unit": "",  "stat": ("Scores", "Altman Z-Score")},
    {"key": "piotroski", "sid": "fScore",  "label": "Piotroski F","group": "health", "higher_better": True,  "unit": "",  "stat": ("Scores", "Piotroski F-Score")},
    # ── 股東回饋 ──
    {"key": "div_yield", "sid": "dividendYield", "col": "dividend_yield",  "label": "股息殖利率", "group": "shareholder", "higher_better": True,  "unit": "%", "stat": ("Dividends & Yields", "Dividend Yield")},
    {"key": "payout", "sid": "payoutRatio", "col": "payout_ratio",     "label": "配息率",     "group": "shareholder", "higher_better": False, "unit": "%", "stat": ("Dividends & Yields", "Payout Ratio")},
    {"key": "buyback", "sid": "buybackYield", "col": "buyback_yield",    "label": "回購殖利率", "group": "shareholder", "higher_better": True,  "unit": "%", "stat": ("Dividends & Yields", "Buyback Yield")},
    {"key": "shldr_y", "sid": "totalReturn",    "label": "股東總報酬", "group": "shareholder", "higher_better": True,  "unit": "%", "stat": ("Dividends & Yields", "Shareholder Yield")},
    {"key": "fcf_yield", "sid": "fcfYield", "col": "fcf_yield",  "label": "FCF Yield",  "group": "shareholder", "higher_better": True,  "unit": "%", "stat": ("Dividends & Yields", "FCF Yield")},
    {"key": "earn_yield", "sid": "earningsYield", "col": "earnings_yield", "label": "盈餘殖利率", "group": "shareholder", "higher_better": True,  "unit": "%", "stat": ("Dividends & Yields", "Earnings Yield")},
    # ── 股價與風險（中性，不排名）──
    {"key": "chg_52w", "sid": "ch1y",    "label": "52週漲跌",   "group": "momentum", "higher_better": True, "unit": "%", "stat": ("Stock Price Statistics", "52-Week Price Change")},
    {"key": "rsi", "sid": "rsi",        "label": "RSI",        "group": "momentum", "higher_better": None, "unit": "",  "stat": ("Stock Price Statistics", "Relative Strength Index (RSI)")},
    {"key": "beta", "sid": "beta",       "label": "Beta 5Y",    "group": "momentum", "higher_better": None, "unit": "",  "stat": ("Stock Price Statistics", "Beta (5Y)")},
    {"key": "short_f", "sid": "shortFloat",    "label": "空單/流通",  "group": "momentum", "higher_better": None, "unit": "%", "stat": ("Short Selling Information", "Short % of Float")},
    # ── 分析師 ──
    {"key": "pt_diff", "sid": "priceTargetChange",    "label": "目標價空間", "group": "analyst", "higher_better": True, "unit": "%", "stat": ("Analyst Forecast", "Price Target Difference")},
    {"key": "analysts", "sid": "analystCount",   "label": "分析師數",   "group": "analyst", "higher_better": None, "unit": "",  "stat": ("Analyst Forecast", "Analyst Count")},
]


def _stats_by_stock(db: Session, stock_ids: list[int]) -> dict[int, dict]:
    """每檔最新一次 statistics 快照。

    回兩份對照：by_id（來源的穩定鍵，如 grossMargin）與 by_label（(section, label)）。
    優先用 id —— label 是顯示字串，來源改過名（Revenue Growth Forecast 5Y → 3Y）
    就會讓整欄在下游斷掉。舊快照沒有 metric_id，才退回 label。

    一次查完全部同業 —— 逐檔查在 23 檔的產業就是 23 趟 round-trip。
    """
    if not stock_ids:
        return {}
    latest = (
        db.query(Statistic.stock_id, func.max(Statistic.snapshot_date).label("d"))
        .filter(Statistic.stock_id.in_(stock_ids))
        .group_by(Statistic.stock_id)
        .subquery()
    )
    rows = (
        db.query(Statistic.stock_id, Statistic.section, Statistic.label,
                 Statistic.metric_id, Statistic.value)
        .join(latest, (Statistic.stock_id == latest.c.stock_id)
                     & (Statistic.snapshot_date == latest.c.d))
        .filter(Statistic.value.isnot(None))
        .all()
    )
    out: dict[int, dict] = {}
    for stock_id, section, label, metric_id, value in rows:
        bucket = out.setdefault(stock_id, {"by_id": {}, "by_label": {}})
        bucket["by_label"][(section, label)] = value
        if metric_id:
            bucket["by_id"][metric_id] = value
    return out


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def _quantile(xs: list[float], q: float) -> float | None:
    """線性內插分位數 —— 同業樣本常常只有十幾檔，不能用最近鄰硬取。"""
    if not xs:
        return None
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _peer_stats(rows: list[dict], target: dict | None) -> list[dict]:
    """每個指標在同業裡的分布，以及本檔的名次。

    rows 是同業（不含本檔）—— 本檔算進中位數會把中位數往自己拉，名次也失真。
    higher_better 為 None 的中性指標（RSI、Beta、分析師數）只給分布，不給名次。
    """
    out = []
    for meta in PEER_METRICS:
        key = meta["key"]
        vals = [r["values"][key] for r in rows if r["values"].get(key) is not None]
        tv = (target or {}).get("values", {}).get(key)
        src = (target or {}).get("sources", {}).get(key)
        horizon = (target or {}).get("horizons", {}).get(key)
        # 同業裡有幾檔是 3Y、幾檔是 5Y —— 混在一起算中位數要標明白
        hmix = {}
        for r in rows:
            h = r.get("horizons", {}).get(key)
            if h and r["values"].get(key) is not None:
                hmix[h] = hmix.get(h, 0) + 1
        med = _median(vals)
        neutral = meta["higher_better"] is None
        pct = rank = rank_total = diff_pct = diff_abs = None
        if tv is not None and vals:
            below = sum(1 for v in vals if v < tv)
            pct = round(below / len(vals) * 100)
            if not neutral:
                # 「第 7 便宜／共 22 檔」比百分位好懂，而且小樣本也不會誇大
                rank_total = len(vals) + 1
                rank = below + 1 if not meta["higher_better"] else rank_total - below
            if med is not None:
                # 絕對差永遠算得出來；百分比型指標（利潤率、殖利率）就該看它。
                # 相對差只給倍數用 —— 回購殖利率的同業中位數是 -0.007%，
                # 拿它當分母會算出「+16000%」這種數字。
                diff_abs = round(tv - med, 2)
                if meta["unit"] == "x" and abs(med) > 1e-6:
                    diff_pct = round((tv - med) / abs(med) * 100, 1)
        out.append({
            **{k: v for k, v in meta.items() if k not in ("stat", "stat_alt", "sid", "sid_alt", "col")},
            "n": len(vals),
            "source": src,
            "horizon": horizon,
            "horizon_mix": hmix or None,
            "min": min(vals) if vals else None,
            "p25": _quantile(vals, 0.25),
            "median": med,
            "p75": _quantile(vals, 0.75),
            "max": max(vals) if vals else None,
            "target": tv,
            "percentile": pct,
            "rank": rank,
            "rank_total": rank_total,
            "diff_vs_median_pct": diff_pct,   # 只有倍數（unit=x）有
            "diff_vs_median_abs": diff_abs,   # 一律有；% 型指標讀這個（百分點）
        })
    return out


@router.get("/peers/{ticker}")
def compare_with_peers(ticker: str, db: Session = Depends(get_db)):
    """Compare a stock against all industry peers tracked in the DB.

    除了逐檔明細，另外回傳三塊：
      groups  —— 指標的分組（估值／成長／獲利／報酬／健康／股東回饋／股價／分析師）
      stats   —— 每個指標在同業裡的分布（min/p25/median/p75/max）與本檔名次
      implied —— 用「同業中位數 P/E × 本檔 EPS」推算的合理價

    implied 刻意跟頁面上既有的兩張卡（本檔自己 5 年中位 PE 推的價）用同一套算法，
    只換掉倍數的來源 —— 兩者並排才讀得出「自己的歷史說 $X、同業說 $Y」。
    """
    ticker = ticker.upper()
    stock = db.query(Stock).filter(func.upper(Stock.ticker) == ticker).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"{ticker} not found in database")

    peers = (
        db.query(Stock)
        .filter(Stock.industry_id == stock.industry_id)
        .order_by(Stock.ticker)
        .all()
    )
    stat_map = _stats_by_stock(db, [p.id for p in peers])

    # metrics.current_price 是每週那輪比率表爬蟲抄下來的「Last Close Price」，
    # 最久會停一整週。同業表整排價格擺在一起比，差一天就看得出來，所以蓋上即時報價；
    # 抓不到的（下市、改名）退回 metrics，不留空。
    live = _get_quotes([p.ticker for p in peers])

    result = []
    for s in peers:
        m = (
            db.query(Metric)
            .filter_by(stock_id=s.id)
            .order_by(Metric.snapshot_date.desc())
            .first()
        )
        v = (
            db.query(Valuation)
            .filter_by(stock_id=s.id)
            .order_by(Valuation.snapshot_date.desc())
            .first()
        )
        stats_row = stat_map.get(s.id, {})
        # 目錄裡每個指標攤成一個扁平的 key → 值。metrics 新但欄位少，
        # statistics 齊但舊 —— 有新的就用新的，剩下的才退回 statistics。
        values, sources, horizons = {}, {}, {}
        for meta in PEER_METRICS:
            key = meta["key"]
            mv = getattr(m, meta["col"], None) if (m and meta.get("col")) else None
            if mv is not None:
                values[key], sources[key] = mv, "metrics"
                continue
            by_id, by_label = stats_row.get("by_id", {}), stats_row.get("by_label", {})
            # 穩定 id 優先；沒有（舊快照）才用顯示標題找
            v0 = by_id.get(meta["sid"]) if meta.get("sid") else None
            if v0 is None:
                v0 = by_label.get(meta["stat"])
            if v0 is None and (meta.get("sid_alt") or meta.get("stat_alt")):
                v0 = by_id.get(meta.get("sid_alt")) or by_label.get(meta.get("stat_alt"))
                if v0 is not None and meta.get("horizons"):
                    horizons[key] = meta["horizons"][1]
            elif v0 is not None and meta.get("horizons"):
                horizons[key] = meta["horizons"][0]
            values[key] = v0
            sources[key] = "statistics" if v0 is not None else None
        result.append({
            "ticker": s.ticker,
            "company_name": s.company_name,
            "is_target": s.ticker.upper() == ticker,
            # 規模與股價不進指標目錄（沒有「同業中位市值」這種比較），單獨給。
            # metrics 優先 —— statistics 的市值是兩個月前的，會跟旁邊的現價對不起來。
            "market_cap": (m.market_cap if m else None)
                          or stats_row.get("by_id", {}).get("marketcap")
                          or stats_row.get("by_label", {}).get(("Total Valuation", "Market Cap")),
            "current_price": (live.get(s.ticker.upper()) or {}).get("price")
                             or (m.current_price if m else None),
            "values": values,
            "sources": sources,
            # 只有成長預估會有：這一格是 3Y 還是 5Y 的預估
            "horizons": horizons,
            "valuation": {
                "status":                    v.valuation_status           if v else None,
                "current_year_fair_price":   v.current_year_fair_price    if v else None,
                "next_year_fair_price":      v.next_year_fair_price       if v else None,
                "valuation_diff_pct":        v.valuation_diff_pct         if v else None,
                "pe_median_5y":              v.pe_median_5y               if v else None,
                "current_year_eps":          v.current_year_eps           if v else None,
                "next_year_eps":             v.next_year_eps              if v else None,
                "pe_median_price":           v.pe_median_price            if v else None,
            } if v else None,
        })

    tgt_stock = next((p for p in peers if p.ticker.upper() == ticker), None)
    as_of = {"metrics": None, "statistics": None}
    if tgt_stock:
        mrow = (db.query(Metric.snapshot_date).filter_by(stock_id=tgt_stock.id)
                  .order_by(Metric.snapshot_date.desc()).first())
        srow = (db.query(func.max(Statistic.snapshot_date))
                  .filter(Statistic.stock_id == tgt_stock.id).first())
        as_of["metrics"] = str(mrow[0]) if mrow and mrow[0] else None
        as_of["statistics"] = str(srow[0]) if srow and srow[0] else None

    target = next((r for r in result if r["is_target"]), None)
    # 本檔自己不算進同業分布 —— 不然中位數會被自己往回拉，名次也失真
    others = [r for r in result if not r["is_target"]]
    stats = _peer_stats(others, target)

    tv = (target or {}).get("valuation") or {}
    price = (target or {}).get("current_price")
    pe_med = next((s["median"] for s in stats if s["key"] == "pe"), None)

    def _implied(eps):
        if pe_med is None or eps is None:
            return None
        fair = round(pe_med * eps, 2)
        return {
            "fair_price": fair,
            "diff_pct": round((price - fair) / fair * 100, 1) if price and fair else None,
        }

    return {
        "ticker": ticker,
        "industry": stock.industry.name,
        "peer_count": len(result),
        "peers": result,
        "groups": PEER_GROUPS,
        "as_of": as_of,
        "stats": stats,
        "implied": {
            "peer_median_pe": round(pe_med, 2) if pe_med is not None else None,
            "peer_n": next((s["n"] for s in stats if s["key"] == "pe"), 0),
            "current_price": price,
            "current_year_eps": tv.get("current_year_eps"),
            "next_year_eps": tv.get("next_year_eps"),
            "current_year": _implied(tv.get("current_year_eps")),
            "next_year": _implied(tv.get("next_year_eps")),
            # 並排對照用：本檔自己 5 年中位 PE 推的價
            "own_pe_median_5y": tv.get("pe_median_5y"),
            "own_fair_price": tv.get("pe_median_price"),
        },
    }


def _val_out(stock, v):
    return ValuationOut(
        ticker=stock.ticker,
        company_name=stock.company_name,
        industry=stock.industry.name,
        snapshot_date=v.snapshot_date,
        fiscal_year=v.fiscal_year,
        estimated_pe=v.estimated_pe,
        current_year_eps=v.current_year_eps,
        current_year_fair_price=v.current_year_fair_price,
        next_year_eps=v.next_year_eps,
        next_year_fair_price=v.next_year_fair_price,
        pe_median_5y=v.pe_median_5y,
        pe_median_price=v.pe_median_price,
        next_year_pe_median_price=v.next_year_pe_median_price,
        current_price=v.current_price,
        market_cap_display=v.market_cap_display,
        valuation_status=v.valuation_status,
        valuation_diff_pct=v.valuation_diff_pct,
        next_year_valuation_status=v.next_year_valuation_status,
        next_year_valuation_diff_pct=v.next_year_valuation_diff_pct,
        revenue_growth_5y=v.revenue_growth_5y,
        eps_growth_5y=v.eps_growth_5y,
        eps_growth_past=v.eps_growth_past,
    )
