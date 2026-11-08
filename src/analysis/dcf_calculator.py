"""
DCF (Discounted Cash Flow) valuation calculator.

Reads every fundamental source StockRadar scrapes (see :mod:`dcf_inputs`),
projects free cash flow for five years, discounts it at a build-up WACC and
derives an intrinsic value per share under three scenarios.

What the model does that a spot-value DCF does not:

  * **Revenue is anchored to analyst *levels*, not growth applied to TTM.**
    Analyst growth rates are fiscal-year over fiscal-year; the old model applied
    them to a TTM revenue base, which for a fast-growing company double-counted
    part of the year already in the trailing figure.  Forecast revenue levels
    for years 1-3 are used directly when available.
  * **Three forecast years, not two.**  ``forecast_ranges`` carries FY+1..FY+3.
  * **FCF margin fades from spot to a normalised level** derived from five years
    of cash-flow history, instead of holding one spot margin flat forever.
  * **Scenario width is derived from the data** — analyst dispersion and the
    historical volatility of the FCF margin — rather than fixed ±1 % / ±2 pp.
  * **WACC is built up** from a shrunk beta, a size premium and an after-tax
    cost of debt implied by actual interest expense, then blended with the
    scraped WACC instead of trusting it outright.
  * **Share count drifts** at its historical rate, so buybacks add and SBC
    dilution subtracts per-share value.
  * **Terminal value is cross-checked** against the company's own median
    historical EV/FCF multiple.
  * **Reverse DCF** reports the revenue CAGR the current price implies, next to
    what analysts actually forecast.
  * **Confidence score** grades how much of the answer rests on real data.

Scenarios:
  - Optimistic:   high analyst path, WACC −δ, terminal growth 3.0 %, margin +σ
  - Moderate:     consensus path,    base WACC, terminal growth 2.5 %, base margin
  - Pessimistic:  low analyst path,  WACC +δ, terminal growth 2.0 %, margin −σ

δ scales with the WACC-to-terminal-growth spread (0.6–1.2 pp) and σ with the
company's own historical FCF-margin volatility, so neither band is a fixed guess.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from src import DATA_DIR
from src.analysis.dcf_inputs import (
    FundamentalBundle,
    clamp,
    dispersion,
    median_of,
    weighted_recent,
    winsorize,
)

# ── Constants ──────────────────────────────────────────────────────────────
# The three macro inputs move every valuation in the universe together, so they
# are env-overridable — retune them in one place rather than editing the model.
RISK_FREE_RATE = float(os.getenv("DCF_RISK_FREE_RATE", "0.0425"))          # 4.25 %
EQUITY_RISK_PREMIUM = float(os.getenv("DCF_EQUITY_RISK_PREMIUM", "0.055")) # 5.50 %
TERMINAL_GROWTH = float(os.getenv("DCF_TERMINAL_GROWTH", "0.025"))         # 2.50 %
PROJECTION_YEARS = 5
MIN_WACC = 0.06               # floor — below this the terminal value explodes
MAX_WACC = 0.16               # cap  — a scraped beta of 2.5+ is noise, not risk
MIN_WACC_GROWTH_SPREAD = 0.02 # WACC must exceed terminal growth by at least this

BETA_SHRINK = 0.67            # Blume: beta_adj = 0.67*beta + 0.33*1.0
BETA_FLOOR, BETA_CAP = 0.6, 1.8

# Size premium by market cap (Ibbotson-style, coarse).
SIZE_PREMIUM_BANDS = [
    (10e9, 0.000),
    (2e9, 0.010),
    (0.0, 0.020),
]

# Scenario offsets. ``fcf_margin_sigma`` multiplies the *measured* historical
# volatility of the FCF margin, so a steady compounder gets a narrow band and a
# cyclical gets a wide one.
SCENARIO_PARAMS = {
    "optimistic": {
        "wacc_offset_sign": -1,
        "terminal_growth": 0.03,
        "fcf_margin_sigma": +1.0,
        "revenue_path": "high",
    },
    "moderate": {
        "wacc_offset_sign": 0,
        "terminal_growth": 0.025,
        "fcf_margin_sigma": 0.0,
        "revenue_path": "avg",
    },
    "pessimistic": {
        "wacc_offset_sign": +1,
        "terminal_growth": 0.02,
        "fcf_margin_sigma": -1.0,
        "revenue_path": "low",
    },
}

# 折現率的情境偏移：利差的 15%，落在 0.6–1.2 個百分點之間
WACC_OFFSET_SPREAD_RATIO = 0.15
WACC_OFFSET_MIN = 0.006
WACC_OFFSET_MAX = 0.012

# 終期利潤率裡「目前值」的權重。壓到 0.15 之後，最近四季的有效權重約 30%
# ——上一版是 58%，等於用一年的數字決定永續期。
TERMINAL_SPOT_WEIGHT = 0.15

# 終期利潤率的推導版本。改動這裡之前先跑 scripts/backtest_dcf.py。
TERMINAL_VARIANTS = ("legacy", "recency", "ensemble", "owner")
TERMINAL_VARIANT_DEFAULT = "owner"

MARGIN_OFFSET_MIN_PP = 1.5    # scenario margin band never narrower than this
MARGIN_OFFSET_MAX_PP = 8.0    # …nor wider
# 資本支出超過折舊這麼多個百分點時，觸發「資本支出正常化」交叉驗證
CAPEX_CYCLE_GAP_PP = 3.0

MARGIN_FLOOR_PCT = 1.0        # 終期利潤率的地板（永續期不該假設永遠燒錢）
MARGIN_PATH_FLOOR_PCT = -15.0 # 明確預測期可以是負的 —— 折現算得出來，夾成正的才是說謊
MARGIN_CAP_PCT = 60.0

SHARE_DRIFT_FLOOR = -4.0      # % per year — cap extrapolated buybacks
SHARE_DRIFT_CAP = 6.0         # …and extrapolated dilution

EXIT_MULTIPLE_FLOOR = 10.0    # EV/FCF band for the terminal cross-check
EXIT_MULTIPLE_CAP = 30.0

_SRC_ZH = {
    "statistics": "統計快照",
    "forecast": "分析師預估",
    "income": "損益表",
    "cashflow": "現金流量表",
    "ratios": "財務比率表",
}

STALE_DAYS = {"statistics": 120, "forecast": 90, "income": 400, "cashflow": 400}



def _cagr(start: float | None, end: float | None, years: int) -> float | None:
    """Compound growth rate, or None when a fractional power would go complex.

    A stressed reverse-DCF path can drive projected revenue negative; raising a
    negative ratio to a fractional power yields a complex number that then blows
    up every downstream comparison.
    """
    if not start or start <= 0 or end is None or end <= 0:
        return None
    return (end / start) ** (1 / years) - 1



def _live_quote(ticker: str) -> dict | None:
    """最新報價（含 last_date 交易日）；Yahoo 掛掉或已下市回 None，呼叫端退回爬蟲的價。"""
    try:
        from src.quotes import get_quotes
        return get_quotes([ticker]).get(ticker.upper())
    except Exception:
        return None


class DCFCalculator:
    """Compute a DCF valuation for any ticker with statistics + forecast data."""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        as_of: date | None = None,
        terminal_variant: str = TERMINAL_VARIANT_DEFAULT,
    ):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        # as_of 讓模型只讀取該日期以前就存在的檔案 —— 回測用，正式跑時是 None
        self.as_of = as_of
        # 終期利潤率的推導方式。可選版本見 TERMINAL_VARIANTS —— 存在的理由是
        # scripts/backtest_dcf.py 要在同一份 point-in-time 資料上公平比較它們。
        if terminal_variant not in TERMINAL_VARIANTS:
            raise ValueError(f"unknown terminal_variant: {terminal_variant}")
        self.terminal_variant = terminal_variant

    # ══════════════════════════════════════════════════════════════════════
    #  Input assembly
    # ══════════════════════════════════════════════════════════════════════

    def _build_inputs(self, ticker: str) -> dict | None:
        b = FundamentalBundle(ticker, self.data_dir, as_of=self.as_of)
        if not b.statistics:
            return None

        inputs: dict = {
            "ticker": b.ticker,
            "data_date": b.statistics.get("date"),
            "sources": b.sources_used(),
            "source_age_days": b.age_days(),
        }
        warnings: list[str] = []

        # ── spot fundamentals ──────────────────────────────────────────────
        inputs["fcf"] = b.stat("Cash Flow", "Free Cash Flow")
        inputs["revenue"] = b.stat("Income Statement", "Revenue")
        inputs["ebitda"] = b.stat("Income Statement", "EBITDA")
        inputs["shares_outstanding"] = b.stat("Share Statistics", "Shares Outstanding")
        inputs["market_cap"] = b.stat("Total Valuation", "Market Cap")
        inputs["enterprise_value"] = b.stat("Total Valuation", "Enterprise Value")

        # Label drift: the scraper has emitted both spellings over time.
        cash = b.stat_any(
            "Balance Sheet", "Cash & Cash Equivalents", "Cash & Marketable Securities"
        )
        debt = b.stat("Balance Sheet", "Total Debt")
        net_cash = b.stat("Balance Sheet", "Net Cash")
        inputs["cash"] = cash
        inputs["total_debt"] = debt
        inputs["net_cash"] = net_cash if net_cash is not None else (cash or 0) - (debt or 0)

        inputs.update(self._resolve_price_and_shares(b, inputs, warnings))

        # ── discount rate ──────────────────────────────────────────────────
        inputs.update(self._build_wacc(b, inputs, warnings))

        # ── revenue path ───────────────────────────────────────────────────
        inputs["revenue_paths"] = self._build_revenue_paths(b, inputs, warnings)

        # ── capex profile ──────────────────────────────────────────────────
        # 要放在利潤率之前：終期利潤率的第四種讀法（資本支出正常化）會用到它。
        inputs["capex_profile"] = b.capex_profile()

        # ── margin profile ─────────────────────────────────────────────────
        inputs.update(self._build_margin_profile(b, inputs, warnings))

        # ── share count drift ──────────────────────────────────────────────
        drift = b.share_change_pct()
        if drift is None:
            inputs["share_drift_pct"] = 0.0
            inputs["share_drift_source"] = "none"
        else:
            inputs["share_drift_pct"] = clamp(drift, SHARE_DRIFT_FLOOR, SHARE_DRIFT_CAP)
            inputs["share_drift_source"] = "income statement (diluted, 5y weighted)"

        # ── context for cross-checks & confidence ──────────────────────────
        inputs["sbc_pct_revenue"] = b.sbc_pct_revenue()
        inputs["capex_pct_revenue"] = b.capex_pct_revenue()
        inputs["exit_multiples"] = b.exit_multiples()
        inputs["coverage"] = b.analyst_coverage()
        inputs["price_target"] = b.price_target()
        inputs["altman_z"] = b.stat("Scores", "Altman Z-Score")
        inputs["piotroski_f"] = b.stat("Scores", "Piotroski F-Score")
        inputs["interest_coverage"] = b.stat("Financial Position", "Interest Coverage")
        # 回測（scripts/backtest_dcf.py）顯示：DCF 的上檔空間與自由現金流殖利率
        # 的排序相關高達 0.80，而且抽掉殖利率之後 DCF 幾乎不剩增量資訊。所以把
        # 殖利率一併輸出，讓頁面誠實地把基準擺在旁邊，而不是只秀自己的數字。
        inputs["fcf_yield"] = b.stat("Dividends & Yields", "FCF Yield")
        inputs["earnings_yield"] = b.stat("Dividends & Yields", "Earnings Yield")
        inputs["lynch_fair_value"] = b.stat("Fair Value", "Lynch Fair Value")
        inputs["graham_number"] = b.stat("Fair Value", "Graham Number")

        # ── data-quality warnings ──────────────────────────────────────────
        for src, limit in STALE_DAYS.items():
            age = inputs["source_age_days"].get(src)
            if age is not None and age > limit:
                warnings.append(f"{_SRC_ZH.get(src, src)}資料已經 {age} 天沒更新（門檻 {limit} 天）—— 需要重跑爬蟲")
            elif age is None and src in ("income", "cashflow"):
                warnings.append(
                    f"沒有抓到{_SRC_ZH.get(src, src)}，利潤率無法常態化，只能退回單點數值"
                )
        if inputs["fcf"] is not None and inputs["fcf"] < 0:
            # 擴產投資造成的負現金流，適用性區塊已經解釋過了，這裡再喊一次
            # 「不是正確角度」反而跟那段互相矛盾。
            gap = (inputs.get("capex_profile") or {}).get("growth_capex_pct")
            if gap is None or gap < CAPEX_CYCLE_GAP_PP:
                warnings.append("最近四季自由現金流為負 —— 現金流折現不是看這家公司的正確角度")
        sbc = inputs["sbc_pct_revenue"]
        if sbc is not None and sbc > 8:
            warnings.append(
                f"股權薪酬佔營收 {sbc:.1f}% —— 帳列自由現金流高估了股東真正拿得到的錢"
            )

        inputs["warnings"] = warnings
        return inputs

    # ── price & share count ────────────────────────────────────────────────

    def _resolve_price_and_shares(
        self, b: FundamentalBundle, inputs: dict, warnings: list[str]
    ) -> dict:
        """Pin the valuation to the freshest price the scrapers hold.

        The old model derived price as market cap / shares out of the statistics
        snapshot, which is refreshed monthly and — for an ADR like TSM, where the
        two figures are quoted on different share bases — wrong by a factor of
        five.  The ratios CSV carries a `Last Close Price` that is usually a day
        or two old; when we have both a fresh price and a fresh market cap the
        implied share count is internally consistent and self-corrects the ADR
        case.
        """
        stat_mc = inputs.get("market_cap")
        stat_shares = inputs.get("shares_outstanding")
        stat_price = stat_mc / stat_shares if stat_mc and stat_shares else None

        ratios_age = (b.age_days() or {}).get("ratios")
        fresh = ratios_age is not None and ratios_age <= 45
        last_close = b.ratios.latest("Last Close Price", scale=False) if fresh else None
        fresh_mc = b.ratios.latest("Market Capitalization") if fresh else None

        if last_close and last_close > 0:
            price, price_source = last_close, f"財務比率表最新收盤價（{ratios_age} 天前）"
            market_cap = fresh_mc or stat_mc
            shares = market_cap / price if market_cap else stat_shares
            if stat_shares and shares and abs(shares / stat_shares - 1) > 0.15:
                warnings.append(
                    f"流通股數由 {stat_shares:,.0f} 調整為 {shares:,.0f}，讓市值與實際成交價一致"
                    f"（ADR 或快照過期造成的落差）"
                )
        else:
            price, price_source = stat_price, "統計快照的市值 ÷ 流通股數"
            market_cap, shares = stat_mc, stat_shares
            if ratios_age is None:
                warnings.append("沒有財務比率表，股價只能從月更快照推算")

        # 上面先用同一輪爬蟲的（收盤價、市值）配對把流通股數推出來 —— 這兩個數字
        # 必須同源，才會自動修掉 ADR 的股數基準差。股數定了之後，價格本身換成真
        # 報價：比率表那欄是「Last Close Price」，爬蟲一週才跑一次而且排在美股盤中，
        # 最好的情況也落後一個交易日。上漲空間、三情境估值軸、反推估值全都拿
        # current_price 去比，用舊價等於整組結論跟著偏。
        price_date = None
        quote = _live_quote(b.ticker)
        live = (quote or {}).get("price")
        if live and live > 0:
            if shares:
                market_cap = shares * live      # 讓市值跟著價走，內部才不會自相矛盾
            price, price_source = live, "即時報價（最新收盤）"
            price_date = quote.get("last_date")

        return {
            "current_price": price,
            "price_source": price_source,
            "price_date": price_date,           # 這個價屬於哪一盤，給快取失效判斷用
            "market_cap": market_cap,
            "shares_outstanding": shares,
        }

    # ── WACC build-up ──────────────────────────────────────────────────────

    def _build_wacc(self, b: FundamentalBundle, inputs: dict, warnings: list[str]) -> dict:
        """Build a WACC from components and reconcile it with the scraped one.

        The scraped WACC is a raw CAPM on a raw 5-year beta, which for a
        high-momentum name lands near 17 % and quietly buries the valuation.
        We shrink beta toward 1 (Blume), add a size premium, and blend in an
        after-tax cost of debt implied by real interest expense.
        """
        beta_raw = b.stat("Stock Price Statistics", "Beta (5Y)")
        tax_pct = b.effective_tax_rate()
        tax = (tax_pct or 21.0) / 100.0

        beta_adj = (
            clamp(BETA_SHRINK * beta_raw + (1 - BETA_SHRINK), BETA_FLOOR, BETA_CAP)
            if beta_raw is not None
            else 1.0
        )

        mc = inputs.get("market_cap") or 0
        size_premium = next(p for floor, p in SIZE_PREMIUM_BANDS if mc >= floor)

        # Financial-risk premium: a levered company's observed beta is often too
        # low (thin trading, a quiet year), and shrinking it toward 1 flatters it
        # further.  Charge equity holders for the leverage they actually carry.
        net_debt = -(inputs.get("net_cash") or 0)
        leverage = net_debt / mc if mc and net_debt > 0 else 0.0
        leverage_premium = clamp(0.02 * leverage, 0.0, 0.04)

        cost_of_equity = (
            RISK_FREE_RATE + beta_adj * EQUITY_RISK_PREMIUM + size_premium + leverage_premium
        )

        cod_pct = b.cost_of_debt()
        if cod_pct is None:
            # No usable interest expense: price debt off coverage quality.
            cov = b.stat("Financial Position", "Interest Coverage")
            spread = 0.01 if (cov or 0) > 10 else 0.025 if (cov or 0) > 3 else 0.05
            cost_of_debt = RISK_FREE_RATE + spread
            cod_source = "由利息保障倍數推算的信用利差"
        else:
            cost_of_debt = cod_pct / 100.0
            cod_source = "利息支出 ÷ 總負債"
        after_tax_cod = cost_of_debt * (1 - tax)

        debt = inputs.get("total_debt") or 0
        equity = mc or 0
        total = debt + equity
        w_d = debt / total if total else 0.0
        w_e = 1 - w_d

        wacc_model = w_e * cost_of_equity + w_d * after_tax_cod

        wacc_source_pct = b.stat(
            "Financial Efficiency", "Weighted Average Cost of Capital (WACC)"
        )
        if wacc_source_pct is not None:
            wacc = 0.5 * wacc_model + 0.5 * (wacc_source_pct / 100.0)
            source = "自行拆解重算與資料源數值取平均"
        else:
            wacc = wacc_model
            source = "自行拆解重算"
            if beta_raw is None:
                warnings.append("查不到貝他係數，股權成本以貝他 = 1.0 計算")

        clamped = clamp(wacc, MIN_WACC, MAX_WACC)
        if abs(clamped - wacc) > 1e-9:
            warnings.append(
                f"折現率由 {wacc:.2%} 收斂到 {clamped:.2%}（允許區間 {MIN_WACC:.0%}–{MAX_WACC:.0%}）"
            )

        return {
            "wacc": clamped,
            "wacc_source": source,
            "wacc_components": {
                "beta_raw": beta_raw,
                "beta_adjusted": round(beta_adj, 3),
                "risk_free_rate": round(RISK_FREE_RATE * 100, 2),
                "equity_risk_premium": round(EQUITY_RISK_PREMIUM * 100, 2),
                "size_premium": round(size_premium * 100, 2),
                "leverage_premium": round(leverage_premium * 100, 2),
                "net_debt_to_market_cap": round(leverage, 2),
                "cost_of_equity": round(cost_of_equity * 100, 2),
                "cost_of_debt": round(cost_of_debt * 100, 2),
                "after_tax_cost_of_debt": round(after_tax_cod * 100, 2),
                "cost_of_debt_source": cod_source,
                "weight_equity": round(w_e * 100, 1),
                "weight_debt": round(w_d * 100, 1),
                "wacc_build_up": round(wacc_model * 100, 2),
                "wacc_scraped": wacc_source_pct,
            },
            "beta": beta_raw,
            "tax_rate": tax_pct,
        }

    # ── revenue paths ──────────────────────────────────────────────────────

    def _build_revenue_paths(
        self, b: FundamentalBundle, inputs: dict, warnings: list[str]
    ) -> dict:
        """Absolute revenue levels for years 1-3 under each analyst path.

        Analyst growth is fiscal-year over fiscal-year.  Compounding it onto a
        TTM revenue base overstates the level whenever the trailing period
        already contains part of the growth — for a company mid-ramp the error
        is easily 15 %.  Where the scraper captured absolute forecast revenue we
        use it directly and only fall back to growth-on-TTM when it did not.
        """
        fc = b.forecast or {}
        ranges = (fc.get("forecast_ranges") or {}).get("revenue") or {}
        growth_ranges = (fc.get("forecast_ranges") or {}).get("revenue_growth") or {}
        annual_hist = fc.get("annual_forecast") or {}

        ttm_revenue = inputs.get("revenue")
        years = ranges.get("years") or []
        levels = {k: (ranges.get(k) or []) for k in ("avg", "high", "low")}
        growths = {k: (growth_ranges.get(k) or []) for k in ("avg", "high", "low")}

        # Base = the last *actual* fiscal year, so the level series stays on one
        # fiscal calendar with the forecasts.
        base_revenue, base_period = None, None
        if years and annual_hist.get("periods"):
            try:
                first_fc_year = int(str(years[0]))
            except (TypeError, ValueError):
                first_fc_year = None
            if first_fc_year:
                for period, rev in zip(annual_hist["periods"], annual_hist.get("revenue") or []):
                    digits = "".join(ch for ch in str(period) if ch.isdigit())
                    if digits and int(digits) < first_fc_year and rev:
                        base_revenue, base_period = rev, period

        has_levels = bool(years) and any(v for v in levels["avg"])
        if has_levels and base_revenue:
            source = "分析師預估的營收金額（對齊會計年度）"
        elif has_levels:
            base_revenue, base_period = ttm_revenue, "TTM"
            source = "分析師預估的營收金額（以最近四季為基期 —— 找不到實際會計年度）"
        else:
            base_revenue, base_period = ttm_revenue, "TTM"
            source = "成長率套用於最近四季營收"
            warnings.append(
                "分析師沒有給營收金額，只能退回「成長率乘最近四季營收」的算法"
            )

        if not base_revenue:
            return {"source": source, "base_revenue": None, "paths": {}}

        # Reporting-currency reconciliation.  Forecast revenue is scraped in the
        # company's reporting currency while market cap, debt and shares come
        # from the USD-quoted statistics page — TSM forecasts in TWD against a
        # USD market cap, a ~30x error that flows straight into value per share.
        # Rebase the level path onto the statistics revenue and keep its shape.
        currency_scale = 1.0
        if ttm_revenue and base_revenue and ttm_revenue > 0:
            ratio = base_revenue / ttm_revenue
            if ratio > 2.5 or ratio < 0.4:
                currency_scale = 1 / ratio
                warnings.append(
                    f"預估營收是統計快照營收的 {ratio:.1f} 倍 —— 判定為不同記帳幣別，"
                    f"已改以美元口徑重新校準"
                )
                base_revenue *= currency_scale
                for key in ("avg", "high", "low"):
                    levels[key] = [
                        (v * currency_scale if v else v) for v in levels[key]
                    ]

        # Historical revenue CAGR — the fallback when analysts say nothing, and
        # a sanity anchor for the optimistic path.
        hist_growth = [g for g in (annual_hist.get("revenue_growth") or []) if g is not None]
        hist_recent = weighted_recent(list(reversed(hist_growth))[:5]) if hist_growth else None
        stat_growth = b.stat_any(
            "Analyst Forecast",
            "Revenue Growth Forecast (3Y)",
            "Revenue Growth Forecast (5Y)",
        )

        def build_path(key: str, consensus: list[float] | None) -> list[float]:
            """One analyst path (avg / high / low) as three absolute revenue levels.

            Resolution order per year, and the order matters: falling back to a
            path-independent level target (the 3Y CAGR) for high *and* low made
            all three converge on the same year-3 revenue, which — read off
            different year-2 bases — printed a pessimistic growth rate *above*
            the optimistic one. High and low therefore track the consensus step
            scaled by how far they have already diverged, and only the consensus
            path is allowed to reach for a level target of its own.
            """
            level_list = levels[key]
            growth_list = growths[key]
            path: list[float] = []
            prev = base_revenue
            last_growth = None

            for i in range(3):
                lvl = level_list[i] if i < len(level_list) else None
                if lvl:
                    last_growth = (float(lvl) / prev - 1) * 100 if prev else last_growth
                    prev = float(lvl)
                    path.append(prev)
                    continue

                # 1) this path's own analyst growth for the year
                g = growth_list[i] if i < len(growth_list) else None

                # 2) high / low: carry the consensus step, keeping the divergence
                if g is None and consensus is not None:
                    prev_c = consensus[i - 1] if i else base_revenue
                    if consensus[i] and prev_c:
                        prev = prev * (consensus[i] / prev_c)
                        last_growth = (consensus[i] / prev_c - 1) * 100
                        path.append(prev)
                        continue

                # 3) consensus growth for the year
                if g is None:
                    g = growths["avg"][i] if i < len(growths["avg"]) else None

                # 4) consensus only: the scraped "Revenue Growth Forecast (3Y)" is a
                #    *CAGR*, not a year-3 rate — years 1-2 have usually delivered
                #    most of it already, so read it as a level target rather than
                #    compounding it on top of what analysts have already booked.
                if g is None and consensus is None and stat_growth is not None and base_revenue:
                    target = base_revenue * (1 + stat_growth / 100.0) ** (i + 1)
                    if target > prev:
                        g = (target / prev - 1) * 100

                # 5) decay the last observed growth toward terminal — fading is a
                #    better prior than repeating the last print
                if g is None:
                    anchor = last_growth if last_growth is not None else hist_recent
                    if anchor is not None:
                        g = TERMINAL_GROWTH * 100 + (anchor - TERMINAL_GROWTH * 100) * 0.6

                if g is None:
                    g = TERMINAL_GROWTH * 100
                last_growth = g
                prev = prev * (1 + g / 100.0)
                path.append(prev)
            return path

        paths: dict[str, list[float]] = {"avg": build_path("avg", None)}
        paths["high"] = build_path("high", paths["avg"])
        paths["low"] = build_path("low", paths["avg"])

        # Order the paths — a stale range can leave low above high.
        for i in range(3):
            trio = sorted(p[i] for p in paths.values())
            paths["low"][i], paths["avg"][i], paths["high"][i] = trio

        if not any(levels["high"]) and not any(levels["low"]):
            warnings.append(
                "分析師沒有給營收預估的高低區間，情境寬度改用歷史成長波動度推算"
            )
            spread = dispersion(hist_growth[-5:]) if len(hist_growth) >= 2 else None
            k = clamp((spread or 15.0) / 100.0, 0.08, 0.45)
            for i in range(3):
                mid = paths["avg"][i]
                step = k * (i + 1) / 3
                paths["high"][i] = mid * (1 + step)
                paths["low"][i] = mid * (1 - step * 0.8)

        return {
            "source": source,
            "base_revenue": base_revenue,
            "base_period": base_period,
            "currency_scale": currency_scale,
            "forecast_years": years[:3],
            "paths": paths,
            "hist_growth_recent": hist_recent,
            "consensus_cagr_3y": _cagr(base_revenue, (paths.get("avg") or [None] * 3)[2], 3),
        }

    # ── margin profile ─────────────────────────────────────────────────────

    def _build_margin_profile(
        self, b: FundamentalBundle, inputs: dict, warnings: list[str]
    ) -> dict:
        """Spot margin, normalised margin, and a data-derived scenario band."""
        spot = b.stat("Margins", "FCF Margin")
        if spot is None and inputs.get("fcf") and inputs.get("revenue"):
            spot = inputs["fcf"] / inputs["revenue"] * 100.0

        history = b.fcf_margin_history()
        trimmed, n_trimmed = winsorize(history)

        # Reported FCF is levered; the value bridge already subtracts net debt.
        # Add interest back so leverage is charged exactly once.
        uplift = b.unlevered_fcf_uplift_pct() or 0.0
        if uplift > 0.05:
            spot = (spot + uplift) if spot is not None else None
            history = [h + uplift for h in history]
            trimmed = [h + uplift for h in trimmed]
        if uplift > 2.0:
            warnings.append(
                f"自由現金流利潤率上調 {uplift:.1f} 個百分點改採未扣息口徑"
                "（利息只在價值橋接扣淨負債時算一次，避免重複扣）"
            )
        normalised = weighted_recent(trimmed) if trimmed else None
        sigma = dispersion(history) if history else None

        if spot is None:
            spot = normalised
        if spot is None:
            warnings.append("任何資料源都查不到自由現金流利潤率")
            return {"margin_profile": None}

        # ── Terminal margin ────────────────────────────────────────────────
        # A terminal margin is a claim about year 6 to infinity, so no single
        # year may dominate it. The previous formulation
        #     terminal = 0.35*spot + 0.65*recency_weighted
        # gave the trailing twelve months an effective weight of 58 % (the last
        # two periods, 74 %) because spot was counted again inside the
        # recency-weighted mean. Any company in a cyclical trough was valued as
        # though the trough were permanent: Oracle's TTM margin of −29 % (an
        # AI-capex year) drove its terminal margin to the 1 % floor against a
        # six-year median of 20 %, and the DCF returned −$50 a share.
        #
        # The obvious fix — lean on the through-cycle median — breaks the
        # opposite case. Reddit went −28 % → +37 % in a straight line; that is a
        # business reaching scale, not a cycle, and its six-year median of
        # −12.8 % describes a company that no longer exists.
        #
        # Five or six annual points cannot reliably tell a trough from an
        # inflection, so the model does not pretend to. It computes three
        # readings of the same history and weights them equally:
        #
        #   recency-weighted mean  →  "where is it heading?"   (trend-sensitive)
        #   median of the last 3   →  "where has it settled?"  (level-sensitive)
        #   median of all years    →  "what is the norm?"      (cycle-sensitive)
        #
        # All three are reported, along with how far apart they are — a wide
        # spread is exactly the case where the terminal margin is a judgement
        # call, and the page should say so rather than show one confident number.
        if normalised is None:
            terminal = spot
            through_cycle = recent_level = owner_margin = None
            estimators: list[float] = []
            core = spot
            margin_source = "只有目前單點數值（沒有現金流量表歷史）"
        else:
            through_cycle = median_of(history)
            recent_level = median_of(history[:3]) if len(history) >= 3 else normalised

            # 第四種讀法：若資本支出回落到維持性水準，利潤率會是多少。
            # 只在資本支出明顯高於折舊時才納入 —— 否則它幾乎等於帳列利潤率，
            # 加進來只是把同一個數字算兩次。
            owner_margin = None
            cp = inputs.get("capex_profile") or {}
            gap = cp.get("growth_capex_pct")
            if (gap or 0) >= CAPEX_CYCLE_GAP_PP and cp.get("operating_cash_flow_pct") is not None:
                owner_margin = cp["operating_cash_flow_pct"] - cp["depreciation_pct"]

            if self.terminal_variant == "legacy":
                # 本次改版前的算法：即期值有效權重 58%
                estimators = [normalised]
                core = normalised
                terminal = 0.35 * spot + 0.65 * normalised
                label = "近期加權（舊版，即期權重 58%）"
            elif self.terminal_variant == "recency":
                estimators = [normalised]
                core = normalised
                terminal = TERMINAL_SPOT_WEIGHT * spot + (1 - TERMINAL_SPOT_WEIGHT) * core
                label = f"近期加權 {normalised:.1f}%"
            else:
                estimators = [normalised, recent_level, through_cycle]
                if self.terminal_variant == "owner" and owner_margin is not None:
                    estimators.append(clamp(owner_margin, MARGIN_PATH_FLOOR_PCT, MARGIN_CAP_PCT))
                core = sum(estimators) / len(estimators)
                terminal = TERMINAL_SPOT_WEIGHT * spot + (1 - TERMINAL_SPOT_WEIGHT) * core
                label = (
                    f"近期加權 {normalised:.1f}% / 近三年中位 {recent_level:.1f}% / "
                    f"六年中位 {through_cycle:.1f}%"
                    + (f" / 資本支出正常化 {owner_margin:.1f}%" if len(estimators) == 4 else "")
                )

            # Never extrapolate a terminal margin the company has never reached —
            # except where the normalised-capex reading is deliberately telling us
            # the reported history understates the steady state.
            hi = max(history)
            if len(estimators) == 4:
                hi = max(hi, estimators[3])
            terminal = clamp(terminal, min(history), hi)
            margin_source = (
                f"目前 {spot:.1f}%；終期 {terminal:.1f}% ＝ 目前值 {TERMINAL_SPOT_WEIGHT:.0%} ＋ "
                f"{len(estimators)} 種讀法等權 {1 - TERMINAL_SPOT_WEIGHT:.0%}（{label}）"
            )
            spread = max(estimators) - min(estimators)
            if spread > 8:
                warnings.append(
                    f"終期利潤率的 {len(estimators)} 種讀法差距 {spread:.1f} 個百分點（{label}）"
                    " —— 這家公司到底是在循環低谷還是結構性轉折，資料本身回答不了"
                )
            if spot < 0 <= core:
                warnings.append(
                    f"目前自由現金流利潤率為負（{spot:.1f}%），但三種歷史讀法平均為 {core:.1f}% —— "
                    "終期不把當前低谷當成常態"
                )
            if n_trimmed:
                warnings.append(
                    f"歷史自由現金流利潤率有 {n_trimmed} 個極端年度已修剪至合理圍籬"
                    f"（原始序列 {[round(h, 1) for h in history]}）—— "
                    "單一崩壞年度不該決定永續期的假設"
                )

        terminal = clamp(terminal, MARGIN_FLOOR_PCT, MARGIN_CAP_PCT)
        offset = clamp(0.75 * (sigma or 3.0), MARGIN_OFFSET_MIN_PP, MARGIN_OFFSET_MAX_PP)

        if sigma is not None and sigma > 10:
            warnings.append(
                f"自由現金流利潤率過去五年擺盪達 ±{sigma:.0f} 個百分點 —— 情境區間拉寬是有原因的"
            )

        return {
            "margin_profile": {
                "spot_pct": round(spot, 2),
                "normalised_pct": round(normalised, 2) if normalised is not None else None,
                "through_cycle_pct": round(through_cycle, 2) if through_cycle is not None else None,
                "recent_level_pct": round(recent_level, 2) if recent_level is not None else None,
                "owner_earnings_pct": round(owner_margin, 2) if owner_margin is not None else None,
                "variant": self.terminal_variant,
                "estimator_spread_pp": round(max(estimators) - min(estimators), 2) if len(estimators) > 1 else None,
                "outliers_trimmed": n_trimmed,
                "spot_weight_pct": round(TERMINAL_SPOT_WEIGHT * 100),
                "terminal_pct": round(terminal, 2),
                "sigma_pp": round(sigma, 2) if sigma is not None else None,
                "scenario_offset_pp": round(offset, 2),
                "history": [round(h, 2) for h in history],
                "unlevered_uplift_pp": round(uplift, 2),
                "basis": "未扣息自由現金流（已加回稅後利息）" if uplift > 0.05 else "帳列自由現金流",
                "source": margin_source,
            }
        }

    # ══════════════════════════════════════════════════════════════════════
    #  Projection mechanics
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _revenue_series(rev_paths: dict, path_key: str, terminal_growth: float) -> list[float] | None:
        """Extend the 3-year analyst path to :data:`PROJECTION_YEARS` years."""
        base = rev_paths.get("base_revenue")
        path = (rev_paths.get("paths") or {}).get(path_key)
        if not base or not path:
            return None

        series = list(path)
        # Years 4-5: fade the year-3 growth rate toward terminal growth.
        g3 = series[2] / series[1] - 1 if series[1] else terminal_growth
        remaining = PROJECTION_YEARS - len(series)
        for step in range(1, remaining + 1):
            frac = step / (remaining + 1)
            g = g3 + (terminal_growth - g3) * frac
            series.append(series[-1] * (1 + g))
        return series[:PROJECTION_YEARS]

    @staticmethod
    def _margin_series(spot_pct: float, terminal_pct: float, offset_pct: float) -> list[float]:
        """Linear fade from today's margin to the normalised long-run margin.

        The scenario offset ramps in over the projection rather than applying in
        full from year 1. Today's margin is measured, not assumed — for a company
        whose cash conversion has genuinely swung (KO: 10 % to 29 % over five
        years) charging the full historical sigma against year 1 as well tripled
        the scenario band on the strength of uncertainty we do not actually have
        about the current year.
        """
        out = []
        for yr in range(1, PROJECTION_YEARS + 1):
            frac = (yr - 1) / (PROJECTION_YEARS - 1)
            m = spot_pct + (terminal_pct - spot_pct) * frac + offset_pct * frac
            # 只有終期利潤率有 +1% 地板；明確預測期放行負值。AMZN 目前的
            # 自由現金流利潤率是 -1.16%，舊版把第 1–3 年一律夾成 +1.0%，等於
            # 用一個常數取代了資料，而且是往高估的方向取代。
            out.append(clamp(m, MARGIN_PATH_FLOOR_PCT, MARGIN_CAP_PCT) / 100.0)
        return out

    def _project(
        self, revenues: list[float], margins: list[float], base_revenue: float
    ) -> list[dict]:
        projections = []
        prev = base_revenue
        for i, (rev, margin) in enumerate(zip(revenues, margins), start=1):
            projections.append({
                "year": i,
                "revenue": rev,
                "growth_rate": (rev / prev - 1) if prev else 0.0,
                "fcf_margin": margin,
                "fcf": rev * margin,
            })
            prev = rev
        return projections

    @staticmethod
    def _discount(projections: list[dict], wacc: float) -> float:
        total = 0.0
        for p in projections:
            df = 1 / (1 + wacc) ** p["year"]
            pv = p["fcf"] * df
            p["discount_factor"] = round(df, 4)
            p["present_value"] = pv
            total += pv
        return total

    @staticmethod
    def _terminal_value(final_fcf: float, wacc: float, terminal_growth: float) -> float:
        effective = max(wacc, terminal_growth + MIN_WACC_GROWTH_SPREAD)
        return final_fcf * (1 + terminal_growth) / (effective - terminal_growth)

    @staticmethod
    def _shares_at(shares: float, drift_pct: float, years: int) -> float:
        return shares * (1 + drift_pct / 100.0) ** years

    # ── one valuation pass, reusable by scenarios / sensitivity / solver ────

    def _value(
        self,
        inputs: dict,
        *,
        revenues: list[float],
        margins: list[float],
        wacc: float,
        terminal_growth: float,
    ) -> dict:
        base_revenue = inputs["revenue_paths"]["base_revenue"]
        projections = self._project(revenues, margins, base_revenue)

        effective_wacc = max(wacc, terminal_growth + MIN_WACC_GROWTH_SPREAD)
        pv_fcfs = self._discount(projections, effective_wacc)

        terminal_value = self._terminal_value(projections[-1]["fcf"], wacc, terminal_growth)
        pv_terminal = terminal_value / (1 + effective_wacc) ** PROJECTION_YEARS

        enterprise_value = pv_fcfs + pv_terminal
        equity_value = enterprise_value + (inputs.get("net_cash") or 0)

        # Dilution / buyback: value accrues over the projection, so divide by the
        # share count the holder will actually be sharing it with.
        shares = self._shares_at(
            inputs["shares_outstanding"], inputs["share_drift_pct"], PROJECTION_YEARS
        )
        return {
            "projections": projections,
            "pv_fcfs": pv_fcfs,
            "terminal_value": terminal_value,
            "pv_terminal": pv_terminal,
            "enterprise_value": enterprise_value,
            "equity_value": equity_value,
            "shares_diluted": shares,
            "intrinsic_value": equity_value / shares if shares else 0.0,
            "effective_wacc": effective_wacc,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  Scenario
    # ══════════════════════════════════════════════════════════════════════

    def _compute_scenario(self, inputs: dict, scenario: str) -> dict | None:
        params = SCENARIO_PARAMS[scenario]
        profile = inputs.get("margin_profile")
        if not profile or not inputs.get("shares_outstanding"):
            return None

        tg = params["terminal_growth"]

        # 折現率的情境偏移按利差等比縮放，不用固定的 ±1 個百分點。
        # 終值的分母是「折現率 − 永續成長率」：同樣 ±1pp，對利差 4pp 的公用事業
        # 是 ±25% 的分母變動，對利差 13pp 的成長股只有 ±8%。固定值會讓低折現率
        # 個股的情境區間寬到三倍，卻讓高折現率個股的區間過窄。
        spread = max(inputs["wacc"] - tg, MIN_WACC_GROWTH_SPREAD)
        wacc_shift = clamp(WACC_OFFSET_SPREAD_RATIO * spread, WACC_OFFSET_MIN, WACC_OFFSET_MAX)
        wacc = clamp(
            inputs["wacc"] + params["wacc_offset_sign"] * wacc_shift, MIN_WACC, MAX_WACC
        )

        revenues = self._revenue_series(inputs["revenue_paths"], params["revenue_path"], tg)
        if not revenues:
            return None

        offset = profile["scenario_offset_pp"] * params["fcf_margin_sigma"]
        margins = self._margin_series(profile["spot_pct"], profile["terminal_pct"], offset)

        v = self._value(inputs, revenues=revenues, margins=margins, wacc=wacc, terminal_growth=tg)

        tv_pct = (v["pv_terminal"] / v["enterprise_value"] * 100) if v["enterprise_value"] else 0
        warnings = list(inputs.get("warnings", []))
        if tv_pct > 85:
            warnings.append(
                f"終值佔企業價值 {tv_pct:.0f}% —— 這個估值幾乎完全是對「第六年以後」的假設"
            )
        if v["effective_wacc"] != wacc:
            warnings.append(
                f"折現率由 {wacc:.2%} 上調至 {v['effective_wacc']:.2%}，"
                f"以維持與永續成長率至少 {MIN_WACC_GROWTH_SPREAD:.0%} 的差距"
            )

        current_price = inputs.get("current_price")
        upside = verdict = None
        if current_price and current_price > 0:
            upside = (v["intrinsic_value"] / current_price - 1) * 100
            verdict = "Undervalued" if upside > 0 else "Overvalued"

        sensitivity = terminal_check = None
        if scenario == "moderate":
            sensitivity = self._compute_sensitivity(inputs, revenues, margins, wacc, tg)
            terminal_check = self._terminal_cross_check(inputs, v, tg)

        out_projections = [
            {
                "year": p["year"],
                "revenue": round(p["revenue"], 0),
                "growth_rate": round(p["growth_rate"] * 100, 2),
                "fcf_margin": round(p["fcf_margin"] * 100, 2),
                "fcf": round(p["fcf"], 0),
                "discount_factor": p["discount_factor"],
                "present_value": round(p["present_value"], 0),
            }
            for p in v["projections"]
        ]

        return {
            "intrinsic_value": round(v["intrinsic_value"], 2),
            "upside_pct": round(upside, 2) if upside is not None else None,
            "margin_of_safety": round(upside, 2) if upside is not None else None,
            "verdict": verdict,
            "assumptions": {
                "wacc": round(inputs["wacc"] * 100, 2),
                "effective_wacc": round(v["effective_wacc"] * 100, 2),
                "wacc_source": inputs.get("wacc_source"),
                "wacc_components": inputs.get("wacc_components"),
                "terminal_growth": round(tg * 100, 2),
                "risk_free_rate": round(RISK_FREE_RATE * 100, 2),
                "equity_risk_premium": round(EQUITY_RISK_PREMIUM * 100, 2),
                "beta": inputs.get("beta"),
                "tax_rate": inputs.get("tax_rate"),
                # Year-1 margin, for continuity with the old field.
                "fcf_margin": round(out_projections[0]["fcf_margin"], 2),
                "fcf_margin_path": [p["fcf_margin"] for p in out_projections],
                "fcf_margin_terminal": profile["terminal_pct"],
                "growth_yr1": out_projections[0]["growth_rate"],
                "growth_yr2": out_projections[1]["growth_rate"],
                "growth_yr3": out_projections[2]["growth_rate"],
                "revenue_source": inputs["revenue_paths"]["source"],
                "revenue_base_period": inputs["revenue_paths"].get("base_period"),
                "share_drift_pct": round(inputs["share_drift_pct"], 2),
                "projection_years": PROJECTION_YEARS,
            },
            "summary": {
                "base_fcf": inputs["fcf"],
                "base_revenue": inputs["revenue_paths"]["base_revenue"],
                "shares_outstanding": inputs["shares_outstanding"],
                "shares_diluted_yr5": round(v["shares_diluted"], 0),
                "enterprise_value": round(v["enterprise_value"], 0),
                "equity_value": round(v["equity_value"], 0),
                "terminal_value": round(v["terminal_value"], 0),
                "pv_terminal": round(v["pv_terminal"], 0),
                "total_pv_fcfs": round(v["pv_fcfs"], 0),
                "terminal_value_pct": round(tv_pct, 1),
                "implied_terminal_fcf_multiple": (
                    round(v["terminal_value"] / v["projections"][-1]["fcf"], 1)
                    if v["projections"][-1]["fcf"] else None
                ),
                "cash": inputs.get("cash"),
                "total_debt": inputs.get("total_debt"),
                "net_cash": round(inputs.get("net_cash") or 0, 0),
                "market_cap": inputs.get("market_cap"),
            },
            "projections": out_projections,
            "value_bridge": [
                {"label": "PV of FCFs", "value": round(v["pv_fcfs"], 0)},
                {"label": "PV of Terminal Value", "value": round(v["pv_terminal"], 0)},
                {"label": "Net Cash", "value": round(inputs.get("net_cash") or 0, 0)},
                {"label": "Equity Value", "value": round(v["equity_value"], 0)},
            ],
            "sensitivity": sensitivity,
            "terminal_cross_check": terminal_check,
            "warnings": warnings,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  Sensitivity, cross-checks, reverse DCF, confidence
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _wacc_ladder(wacc: float) -> tuple[list[float], int]:
        """Five distinct in-band WACC rows, one of them exactly the base WACC.

        A fixed ±2 % ladder duplicates rows whenever the base sits near the
        6–16 % band edge: NVDA at 15.2 % printed 16.0 % twice, and a company
        pinned to the 6 % floor printed 6.0 % three times — three identical rows
        of a matrix whose whole job is to show how the answer moves. Rows are
        therefore allocated to whichever side has room, with the base row kept
        exact so the highlighted cell really is the headline number.
        """
        room_lo = wacc - MIN_WACC
        room_hi = MAX_WACC - wacc
        eps = 0.0005

        n_lo = 2 if room_lo > 2 * eps else 1 if room_lo > eps else 0
        n_hi = 4 - n_lo
        if room_hi <= eps:                       # pinned to the ceiling
            n_hi, n_lo = 0, 4
        elif n_hi * eps > room_hi:               # not enough headroom for that many
            n_hi = max(int(room_hi / eps), 1)
            n_lo = 4 - n_hi

        s_lo = min(0.01, room_lo / n_lo) if n_lo else 0.0
        s_hi = min(0.01, room_hi / n_hi) if n_hi else 0.0

        values = (
            [wacc - i * s_lo for i in range(n_lo, 0, -1)]
            + [wacc]
            + [wacc + i * s_hi for i in range(1, n_hi + 1)]
        )
        return values, n_lo

    def _compute_sensitivity(
        self, inputs: dict, revenues: list[float], margins: list[float],
        wacc: float, terminal_growth: float,
    ) -> dict:
        """WACC × terminal-growth, plus margin × growth.

        Unlike the previous version this rebuilds the FCF path for every
        terminal-growth column, because the years 4-5 fade targets that growth
        rate — holding one path fixed made the matrix inconsistent with the
        headline number it was supposed to explain.
        """
        wacc_values, base_wacc_idx = self._wacc_ladder(wacc)
        tg_values = [0.015, 0.02, 0.025, 0.03, 0.035]

        matrix = []
        for w in wacc_values:
            row = []
            for tg in tg_values:
                revs = self._revenue_series(inputs["revenue_paths"], "avg", tg) or revenues
                v = self._value(inputs, revenues=revs, margins=margins, wacc=w, terminal_growth=tg)
                row.append(round(v["intrinsic_value"], 2))
            matrix.append(row)

        # Margin × revenue-path: the two inputs an analyst actually argues about.
        profile = inputs["margin_profile"]
        margin_offsets = [-2.0, -1.0, 0.0, 1.0, 2.0]
        growth_scales = [0.7, 0.85, 1.0, 1.15, 1.3]
        m_matrix = []
        for mo in margin_offsets:
            row = []
            m = self._margin_series(
                profile["spot_pct"], profile["terminal_pct"],
                profile["scenario_offset_pp"] * mo,
            )
            for gs in growth_scales:
                revs = self._scaled_revenues(inputs, gs, terminal_growth)
                if not revs:
                    row.append(None)
                    continue
                v = self._value(inputs, revenues=revs, margins=m, wacc=wacc,
                                terminal_growth=terminal_growth)
                row.append(round(v["intrinsic_value"], 2))
            m_matrix.append(row)

        return {
            "wacc_labels": [f"{w * 100:.2f}%" for w in wacc_values],
            "tg_labels": [f"{tg * 100:.1f}%" for tg in tg_values],
            "matrix": matrix,
            "base_wacc_idx": base_wacc_idx,
            "base_tg_idx": tg_values.index(terminal_growth) if terminal_growth in tg_values else 2,
            "margin_growth": {
                # Label by the year-5 margin: the scenario offset now ramps in over
                # the projection, so year 1 is identical across every row and
                # labelling by it would suggest the rows differ when they do not.
                "margin_labels": [
                    f"{self._margin_series(profile['spot_pct'], profile['terminal_pct'], profile['scenario_offset_pp'] * mo)[-1] * 100:.1f}%"
                    for mo in margin_offsets
                ],
                "margin_axis": "第 5 年自由現金流利潤率",
                "growth_labels": [f"{(gs - 1) * 100:+.0f}% vs consensus" for gs in growth_scales],
                "matrix": m_matrix,
                "base_margin_idx": margin_offsets.index(0),
                "base_growth_idx": growth_scales.index(1.0),
            },
        }

    def _scaled_revenues(self, inputs: dict, scale: float, tg: float) -> list[float] | None:
        """Consensus revenue path with every growth *rate* scaled by *scale*."""
        base = inputs["revenue_paths"].get("base_revenue")
        consensus = self._revenue_series(inputs["revenue_paths"], "avg", tg)
        if not base or not consensus:
            return None
        out, prev_c, prev_o = [], base, base
        for rev in consensus:
            g = (rev / prev_c - 1) if prev_c else 0.0
            prev_o = prev_o * (1 + g * scale)
            out.append(prev_o)
            prev_c = rev
        return out

    def _terminal_cross_check(self, inputs: dict, v: dict, tg: float) -> dict | None:
        """Gordon terminal value vs the company's own median EV/FCF multiple."""
        mult = (inputs.get("exit_multiples") or {}).get("ev_fcf")
        if not mult or mult <= 0:
            return None
        capped = clamp(mult, EXIT_MULTIPLE_FLOOR, EXIT_MULTIPLE_CAP)
        tv_exit = v["projections"][-1]["fcf"] * capped
        pv_exit = tv_exit / (1 + v["effective_wacc"]) ** PROJECTION_YEARS
        equity_exit = v["pv_fcfs"] + pv_exit + (inputs.get("net_cash") or 0)
        iv_exit = equity_exit / v["shares_diluted"] if v["shares_diluted"] else 0
        implied_gordon_mult = (
            v["terminal_value"] / v["projections"][-1]["fcf"]
            if v["projections"][-1]["fcf"]
            else None
        )
        divergence = (
            (iv_exit / v["intrinsic_value"] - 1) * 100 if v["intrinsic_value"] else None
        )
        return {
            "method": "EV/FCF 出場倍數",
            "historical_ev_fcf_median": round(mult, 1),
            "applied_multiple": round(capped, 1),
            "implied_gordon_multiple": round(implied_gordon_mult, 1) if implied_gordon_mult else None,
            "intrinsic_value": round(iv_exit, 2),
            "divergence_vs_gordon_pct": round(divergence, 1) if divergence is not None else None,
            "note": (
                "永續成長模型與歷史出場倍數的結果相差超過 40% —— 終值假設是這檔估值的最大變數"
                if divergence is not None and abs(divergence) > 40
                else "永續成長模型與歷史出場倍數的結果大致一致"
            ),
        }

    def _capex_cycle_check(self, inputs: dict) -> dict | None:
        """What the company is worth if capex normalises to maintenance level.

        A free-cash-flow DCF treats every dollar of capex as gone forever. For a
        company mid-buildout that is the wrong question: Amazon runs 22.3 % of
        revenue through capex against 9.7 % of depreciation, so the model prices
        a $1.3T-revenue business at a 2.25 % cash margin and lands 95 % below the
        market. The valuation is not wrong so much as it is answering "what if
        this capex never stops" — worth reporting, but worth reporting *next to*
        the version where capex settles at depreciation.
        """
        cp = inputs.get("capex_profile") or {}
        gap = cp.get("growth_capex_pct")
        ocf_pct = cp.get("operating_cash_flow_pct")
        da_pct = cp.get("depreciation_pct")
        if gap is None or ocf_pct is None or da_pct is None or gap < CAPEX_CYCLE_GAP_PP:
            return None

        profile = inputs.get("margin_profile")
        price = inputs.get("current_price")
        if not profile or not price:
            return None

        # Owner earnings: operating cash flow less maintenance capex, proxied by
        # depreciation. Deliberately a proxy — D&A understates maintenance for a
        # business whose asset base is still compounding.
        normalised_pct = clamp(ocf_pct - da_pct, MARGIN_FLOOR_PCT, MARGIN_CAP_PCT)
        tg = SCENARIO_PARAMS["moderate"]["terminal_growth"]
        revenues = self._revenue_series(inputs["revenue_paths"], "avg", tg)
        if not revenues:
            return None

        v = self._value(
            inputs,
            revenues=revenues,
            margins=self._margin_series(normalised_pct, normalised_pct, 0),
            wacc=inputs["wacc"],
            terminal_growth=tg,
        )
        return {
            "applies": True,
            "operating_cash_flow_pct": ocf_pct,
            "capex_pct": cp.get("capex_pct"),
            "depreciation_pct": da_pct,
            "growth_capex_pct": gap,
            "normalised_fcf_margin": round(normalised_pct, 2),
            "model_terminal_fcf_margin": profile["terminal_pct"],
            "intrinsic_value": round(v["intrinsic_value"], 2),
            "upside_pct": round((v["intrinsic_value"] / price - 1) * 100, 2),
            "note": (
                f"資本支出佔營收 {cp.get('capex_pct')}%，折舊只有 {da_pct}% —— "
                f"中間 {gap} 個百分點是擴產投資，被自由現金流當成永久性流出。"
                f"若資本支出回落到折舊水準（營運現金流 − 折舊 = {normalised_pct:.1f}% 利潤率），"
                f"每股為 ${v['intrinsic_value']:.2f}。"
            ),
        }

    def _reverse_dcf(self, inputs: dict) -> dict | None:
        """What revenue growth does today's price already assume?

        Bisect on a multiplier applied to the consensus growth path until the
        model's intrinsic value equals the market price, holding margins, WACC
        and terminal growth at their moderate-case values.
        """
        price = inputs.get("current_price")
        profile = inputs.get("margin_profile")
        if not price or price <= 0 or not profile:
            return None

        tg = SCENARIO_PARAMS["moderate"]["terminal_growth"]
        wacc = inputs["wacc"]
        margins = self._margin_series(profile["spot_pct"], profile["terminal_pct"], 0)
        base = inputs["revenue_paths"].get("base_revenue")
        if not base:
            return None

        def iv_at(scale: float) -> float | None:
            revs = self._scaled_revenues(inputs, scale, tg)
            if not revs:
                return None
            return self._value(
                inputs, revenues=revs, margins=margins, wacc=wacc, terminal_growth=tg
            )["intrinsic_value"]

        lo, hi = -3.0, 6.0
        if (iv_at(lo) or 0) > price:
            implied_scale = lo
        elif (iv_at(hi) or 0) < price:
            implied_scale = hi
        else:
            for _ in range(60):
                mid = (lo + hi) / 2
                if (iv_at(mid) or 0) < price:
                    lo = mid
                else:
                    hi = mid
            implied_scale = (lo + hi) / 2

        implied_revs = self._scaled_revenues(inputs, implied_scale, tg)
        consensus_revs = self._revenue_series(inputs["revenue_paths"], "avg", tg)
        if not implied_revs or not consensus_revs:
            return None

        implied_cagr = _cagr(base, implied_revs[-1], PROJECTION_YEARS)
        consensus_cagr = _cagr(base, consensus_revs[-1], PROJECTION_YEARS)
        if implied_cagr is None or consensus_cagr is None:
            return None
        gap = (consensus_cagr - implied_cagr) * 100

        # Also: holding consensus growth, what terminal margin justifies the price?
        implied_margin = None
        m_lo, m_hi = MARGIN_FLOOR_PCT, MARGIN_CAP_PCT
        for _ in range(50):
            m_mid = (m_lo + m_hi) / 2
            v = self._value(
                inputs,
                revenues=consensus_revs,
                margins=self._margin_series(profile["spot_pct"], m_mid, 0),
                wacc=wacc,
                terminal_growth=tg,
            )
            if v["intrinsic_value"] < price:
                m_lo = m_mid
            else:
                m_hi = m_mid
        implied_margin = (m_lo + m_hi) / 2

        if gap > 5:
            read = "市場價入的成長明顯低於分析師預估"
        elif gap < -5:
            read = "市場價入的成長已經高於分析師預估"
        else:
            read = "市場大致按分析師共識定價"

        margin_capped = implied_margin >= MARGIN_CAP_PCT - 0.5
        return {
            "implied_revenue_cagr_5y": round(implied_cagr * 100, 2),
            "consensus_revenue_cagr_5y": round(consensus_cagr * 100, 2),
            "gap_pp": round(gap, 2),
            "implied_terminal_fcf_margin": round(implied_margin, 2),
            "implied_terminal_fcf_margin_capped": margin_capped,
            "model_terminal_fcf_margin": profile["terminal_pct"],
            "at_wacc": round(wacc * 100, 2),
            "at_terminal_growth": round(tg * 100, 2),
            "interpretation": read,
        }

    def _confidence(self, inputs: dict, scenarios: dict) -> dict:
        """Grade how much of the answer rests on measured data vs assumption."""
        factors: list[dict] = []

        def add(name: str, score: float, note: str) -> None:
            factors.append({"name": name, "score": round(score), "note": note})

        ages = inputs["source_age_days"]
        worst = max((a for a in ages.values() if a is not None), default=None)
        missing = [k for k, v in ages.items() if v is None]
        if worst is None:
            add("資料新鮮度", 20, "沒有任何帶日期的資料源")
        else:
            fresh = clamp(100 - worst / 3, 0, 100)
            miss_zh = "、".join(_SRC_ZH.get(m, m) for m in missing)
            add("資料新鮮度", fresh,
                f"最舊的資料源 {worst} 天前" + (f"；缺{miss_zh}" if missing else ""))

        cov = inputs.get("coverage") or {}
        n = cov.get("num_estimates") or cov.get("analyst_count") or 0
        cov_score = clamp(n * 6, 0, 100)
        if cov.get("thin_coverage"):
            cov_score *= 0.7
        add("分析師覆蓋度", cov_score,
            f"{int(n)} 家預估" + ("（後段年度覆蓋稀薄）" if cov.get("thin_coverage") else ""))

        rp = inputs["revenue_paths"]
        if "金額" in (rp.get("source") or ""):
            add("營收預估品質", 90, rp["source"])
        else:
            add("營收預估品質", 45, rp.get("source") or "來源不明")

        profile = inputs.get("margin_profile") or {}
        sigma = profile.get("sigma_pp")
        spread = profile.get("estimator_spread_pp")
        if sigma is None:
            add("利潤率穩定度", 40, "沒有現金流量表歷史，只有單點利潤率")
        else:
            # 三種讀法的落差比單純的標準差更貼近「終期假設有多不確定」：
            # 一家利潤率波動大但三種讀法都指向同一水準的公司，其實是可預測的。
            penalty = max(sigma * 7, (spread or 0) * 4)
            note = f"五年標準差 {sigma:.1f} 個百分點"
            if spread is not None:
                note += f"；終期三種讀法落差 {spread:.1f} 個百分點"
            add("利潤率穩定度", clamp(100 - penalty, 0, 100), note)

        tv_pct = scenarios["moderate"]["summary"]["terminal_value_pct"]
        add("對終值的依賴度", clamp((95 - tv_pct) * 2.5, 0, 100),
            f"終值佔企業價值 {tv_pct:.0f}%")

        spread = None
        ivs = [scenarios[s]["intrinsic_value"] for s in scenarios]
        mid = scenarios["moderate"]["intrinsic_value"]
        if mid:
            spread = (max(ivs) - min(ivs)) / abs(mid) * 100
            add("情境區間收斂度", clamp(120 - spread, 0, 100),
                f"樂觀到悲觀的落差是中性情境的 {spread:.0f}%")

        score = sum(f["score"] for f in factors) / len(factors)
        grade = "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"
        return {
            "score": round(score),
            "grade": grade,
            "factors": factors,
            "summary": {
                "A": "依據充分 —— 輸入資料新、都是實際量到的，情境也收斂",
                "B": "可用 —— 有一兩項輸入偏向假設",
                "C": "僅供參考 —— 好幾項輸入是假設出來的或已經過期",
                "D": "不要拿這個數字做交易決策 —— 模型幾乎都建立在假設上",
            }[grade],
        }

    @staticmethod
    def _applicability(inputs: dict, scenarios: dict) -> dict:
        """Say plainly when a DCF is the wrong tool, instead of printing a number.

        A negative equity value or a company that has never converted revenue
        into cash produces an arithmetically valid figure that means nothing.
        Callers (war room, AI analysis, the DCF page) should read this before
        quoting `intrinsic_value`.
        """
        reasons = []
        profile = inputs.get("margin_profile") or {}
        cc = inputs.get("_capex_cycle") or {}
        if (inputs.get("fcf") or 0) < 0:
            if cc.get("applies"):
                reasons.append(
                    f"最近四季自由現金流為負 —— 但主因是擴產投資"
                    f"（資本支出佔營收 {cc['capex_pct']}%，折舊只有 {cc['depreciation_pct']}%），"
                    f"不是本業不賺錢；請看下方「資本支出正常化」對照"
                )
            else:
                reasons.append("最近四季自由現金流為負")
        if profile.get("normalised_pct") is not None and profile["normalised_pct"] <= 0:
            reasons.append("五年平均自由現金流利潤率為零或負值")
        if scenarios["moderate"]["intrinsic_value"] <= 0:
            reasons.append("模型算出的股權價值為負 —— 負債大於折現後的現金流總值")
        net_cash = inputs.get("net_cash") or 0
        mc = inputs.get("market_cap") or 0
        if mc and net_cash < -1.5 * mc:
            reasons.append("淨負債超過市值的 150% —— 股權估值幾乎完全被槓桿主導")
        return {
            "meaningful": not reasons,
            "reasons": reasons,
            "note": (
                "現金流折現適合用來看這家公司"
                if not reasons
                else "不要引用這個每股價值：" + "；".join(reasons)
            ),
        }

    def _cross_checks(self, inputs: dict, scenarios: dict, weighted: float | None) -> dict:
        price = inputs.get("current_price")
        checks: list[dict] = []

        pt = inputs.get("price_target") or {}
        if pt.get("average"):
            checks.append({
                "name": "分析師目標價",
                "value": round(pt["average"], 2),
                "range": [pt.get("low"), pt.get("high")],
                "vs_dcf_pct": (
                    round((pt["average"] / scenarios["moderate"]["intrinsic_value"] - 1) * 100, 1)
                    if scenarios["moderate"]["intrinsic_value"] else None
                ),
            })
        for label, key in (("林區合理價", "lynch_fair_value"), ("葛拉漢數字", "graham_number")):
            if inputs.get(key):
                checks.append({
                    "name": label,
                    "value": round(inputs[key], 2),
                    "vs_price_pct": round((inputs[key] / price - 1) * 100, 1) if price else None,
                })

        implied_ev = scenarios["moderate"]["summary"]["enterprise_value"]
        actual_ev = inputs.get("enterprise_value")
        if actual_ev and implied_ev:
            checks.append({
                "name": "模型企業價值 vs 市場企業價值",
                "value": round(implied_ev / actual_ev, 2),
                "note": f"模型算出的企業價值較市場成交值 {implied_ev / actual_ev - 1:+.0%}",
            })

        return {
            "checks": checks,
            "weighted_fair_value": weighted,
            "reverse_dcf": inputs.get("_reverse_dcf"),
        }

    # ══════════════════════════════════════════════════════════════════════
    #  Entry points
    # ══════════════════════════════════════════════════════════════════════

    def compute(self, ticker: str) -> dict | None:
        inputs = self._build_inputs(ticker)
        if inputs is None or not inputs.get("revenue_paths", {}).get("base_revenue"):
            return None

        scenarios = {}
        for name in ("optimistic", "moderate", "pessimistic"):
            result = self._compute_scenario(inputs, name)
            if result is None:
                return None
            scenarios[name] = result

        inputs["_reverse_dcf"] = self._reverse_dcf(inputs)
        inputs["_capex_cycle"] = self._capex_cycle_check(inputs)
        confidence = self._confidence(inputs, scenarios)

        # Probability weights: a low-confidence model should not present a sharp
        # point estimate, so weight shifts toward the base case as data thins.
        conf = confidence["score"] / 100
        tail = 0.25 * conf + 0.12 * (1 - conf)
        weights = {"optimistic": tail, "pessimistic": tail, "moderate": 1 - 2 * tail}
        weighted = sum(scenarios[k]["intrinsic_value"] * w for k, w in weights.items())

        current_price = inputs.get("current_price")
        return {
            "ticker": inputs["ticker"],
            "applicability": self._applicability(inputs, scenarios),
            "data_date": inputs["data_date"],
            "computed_date": str(self.as_of or date.today()),
            "current_price": round(current_price, 2) if current_price else None,
            "price_date": inputs.get("price_date"),   # 這個價屬於哪一盤（快取失效用）
            "scenarios": scenarios,
            "fair_value": {
                "weighted": round(weighted, 2),
                "upside_pct": (
                    round((weighted / current_price - 1) * 100, 2) if current_price else None
                ),
                "weights": {k: round(w, 3) for k, w in weights.items()},
            },
            "confidence": confidence,
            "reverse_dcf": inputs["_reverse_dcf"],
            "capex_cycle": inputs["_capex_cycle"],
            # 終期利潤率是這個模型最主觀的一個輸入，所以三種讀法、歷史序列、
            # 修剪了幾個極端年度全部攤開，讓看的人自己判斷，而不是只給一個數字。
            "margin_readings": inputs.get("margin_profile"),
            "benchmarks": {
                "fcf_yield": inputs.get("fcf_yield"),
                "earnings_yield": inputs.get("earnings_yield"),
                "note": (
                    "回測（2026-02 至 2026-09，三個橫斷面）顯示：本模型的上檔空間排序"
                    "與自由現金流殖利率的相關性 0.80，抽掉殖利率後只剩 IC +0.07。"
                    "選股排序請以殖利率為準，本模型的價值在於個股診斷與適用性判定。"
                ),
            },
            "cross_checks": self._cross_checks(inputs, scenarios, round(weighted, 2)),
            "data_sources": {
                "used": inputs["sources"],
                "age_days": inputs["source_age_days"],
                "revenue_anchor": inputs["revenue_paths"]["source"],
                "margin_basis": (inputs.get("margin_profile") or {}).get("source"),
                "fcf_basis": (inputs.get("margin_profile") or {}).get("basis"),
                "wacc_basis": inputs.get("wacc_source"),
                "price_source": inputs.get("price_source"),
            },
            "warnings": inputs["warnings"],
        }

    def compute_and_save(self, ticker: str, force: bool = False) -> dict | None:
        out_dir = self.data_dir / "dcf"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ticker.upper()}_dcf_{date.today()}.json"

        if out_path.exists() and not force:
            with open(out_path, encoding="utf-8") as f:
                return json.load(f)

        result = self.compute(ticker)
        if result is None:
            return None

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        return result
