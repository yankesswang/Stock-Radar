"""
Database service for scrapers.
Provides functions to save scraped data directly to SQLite database.
"""
import sys
from datetime import date
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func

from backend.app.database import SessionLocal, engine, Base
from backend.app.models import (
    Industry, Stock, Metric, Forecast, PeMedian, Valuation, DataSnapshot,
    Statistic, MetricsBreakdown,
)
from backend.app.config import settings
from src.config import PE_TICKER_TO_COMPANY

# --- Field mapping from JSON keys to DB column names ---
RATIO_KEY_MAP = {
    "marketcap": "market_cap",
    "enterpriseValue": "enterprise_value",
    "currentPrice": "current_price",
    "pe": "pe",
    "peForward": "pe_forward",
    "ps": "ps",
    "psForward": "ps_forward",
    "pb": "pb",
    "pegRatio": "peg_ratio",
    "evEarnings": "ev_earnings",
    "evSales": "ev_sales",
    "evEbitda": "ev_ebitda",
    "evEbit": "ev_ebit",
    "evFcf": "ev_fcf",
    "currentRatio": "current_ratio",
    "quickRatio": "quick_ratio",
    "debtEquity": "debt_equity",
    "debtEbitda": "debt_ebitda",
    "debtFcf": "debt_fcf",
    "interestCoverage": "interest_coverage",
    "grossMargin": "gross_margin",
    "operatingMargin": "operating_margin",
    "pretaxMargin": "pretax_margin",
    "profitMargin": "profit_margin",
    "ebitdaMargin": "ebitda_margin",
    "ebitMargin": "ebit_margin",
    "fcfMargin": "fcf_margin",
    "roe": "roe",
    "roa": "roa",
    "roic": "roic",
    "roce": "roce",
    "revenue": "revenue",
    "gp": "gross_profit",
    "opinc": "operating_income",
    "pretax": "pretax_income",
    "netinc": "net_income",
    "ebitda": "ebitda",
    "ebit": "ebit",
    "eps": "eps",
    "totalcash": "total_cash",
    "debt": "debt",
    "netcash": "net_cash",
    "netcashpershare": "net_cash_per_share",
    "equity": "equity",
    "bvps": "bvps",
    "workingcapital": "working_capital",
    "ncfo": "ncfo",
    "capex": "capex",
    "fcf": "fcf",
    "fcfps": "fcf_per_share",
    "dividendYield": "dividend_yield",
    "dividendGrowth": "dividend_growth",
    "dividendGrowthYears": "dividend_growth_years",
    "payoutRatio": "payout_ratio",
    "buybackYield": "buyback_yield",
    "earningsYield": "earnings_yield",
    "fcfYield": "fcf_yield",
    "priceTarget": "price_target",
    "analystCount": "analyst_count",
    "revenue5y": "revenue_5y",
    "eps5y": "eps_5y",
    "sharesout": "shares_out",
    "sharesgrowthyoy": "shares_growth_yoy",
    "float": "float_shares",
    "beta": "beta",
    "ch1y": "change_1y",
    "sma50": "sma_50",
    "sma200": "sma_200",
    "rsi": "rsi",
    "shortInterest": "short_interest",
    "shortFloat": "short_float",
    "shortRatio": "short_ratio",
}


def ensure_tables():
    """Ensure all database tables exist."""
    Base.metadata.create_all(bind=engine)


def get_or_create_industry(db, industry_name: str) -> Industry:
    """Get existing industry or create new one."""
    industry = db.query(Industry).filter_by(name=industry_name).first()
    if not industry:
        industry = Industry(name=industry_name)
        db.add(industry)
        db.flush()
    return industry


def get_or_create_stock(db, ticker: str, industry_name: str) -> Stock:
    """Get existing stock or create new one."""
    stock = db.query(Stock).filter_by(ticker=ticker).first()
    industry = get_or_create_industry(db, industry_name)
    if not stock:
        # Get company name from mapping
        ticker_lower = ticker.lower()
        company_name = None
        if ticker_lower in PE_TICKER_TO_COMPANY:
            slug = PE_TICKER_TO_COMPANY[ticker_lower]
            company_name = slug.replace("-", " ").title()
        
        stock = Stock(
            ticker=ticker,
            company_name=company_name,
            industry_id=industry.id,
        )
        db.add(stock)
        db.flush()
    elif industry_name and industry_name != "Unknown" and stock.industry_id != industry.id:
        stock.industry_id = industry.id
        db.flush()
    return stock


def save_metrics(ticker: str, industry: str, metrics_dict: dict, snapshot_date: date):
    """
    Save ratio/metrics data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        industry: Industry name
        metrics_dict: Dictionary of scraped metrics (JSON keys)
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = get_or_create_stock(db, ticker, industry)
        
        # Map JSON keys to DB columns
        metric_kwargs = {"stock_id": stock.id, "snapshot_date": snapshot_date}
        for json_key, db_col in RATIO_KEY_MAP.items():
            val = metrics_dict.get(json_key)
            if val is not None and not isinstance(val, str):
                metric_kwargs[db_col] = val
        
        # Check if already exists
        existing = (
            db.query(Metric)
            .filter_by(stock_id=stock.id, snapshot_date=snapshot_date)
            .first()
        )
        if existing:
            for k, v in metric_kwargs.items():
                if k not in ("stock_id", "snapshot_date"):
                    setattr(existing, k, v)
        else:
            db.add(Metric(**metric_kwargs))
        
        db.commit()
        print(f"  [DB] Saved metrics for {ticker}")
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving metrics for {ticker}: {e}")
    finally:
        db.close()


def save_forecast(ticker: str, forecast_dict: dict, snapshot_date: date):
    """
    Save forecast data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        forecast_dict: Dictionary with 'annual' key containing forecast data
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter_by(ticker=ticker).first()
        if not stock:
            print(f"  [DB] Stock {ticker} not found, skipping forecast save")
            return
        
        annual = forecast_dict.get("annual", {})
        forecast_kwargs = {
            "stock_id": stock.id,
            "snapshot_date": snapshot_date,
            "current_eps": annual.get("current_eps"),
            "current_eps_growth": annual.get("current_growth"),
            "next_year_eps": annual.get("next_year_eps"),
            "next_year_eps_growth": annual.get("next_year_growth"),
            "current_revenue": annual.get("current_revenue"),
            "current_revenue_growth": annual.get("current_revenue_growth"),
            "next_year_revenue": annual.get("next_year_revenue"),
            "next_year_revenue_growth": annual.get("next_year_revenue_growth"),
        }
        
        existing = (
            db.query(Forecast)
            .filter_by(stock_id=stock.id, snapshot_date=snapshot_date)
            .first()
        )
        if existing:
            for k, v in forecast_kwargs.items():
                if k not in ("stock_id", "snapshot_date"):
                    setattr(existing, k, v)
        else:
            db.add(Forecast(**forecast_kwargs))
        
        db.commit()
        print(f"  [DB] Saved forecast for {ticker}")
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving forecast for {ticker}: {e}")
    finally:
        db.close()


def save_pe_median(ticker: str, pe_median: float, snapshot_date: date):
    """
    Save PE median data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        pe_median: 5-year median PE ratio
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter_by(ticker=ticker).first()
        if not stock:
            print(f"  [DB] Stock {ticker} not found, skipping PE save")
            return
        
        existing = (
            db.query(PeMedian)
            .filter_by(stock_id=stock.id, snapshot_date=snapshot_date)
            .first()
        )
        if existing:
            existing.pe_median = pe_median
        else:
            db.add(PeMedian(stock_id=stock.id, snapshot_date=snapshot_date, pe_median=pe_median))
        
        db.commit()
        print(f"  [DB] Saved PE median for {ticker}")
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving PE median for {ticker}: {e}")
    finally:
        db.close()


# ── PE 中位數合理價的兩個防呆（2026-09）───────────────────────────────────────
#
# 1. 幣別：ADR／外國公司的分析師 EPS 是財報幣別（TSM 台幣、PDD 人民幣、ASML 歐元），
#    有的還是「每股普通股」而不是「每 ADS」（TSM 1 ADS = 5 股）。直接乘上美元股價的
#    PE 中位數，PDD 會算出 $986 的合理價（現價 $78）。換算係數 = 匯率 × ADS 比例：
#    匯率走 Yahoo，ADS 比例用 stockanalysis 自己的美元 Forward PE 反推再對齊常見比例。
#    校準不出來就回 None —— 寧可不給，不給錯的。
# 2. 失真：fullratio 的歷史 PE 是 GAAP，分析師 EPS 是 non-GAAP；剛轉盈（TENB 567x）或
#    大量 SBC（CRM、NOW）的公司，5 年中位數會是 forward PE 的好幾倍，乘出來的合理價
#    只是「現價 × 那個倍數」。超過 PE_MEDIAN_MAX_FWD_RATIO 倍就不產生這個合理價。
PE_MEDIAN_MAX_FWD_RATIO = 3.0
EPS_CONSISTENCY_MAX = 2.0          # 今年度 EPS ÷ Forward PE 反推的 EPS，超出 [1/2, 2] 視為失真
# 1 ADS 代表幾股普通股（存託憑證的公開比例）。stockanalysis 的分析師 EPS 有時是「每 ADS」
# （PDD、YMM），有時是「每股普通股」（TSM、BABA），所以每檔只在 {1, 這個比例} 兩個候選
# 之間選 —— 反推的估計有 ±15% 雜訊，拿一整串常見比例去對齊會把 BEKE(3) 對成 2.5。
# 清單上沒有的外國公司只接受 1（EPS 已經是每 ADS／本身就是普通股掛牌）。
ADS_SHARES = {
    "TSM": 5, "UMC": 5, "ASX": 2,
    "BABA": 8, "BIDU": 8, "PDD": 4, "JD": 2, "NTES": 5, "TCOM": 1, "BEKE": 3,
    "TME": 2, "LI": 2, "NIO": 1, "XPEV": 2, "YMM": 20, "FUTU": 8, "BILI": 1,
    "ZTO": 1, "EDU": 10, "TAL": 3, "VIPS": 0.2, "HTHT": 10, "YUMC": 1, "ZK": 10,
    "RYAAY": 2.5, "ASML": 1, "SAP": 1, "NVO": 1, "AZN": 0.5, "SONY": 1, "TM": 10,
    "HMC": 3, "INFY": 1, "IBN": 2, "HDB": 3, "SHEL": 2, "BP": 6, "TTE": 1,
    "BHP": 2, "RIO": 1, "VALE": 1, "NU": 1, "STLA": 1, "SE": 1, "GRAB": 1,
}
_ADS_SNAP_TOLERANCE = 0.35         # |ln(估計 / 候選)| 的上限，約 ±40%
_fx_cache: dict[str, float | None] = {}


def _fx_to_usd(currency: str) -> float | None:
    """1 單位 currency 值多少美元（Yahoo 日線最後收盤）。"""
    if currency in _fx_cache:
        return _fx_cache[currency]
    rate = None
    try:
        from backend.app.routers.chart import _fetch_yahoo_chart_df
        df = _fetch_yahoo_chart_df(f"{currency}USD=X", "5d", "1d")
        if df is not None and len(df):
            rate = float(df["Close"].dropna().iloc[-1]) or None
    except Exception as e:
        print(f"  [FX] {currency}USD 取不到：{e}")
    _fx_cache[currency] = rate
    return rate


def _latest_forecast_json(ticker: str) -> dict | None:
    import json
    files = sorted((settings.DATA_DIR / "forecast" / "detail").glob(f"{ticker.upper()}_forecast_*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except Exception:
        return None


def _financial_currency(db, ticker: str, fc_json: dict | None) -> str | None:
    """財報幣別：預估 JSON 有記就用；沒有就看 SEC 申報 EPS 的單位（TWD/shares…）；都沒有回 None。"""
    cur = (fc_json or {}).get("financial_currency")
    if cur:
        return cur
    # 取出現最多次的單位，不是最新一期 —— FUTU 最新年報附了一欄美元便利換算，
    # 取最新會誤判成 USD
    # BIDU、TCOM、TME 的 XBRL 沒有每股 EPS 科目，就退而看金額科目的幣別
    from sqlalchemy import text
    for cond in ("unit LIKE '%/shares'", "length(unit) = 3 AND unit NOT IN ('pure')"):
        row = db.execute(text(
            f"SELECT unit, COUNT(*) n FROM financial_facts WHERE ticker = :t AND {cond} "
            "GROUP BY unit ORDER BY n DESC LIMIT 1"), {"t": ticker.upper()}).first()
        if row and row[0]:
            return row[0].split("/")[0]
    return None


def _eps_points(fc_json: dict | None, forecast_annual: dict, as_of: date) -> list[tuple[date, float]]:
    """(會計年度結束日, EPS) —— 實際值與預估值都在，財報幣別。"""
    from datetime import datetime as _dt
    pts = []
    af = (fc_json or {}).get("annual_forecast") or {}
    for end, eps in zip(af.get("period_endings") or [], af.get("eps") or []):
        try:
            pts.append((_dt.strptime(end, "%b %d, %Y").date(), float(eps)))
        except (TypeError, ValueError):
            continue
    if not pts:   # 沒有 JSON：當作 12 月制
        cur, nxt = forecast_annual.get("current_eps"), forecast_annual.get("next_year_eps")
        if cur:
            pts.append((date(as_of.year, 12, 31), float(cur)))
        if nxt:
            pts.append((date(as_of.year + 1, 12, 31), float(nxt)))
    return sorted(pts)


def _eps_window(pts: list[tuple[date, float]], window_end: date) -> float | None:
    """截至 window_end 的 12 個月 EPS：把涵蓋那段期間的兩個會計年度按月份加權。
    兩個年度缺一就回 None（只有一個點時沒辦法內插，不硬猜）。"""
    for (e1, v1), (e2, v2) in zip(pts, pts[1:]):
        if e1 < window_end <= e2:
            w2 = (window_end - e1).days / max((e2 - e1).days, 1)   # 落在後一年度的比例
            return (1 - w2) * v1 + w2 * v2
    return None


def _implied_multiplier(fc_json, forecast_annual, metric, as_of: date) -> float | None:
    """stockanalysis 自己的美元 EPS ÷ 分析師 EPS（財報幣別）＝ 匯率 × ADS 比例 的估計值。

    主錨點：Forward PE（non-GAAP，跟分析師 EPS 同口徑）對未來 12 個月內插值。
    明年度預估缺（ASX 的 FY2027 在 Pro 後面）就退回 TTM PE 對過去 12 個月內插值
    （GAAP 對 non-GAAP 會有落差，只當備援）。
    """
    from datetime import timedelta
    pts = _eps_points(fc_json, forecast_annual, as_of)
    price = getattr(metric, "current_price", None)
    if not price:
        return None
    fpe, tpe = getattr(metric, "pe_forward", None), getattr(metric, "pe", None)
    if fpe and fpe > 0:
        local = _eps_window(pts, as_of + timedelta(days=365))
        if local and local > 0:
            return (price / fpe) / local
    if tpe and tpe > 0:
        # 最近一季大約在 60 天前結束，TTM 視窗就截在那裡
        local = _eps_window(pts, as_of - timedelta(days=60))
        if local and local > 0:
            return (price / tpe) / local
    return None


def eps_usd_factor(db, ticker: str, forecast_annual: dict, metric, as_of: date) -> float | None:
    """分析師 EPS → 美元／每 ADS 的乘數。美國公司回 1.0；外國公司校準不出來回 None。"""
    import math
    fc_json = _latest_forecast_json(ticker)
    cur = _financial_currency(db, ticker, fc_json)
    if cur == "USD":
        return 1.0
    implied = _implied_multiplier(fc_json, forecast_annual, metric, as_of)
    if cur is None:
        # 幣別查不到（沒有 SEC 資料、JSON 也沒記）：反推的乘數接近 1 才當美元，
        # 否則多半是沒被標出來的 ADR（FUTU 港幣、TCOM 人民幣），寧可不算
        if implied and abs(math.log(implied)) < _ADS_SNAP_TOLERANCE:
            return 1.0
        return None
    fx = _fx_to_usd(cur)
    if not (fx and implied):
        return None
    ads_est = implied / fx
    candidates = {1, ADS_SHARES.get(ticker.upper(), 1)}
    snap = min(candidates, key=lambda r: abs(math.log(ads_est / r)))
    if abs(math.log(ads_est / snap)) > _ADS_SNAP_TOLERANCE:
        print(f"  [FX] {ticker} ADS 比例對不齊（估 {ads_est:.2f}，候選 {sorted(candidates)}），不算合理價")
        return None
    return fx * snap


def compute_valuation(
    forecast_annual: dict,
    ratio_data: dict,
    pe_median: float | None,
    fiscal_year: int = 2025,
) -> dict:
    """
    Compute valuation metrics based on forecast, ratio, and PE data.
    Replicates logic from Valuation_Analyzer_Pure.

    ratio_data 可帶 ``eps_usd_factor``（見 eps_usd_factor；None 表示外國公司換算不出來）
    與 ``pe_forward``（失真保護用）。
    """
    eps_5y = ratio_data.get("eps_5y")
    past_growth = forecast_annual.get("current_growth")
    growth = eps_5y or past_growth or 0
    if growth is None:
        growth = 0

    estimated_pe = growth
    if 0 < growth < 5:
        estimated_pe *= 0.8
    elif 5 <= growth < 10:
        estimated_pe *= 1.0
    elif 10 <= growth < 15:
        estimated_pe *= 1.1
    elif 15 <= growth < 20:
        estimated_pe *= 1.2
    elif 20 <= growth < 30:
        estimated_pe *= 1.5
    else:
        estimated_pe *= 2.0

    estimated_pe = round(estimated_pe, 2) if estimated_pe else 0.0

    eps_current = forecast_annual.get("current_eps") or 0.0
    eps_next = forecast_annual.get("next_year_eps") or 0.0

    # 換成美元／每 ADS；外國公司換算不出來就不產生任何合理價（EPS 照原幣保留）
    factor = ratio_data.get("eps_usd_factor", 1.0)
    priceable = factor is not None
    if priceable and factor != 1.0:
        eps_current = round(eps_current * factor, 4)
        eps_next = round(eps_next * factor, 4)

    current_year_fair = round(eps_current * estimated_pe) if eps_current and priceable else 0
    next_year_fair = round(eps_next * estimated_pe) if eps_next and priceable else 0

    pe_med = pe_median or 0
    pe_fwd = ratio_data.get("pe_forward")
    distorted = bool(pe_med and pe_fwd and pe_fwd > 0 and pe_med / pe_fwd > PE_MEDIAN_MAX_FWD_RATIO)
    # 今年度 EPS 本身失真：跟 stockanalysis Forward PE 反推的 EPS 差兩倍以上，多半是一次性
    # 損益（DUOL 稅務利益、MRK 併購研發費用、SRPT）。乘上什麼 PE 都不會準。
    px = ratio_data.get("current_price")
    if eps_current > 0 and pe_fwd and pe_fwd > 0 and px:
        r = eps_current * pe_fwd / px
        if not (1 / EPS_CONSISTENCY_MAX <= r <= EPS_CONSISTENCY_MAX):
            distorted = True
    pe_ok = priceable and not distorted
    # 算不出來存 None 不存 0：AI 分析／投資問答用 `is not None` 判斷，0 會變成「合理價 $0」，
    # 個股總覽列則會算出 -100% 的上漲空間
    pe_median_price = round(pe_med * eps_current) if pe_med and eps_current > 0 and pe_ok else None
    next_year_pe_median_price = round(pe_med * eps_next) if pe_med and eps_next > 0 and pe_ok else None

    current_price = ratio_data.get("current_price") or 0

    mc = ratio_data.get("market_cap")
    if mc:
        if mc >= 1e12:
            market_cap_display = f"{round(mc / 1e12, 2)}T"
        else:
            market_cap_display = f"{round(mc / 1e9, 2)}B"
    else:
        market_cap_display = "N/A"

    valuation_status = "N/A"
    valuation_diff_pct = None
    if current_price and pe_median_price:
        valuation_status = "overvalued" if current_price > pe_median_price else "undervalued"
        valuation_diff_pct = round(
            (pe_median_price - current_price) / pe_median_price * 100, 1
        )

    next_year_valuation_status = "N/A"
    next_year_valuation_diff_pct = None
    if current_price and next_year_pe_median_price:
        next_year_valuation_status = (
            "overvalued" if current_price > next_year_pe_median_price else "undervalued"
        )
        next_year_valuation_diff_pct = round(
            (next_year_pe_median_price - current_price)
            / next_year_pe_median_price
            * 100,
            1,
        )

    return {
        "estimated_pe": estimated_pe,
        "current_year_eps": eps_current,
        "current_year_fair_price": current_year_fair,
        "next_year_eps": eps_next,
        "next_year_fair_price": next_year_fair,
        "pe_median_5y": pe_med,
        "pe_median_price": pe_median_price,
        "next_year_pe_median_price": next_year_pe_median_price,
        "current_price": current_price,
        "market_cap_display": market_cap_display,
        "valuation_status": valuation_status,
        "valuation_diff_pct": valuation_diff_pct,
        "next_year_valuation_status": next_year_valuation_status,
        "next_year_valuation_diff_pct": next_year_valuation_diff_pct,
        "revenue_growth_5y": ratio_data.get("revenue_5y"),
        "eps_growth_5y": eps_5y,
        "eps_growth_past": past_growth,
    }


def _live_quotes(tickers: list[str]) -> dict:
    """批次即時報價。整個掛掉就回空 dict，呼叫端會退回 Metric 上的爬蟲價。"""
    try:
        from src.quotes import get_quotes
        return get_quotes(tickers)
    except Exception as exc:
        print(f"[DB] 即時報價取得失敗，退回比率表的收盤價：{exc}")
        return {}


def _apply_live_price(metric, quote) -> tuple:
    """(現價, 市值)。有報價就用報價，並把市值按比例縮放 ——
    市值留著舊價算出來的數字，會跟旁邊的現價對不起來。"""
    px, mc = metric.current_price, metric.market_cap
    live = (quote or {}).get("price")
    if live and live > 0:
        if mc and px and px > 0:
            mc = mc * live / px
        px = live
    return px, mc


def compute_valuations_for_date(snapshot_date: date, fiscal_year: int = 2025):
    """
    Compute valuations for all stocks for the given snapshot date.
    Should be called after all scrapers have finished.
    """
    ensure_tables()
    db = SessionLocal()
    try:
        print(f"[DB] Computing valuations for {snapshot_date}...")
        
        # Get all metrics for this date
        metrics_list = db.query(Metric).filter_by(snapshot_date=snapshot_date).all()

        # 價格改用即時報價，理由同 recompute_all_valuations
        _tk = {m.stock_id: db.get(Stock, m.stock_id) for m in metrics_list}
        live = _live_quotes([s.ticker for s in _tk.values() if s])

        count = 0
        for metric in metrics_list:
            stock = db.get(Stock, metric.stock_id)
            if not stock:
                continue
            
            # Get forecast (closest at or before this date)
            forecast = (
                db.query(Forecast)
                .filter(Forecast.stock_id == stock.id, Forecast.snapshot_date <= snapshot_date)
                .order_by(Forecast.snapshot_date.desc())
                .first()
            )
            
            # Get PE median (closest at or before this date)
            pe_rec = (
                db.query(PeMedian)
                .filter(PeMedian.stock_id == stock.id, PeMedian.snapshot_date <= snapshot_date)
                .order_by(PeMedian.snapshot_date.desc())
                .first()
            )
            
            if not forecast:
                continue
            
            forecast_annual = {
                "current_eps": forecast.current_eps,
                "current_growth": forecast.current_eps_growth,
                "next_year_eps": forecast.next_year_eps,
                "next_year_growth": forecast.next_year_eps_growth,
            }

            px, mc = _apply_live_price(metric, live.get(stock.ticker.upper()))
            ratio_dict = {
                "eps_5y": metric.eps_5y,
                "revenue_5y": metric.revenue_5y,
                "current_price": px,
                "market_cap": mc,
                "pe_forward": metric.pe_forward,
                "eps_usd_factor": eps_usd_factor(db, stock.ticker, forecast_annual, metric, snapshot_date),
            }
            
            pe_median_val = pe_rec.pe_median if pe_rec else None
            
            val = compute_valuation(forecast_annual, ratio_dict, pe_median_val, fiscal_year)
            
            existing = (
                db.query(Valuation)
                .filter_by(stock_id=stock.id, snapshot_date=snapshot_date)
                .first()
            )
            if existing:
                for k, v in val.items():
                    setattr(existing, k, v)
                existing.fiscal_year = fiscal_year
            else:
                db.add(
                    Valuation(
                        stock_id=stock.id,
                        snapshot_date=snapshot_date,
                        fiscal_year=fiscal_year,
                        **val,
                    )
                )
            count += 1
        
        db.commit()
        print(f"[DB] Computed {count} valuations for {snapshot_date}")
    except Exception as e:
        db.rollback()
        print(f"[DB] Error computing valuations: {e}")
    finally:
        db.close()


def recompute_all_valuations(snapshot_date: date, fiscal_year: int = 2025):
    """
    Recompute valuations for EVERY stock using each stock's *latest* metrics,
    forecast, and PE median at or before ``snapshot_date``, writing a fresh
    valuation row dated ``snapshot_date``.

    Unlike :func:`compute_valuations_for_date` — which only covers stocks that
    were scraped on exactly ``snapshot_date`` — this keeps every ticker's
    over/under verdict current even when only its forecast or PE was refreshed
    (e.g. ADI showed a stale "overvalued" because its 06-01 valuation kept using
    an old EPS after the forecast was later revised up).

    價格用即時報價，不用 Metric 上那個「最後一次爬到的收盤價」。那欄來自每週一次的
    比率表爬蟲（stockanalysis 的 Last Close Price），最舊會停一整週 —— 這支每天跑、
    而且會覆寫當天整批估值列，所以只要它還讀 Metric，前面不管誰把價格更新成正確的，
    都會被這裡蓋回去（2026-09-23 查到的就是這條路徑）。抓不到報價的才退回 Metric。
    """
    ensure_tables()
    db = SessionLocal()
    try:
        print(f"[DB] Recomputing all valuations for {snapshot_date}...")
        stocks = db.query(Stock).all()
        live = _live_quotes([s.ticker for s in stocks])
        count = 0
        skipped = 0
        for stock in stocks:
            metric = (
                db.query(Metric)
                .filter(Metric.stock_id == stock.id, Metric.snapshot_date <= snapshot_date)
                .order_by(Metric.snapshot_date.desc())
                .first()
            )
            forecast = (
                db.query(Forecast)
                .filter(Forecast.stock_id == stock.id, Forecast.snapshot_date <= snapshot_date)
                .order_by(Forecast.snapshot_date.desc())
                .first()
            )
            # Need both a price/market-cap snapshot and a forecast to value it.
            if not metric or not forecast:
                skipped += 1
                continue

            pe_rec = (
                db.query(PeMedian)
                .filter(PeMedian.stock_id == stock.id, PeMedian.snapshot_date <= snapshot_date)
                .order_by(PeMedian.snapshot_date.desc())
                .first()
            )

            forecast_annual = {
                "current_eps": forecast.current_eps,
                "current_growth": forecast.current_eps_growth,
                "next_year_eps": forecast.next_year_eps,
                "next_year_growth": forecast.next_year_eps_growth,
            }
            px, mc = _apply_live_price(metric, live.get(stock.ticker.upper()))
            ratio_dict = {
                "eps_5y": metric.eps_5y,
                "revenue_5y": metric.revenue_5y,
                "current_price": px,
                "market_cap": mc,
                "pe_forward": metric.pe_forward,
                "eps_usd_factor": eps_usd_factor(db, stock.ticker, forecast_annual, metric, snapshot_date),
            }
            pe_median_val = pe_rec.pe_median if pe_rec else None

            val = compute_valuation(forecast_annual, ratio_dict, pe_median_val, fiscal_year)

            existing = (
                db.query(Valuation)
                .filter_by(stock_id=stock.id, snapshot_date=snapshot_date)
                .first()
            )
            if existing:
                for k, v in val.items():
                    setattr(existing, k, v)
                existing.fiscal_year = fiscal_year
            else:
                db.add(
                    Valuation(
                        stock_id=stock.id,
                        snapshot_date=snapshot_date,
                        fiscal_year=fiscal_year,
                        **val,
                    )
                )
            count += 1

        db.commit()
        print(f"[DB] Recomputed {count} valuations for {snapshot_date} (skipped {skipped})")
    except Exception as e:
        db.rollback()
        print(f"[DB] Error recomputing valuations: {e}")
    finally:
        db.close()


# metrics 只吃 ratios 頁的「Current」欄，但那張表常常缺列：歷年全虧損、只有 TTM 轉正的
# 公司（BE、MDB、SYM…）整列 PE Ratio 不會出現；EPS／淨利／營收則根本不在那張表上。
# statistics 的 JSON 端點 id 跟 RATIO_KEY_MAP 的 key 是同一套，所以拿它來補空欄。
# 只補 None，不覆寫 ratios 已有的值；日期差太多就不補（避免把新數字塞進很舊的快照）。
METRICS_FILL_MAX_GAP_DAYS = 7


def _fill_metrics_gaps(db, stock_id: int, data: dict, snapshot_date: date) -> int:
    """Fill NULL columns of the stock's latest metrics row from a statistics payload."""
    row = (
        db.query(Metric)
        .filter(Metric.stock_id == stock_id)
        .order_by(Metric.snapshot_date.desc())
        .first()
    )
    if row is None or abs((snapshot_date - row.snapshot_date).days) > METRICS_FILL_MAX_GAP_DAYS:
        return 0

    by_id = {}
    for section in data.get("sections", []):
        for item in section.get("items", []):
            value = item.get("value")
            if item.get("id") and isinstance(value, (int, float)) and not isinstance(value, bool):
                by_id[item["id"]] = value

    filled = 0
    for json_key, db_col in RATIO_KEY_MAP.items():
        if json_key in by_id and getattr(row, db_col) is None:
            setattr(row, db_col, by_id[json_key])
            filled += 1
    return filled


def backfill_metrics_from_statistics() -> dict:
    """One-off / on-demand: run _fill_metrics_gaps for every stock's latest statistics."""
    ensure_tables()
    db = SessionLocal()
    stocks = touched = cols = 0
    try:
        latest = (
            db.query(Statistic.stock_id, func.max(Statistic.snapshot_date))
            .filter(Statistic.metric_id.isnot(None))
            .group_by(Statistic.stock_id)
            .all()
        )
        for stock_id, snap in latest:
            items = [
                {"id": s.metric_id, "value": s.value}
                for s in db.query(Statistic).filter_by(stock_id=stock_id, snapshot_date=snap)
            ]
            n = _fill_metrics_gaps(db, stock_id, {"sections": [{"items": items}]}, snap)
            stocks += 1
            if n:
                touched += 1
                cols += n
        db.commit()
    finally:
        db.close()
    return {"stocks": stocks, "rows_touched": touched, "columns_filled": cols}


def save_statistics(ticker: str, data: dict, snapshot_date: date):
    """
    Save statistics data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        data: Dictionary with 'sections' key containing list of sections with items
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter_by(ticker=ticker.upper()).first()
        if not stock:
            print(f"  [DB] Stock {ticker} not found, skipping statistics save")
            return
        
        # Delete existing statistics for this stock and date
        db.query(Statistic).filter_by(
            stock_id=stock.id, snapshot_date=snapshot_date
        ).delete()
        
        # Insert new statistics
        count = 0
        for section in data.get("sections", []):
            section_name = section.get("name", "Unknown")
            for item in section.get("items", []):
                label = item.get("label")
                value = item.get("value")
                display = item.get("display")
                
                if label:
                    # Only save numeric values
                    numeric_value = value if isinstance(value, (int, float)) else None
                    db.add(Statistic(
                        stock_id=stock.id,
                        snapshot_date=snapshot_date,
                        section=section_name,
                        label=label,
                        metric_id=item.get("id"),   # JSON 端點才有；爬 HTML 的是 None
                        value=numeric_value,
                        display=display,
                    ))
                    count += 1

        filled = _fill_metrics_gaps(db, stock.id, data, snapshot_date)

        db.commit()
        print(f"  [DB] Saved {count} statistics for {ticker}"
              + (f"（補 metrics {filled} 欄）" if filled else ""))
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving statistics for {ticker}: {e}")
    finally:
        db.close()


def save_metrics_breakdown(ticker: str, df, snapshot_date: date):
    """
    Save metrics breakdown data for a single stock to the database.
    
    Args:
        ticker: Stock ticker symbol
        df: pandas DataFrame with columns [Category, Metric, period_1, period_2, ...]
        snapshot_date: Date of the snapshot
    """
    ensure_tables()
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter_by(ticker=ticker.upper()).first()
        if not stock:
            print(f"  [DB] Stock {ticker} not found, skipping metrics breakdown save")
            return
        
        # Delete existing metrics breakdown for this stock and date
        db.query(MetricsBreakdown).filter_by(
            stock_id=stock.id, snapshot_date=snapshot_date
        ).delete()
        
        # Get period columns (all columns except Category and Metric)
        meta_cols = ["Category", "Metric"]
        period_cols = [c for c in df.columns if c not in meta_cols]
        
        # Insert new metrics breakdown
        count = 0
        for _, row in df.iterrows():
            category = row.get("Category", "Unknown")
            metric = row.get("Metric", "Unknown")
            
            for period in period_cols:
                value = row.get(period)
                if value is not None and isinstance(value, (int, float)):
                    db.add(MetricsBreakdown(
                        stock_id=stock.id,
                        snapshot_date=snapshot_date,
                        category=category,
                        metric=metric,
                        period=period,
                        value=value,
                    ))
                    count += 1
        
        db.commit()
        print(f"  [DB] Saved {count} metrics breakdown entries for {ticker}")
    except Exception as e:
        db.rollback()
        print(f"  [DB] Error saving metrics breakdown for {ticker}: {e}")
    finally:
        db.close()
