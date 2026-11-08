"""
DCF input layer — pull every fundamental series StockRadar already scrapes into
one normalised structure the valuation engine can reason about.

The DCF used to read only two files (statistics + forecast detail) and take a
single spot value for every input.  StockRadar actually scrapes far more:

    data/statistics/{T}_statistics_{d}.json   spot balance sheet / margins / WACC / scores
    data/forecast/detail/{T}_forecast_{d}.json 8y revenue history + 3y analyst
                                               revenue/EPS levels, ranges,
                                               estimate counts, price targets
    data/financials/{T}_income_{d}.csv        5FY + TTM income statement
    data/financials/{T}_cashflow_{d}.csv      5FY + TTM cash flow (SBC, capex,
                                               D&A, unlevered FCF, FCF margin)
    data/financials/{T}_ratios_{d}.csv        5FY + current EV/EBITDA, EV/FCF …

This module loads all of them, harmonises units (statements are in millions,
statistics in raw dollars) and exposes helpers the calculator needs: normalised
margins, share-count drift, cost of debt, historical exit multiples.
"""

from __future__ import annotations

import csv
import glob
import json
import re
import statistics as stats_mod
from datetime import date, datetime
from pathlib import Path

MILLION = 1_000_000

# Recency weights applied to a series ordered newest → oldest (TTM first).
# Half-life of roughly two periods: the last two years carry ~65 % of the weight.
RECENCY_WEIGHTS = [0.35, 0.25, 0.18, 0.12, 0.06, 0.04]


# ── generic helpers ────────────────────────────────────────────────────────

def _newest(pattern: str, as_of: date | None = None) -> Path | None:
    """Newest file matching *pattern*, ignoring anything dated after *as_of*.

    `as_of` makes point-in-time reconstruction possible: the backtest needs the
    inputs the model would actually have had on a past date, not today's.
    """
    files = sorted(glob.glob(pattern), reverse=True)
    if as_of is not None:
        files = [f for f in files
                 if (d := _date_from_name(Path(f))) is not None and d <= as_of]
    return Path(files[0]) if files else None


def _date_from_name(path: Path) -> date | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _num(raw) -> float | None:
    """Parse a CSV cell into a float, tolerating '', 'n/a', '1,234', '(12)', '5%'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s or s.lower() in {"n/a", "na", "-", "--", "upgrade"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("$", "").replace("%", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def weighted_recent(series: list[float]) -> float | None:
    """Recency-weighted mean of a newest-first series."""
    vals = [v for v in series if v is not None]
    if not vals:
        return None
    w = RECENCY_WEIGHTS[: len(vals)]
    total = sum(w)
    return sum(v * wi for v, wi in zip(vals, w)) / total


def dispersion(series: list[float]) -> float | None:
    """Population stdev of a series, or None when fewer than two points."""
    vals = [v for v in series if v is not None]
    if len(vals) < 2:
        return None
    return stats_mod.pstdev(vals)


def median_of(series: list[float]) -> float | None:
    vals = [v for v in series if v is not None]
    return stats_mod.median(vals) if vals else None


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def winsorize(series: list[float]) -> tuple[list[float], int]:
    """Pull outliers back to a Tukey fence, preserving order and length.

    A weighted mean has no defence against one catastrophic year. Carnival's
    2020 FCF margin was −392 % and Royal Caribbean's −256 %; either one dragged
    the recency-weighted mean of an otherwise 17–22 % business below zero and
    took the terminal margin with it. The median already ignores such a point;
    this lets the mean ignore it too, without discarding the observation
    entirely — a real bad year should still count, just not count for everything.

    Returns the clipped series and how many values were actually moved.
    """
    vals = [v for v in series if v is not None]
    if len(vals) < 4:
        return list(series), 0
    q = sorted(vals)
    n = len(q)
    p25, p75 = q[n // 4], q[(3 * n) // 4]
    iqr = p75 - p25
    if iqr <= 0:
        return list(series), 0
    lo, hi = p25 - 1.5 * iqr, p75 + 1.5 * iqr
    out = [v if v is None else clamp(v, lo, hi) for v in series]
    moved = sum(1 for a, b in zip(series, out) if a is not None and abs(a - b) > 1e-9)
    return out, moved


# ── statement CSV loader ───────────────────────────────────────────────────

class StatementTable:
    """A scraped income / cashflow / ratios CSV, addressed by metric name.

    Columns are periods, newest first ('TTM' or 'Current', then FY n, FY n-1 …).
    Values are returned newest-first; money metrics are converted to raw dollars.
    """

    # Metrics that are ratios/percentages and must not be scaled by 1e6.
    _RATIO_HINT = re.compile(
        r"margin|growth|ratio|rate|yield|per share|change|turnover|coverage|"
        r"return on|z-score|f-score|eps|dividend per",
        re.I,
    )

    def __init__(self, path: Path | None):
        self.path = path
        self.as_of = _date_from_name(path) if path else None
        self.periods: list[str] = []
        self._rows: dict[str, list[float | None]] = {}
        if path and path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return
            self.periods = header[2:]
            for row in reader:
                if len(row) < 3:
                    continue
                metric = row[1].strip()
                self._rows.setdefault(metric, [_num(c) for c in row[2:]])

    def __bool__(self) -> bool:
        return bool(self._rows)

    def series(self, metric: str, *, scale: bool | None = None) -> list[float | None]:
        """Newest-first values for *metric*. Money is scaled to raw dollars."""
        raw = self._rows.get(metric)
        if raw is None:
            for key in self._rows:
                if key.lower() == metric.lower():
                    raw = self._rows[key]
                    break
        if raw is None:
            return []
        if scale is None:
            scale = not self._RATIO_HINT.search(metric)
        return [None if v is None else (v * MILLION if scale else v) for v in raw]

    def series_any(self, *metrics: str, scale: bool | None = None) -> list[float | None]:
        """First non-empty series among *metrics*.

        The scraper's metric names drift between tickers and over time
        ('FCF Margin' vs 'Free Cash Flow Margin'), which silently emptied whole
        history series and pushed the model back onto spot values.
        """
        for m in metrics:
            got = self.series(m, scale=scale)
            if any(v is not None for v in got):
                return got
        return []

    def latest_any(self, *metrics: str, scale: bool | None = None) -> float | None:
        for v in self.series_any(*metrics, scale=scale):
            if v is not None:
                return v
        return None

    def latest(self, metric: str, *, scale: bool | None = None) -> float | None:
        for v in self.series(metric, scale=scale):
            if v is not None:
                return v
        return None

    def fy_series(self, metric: str, *, scale: bool | None = None) -> list[float | None]:
        """Same as :meth:`series` but drops the TTM / Current column."""
        s = self.series(metric, scale=scale)
        if self.periods and self.periods[0].strip().lower() in {"ttm", "current"}:
            return s[1:]
        return s


# ── the bundle ─────────────────────────────────────────────────────────────

class FundamentalBundle:
    """Every scraped source for one ticker, loaded once."""

    def __init__(self, ticker: str, data_dir: Path, as_of: date | None = None):
        self.ticker = ticker.upper()
        self.data_dir = Path(data_dir)
        self.as_of = as_of

        self.statistics, self.statistics_date = self._load_json(
            self.data_dir / "statistics" / f"{self.ticker}_statistics_*.json", as_of
        )
        self.forecast, self.forecast_date = self._load_json(
            self.data_dir / "forecast" / "detail" / f"{self.ticker}_forecast_*.json", as_of
        )
        fin = self.data_dir / "financials"
        self.income = StatementTable(_newest(str(fin / f"{self.ticker}_income_*.csv"), as_of))
        self.cashflow = StatementTable(_newest(str(fin / f"{self.ticker}_cashflow_*.csv"), as_of))
        self.ratios = StatementTable(_newest(str(fin / f"{self.ticker}_ratios_*.csv"), as_of))

    @staticmethod
    def _load_json(pattern: str | Path, as_of: date | None = None) -> tuple[dict | None, date | None]:
        path = _newest(str(pattern), as_of)
        if not path:
            return None, None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f), _date_from_name(path)
        except (OSError, json.JSONDecodeError):
            return None, None

    # ── statistics accessors ───────────────────────────────────────────────

    def stat(self, section: str, label: str):
        """Numeric value from a statistics section, tolerant of label drift.

        The scraped labels have changed over time (``Cash & Marketable
        Securities`` → ``Cash & Cash Equivalents``, ``Revenue Growth Forecast
        (5Y)`` → ``(3Y)``), which used to silently return ``None`` and knock out
        whole branches of the model.  Matching is case-insensitive with a
        normalised-prefix fallback so a renamed suffix still resolves.
        """
        if not self.statistics:
            return None
        want_section = section.strip().lower()
        want_label = label.strip().lower()
        stem = re.sub(r"\s*\(.*?\)\s*$", "", want_label).strip()
        fallback = None
        for sec in self.statistics.get("sections", []):
            if sec.get("name", "").strip().lower() != want_section:
                continue
            for item in sec.get("items", []):
                got = item.get("label", "").strip().lower()
                if got == want_label:
                    return item.get("value")
                if stem and re.sub(r"\s*\(.*?\)\s*$", "", got).strip() == stem:
                    fallback = item.get("value")
        return fallback

    def stat_any(self, section: str, *labels: str):
        """First non-None value among *labels* in *section*."""
        for label in labels:
            v = self.stat(section, label)
            if v is not None:
                return v
        return None

    # ── freshness ──────────────────────────────────────────────────────────

    def age_days(self, today: date | None = None) -> dict[str, int | None]:
        today = today or self.as_of or date.today()

        def age(d: date | None) -> int | None:
            return (today - d).days if d else None

        return {
            "statistics": age(self.statistics_date),
            "forecast": age(self.forecast_date),
            "income": age(self.income.as_of),
            "cashflow": age(self.cashflow.as_of),
            "ratios": age(self.ratios.as_of),
        }

    def sources_used(self) -> list[str]:
        used = []
        if self.statistics:
            used.append("statistics")
        if self.forecast:
            used.append("forecast")
        if self.income:
            used.append("income")
        if self.cashflow:
            used.append("cashflow")
        if self.ratios:
            used.append("ratios")
        return used

    # ── derived series the calculator wants ────────────────────────────────

    FCF_MARGIN_ALIASES = ("FCF Margin", "Free Cash Flow Margin")
    FCF_ALIASES = ("Free Cash Flow", "Levered Free Cash Flow")
    UFCF_ALIASES = ("Unlevered Free Cash Flow",)

    def fcf_margin_history(self) -> list[float]:
        """FCF margin (%) newest-first, from the cash-flow statement."""
        series = self.cashflow.series_any(*self.FCF_MARGIN_ALIASES)
        if not series:
            series = self.income.series_any(*self.FCF_MARGIN_ALIASES)
        return [v for v in series if v is not None]

    def unlevered_fcf_uplift_pct(self) -> float | None:
        """How many margin points reported FCF understates *unlevered* FCF.

        A DCF discounts cash available to all capital providers and then
        subtracts net debt.  Reported free cash flow is already net of interest,
        so feeding it into an enterprise-value bridge charges the same leverage
        twice — harmless for a debt-free company, decisive for a levered one.
        Prefer the scraped unlevered series; otherwise add back after-tax
        interest expense.
        """
        rev = self.income.series("Revenue")
        # Deliberately not using the scraped "Unlevered Free Cash Flow" line: its
        # definition varies by ticker (for NVDA it sits ~6 pp of revenue above
        # reported FCF, far more than the company's trivial interest bill), so
        # the add-back is computed from interest expense, which is unambiguous.
        interest = self.income.series("Interest Expense")
        tax = (self.effective_tax_rate() or 21.0) / 100.0
        if interest and rev:
            deltas = [
                abs(i) * (1 - tax) / r * 100.0
                for i, r in zip(interest, rev)
                if i is not None and r
            ]
            got = weighted_recent(deltas)
            if got is not None:
                return clamp(got, 0.0, 15.0)
        return None

    def revenue_history(self) -> list[float]:
        """Annual revenue (raw dollars) newest-first from the income statement."""
        return [v for v in self.income.fy_series("Revenue") if v is not None]

    def sbc_pct_revenue(self) -> float | None:
        """Stock-based comp as a % of revenue, recency weighted."""
        sbc = self.cashflow.series("Stock-Based Compensation")
        rev = self.income.series("Revenue")
        if not sbc or not rev:
            return None
        ratios = [
            s / r * 100.0
            for s, r in zip(sbc, rev)
            if s is not None and r not in (None, 0)
        ]
        return weighted_recent(ratios)

    def capex_pct_revenue(self) -> float | None:
        capex = self.cashflow.series("Capital Expenditures")
        rev = self.income.series("Revenue")
        if not capex or not rev:
            return None
        ratios = [
            abs(c) / r * 100.0
            for c, r in zip(capex, rev)
            if c is not None and r not in (None, 0)
        ]
        return weighted_recent(ratios)

    def share_change_pct(self) -> float | None:
        """Recency-weighted annual change in diluted shares (%, + = dilution).

        Falls back to the statistics 'Shares Change (YoY)' spot figure.
        """
        series = [v for v in self.income.series("Shares Change (YoY)") if v is not None]
        if series:
            return weighted_recent(series)
        return self.stat("Share Statistics", "Shares Change (YoY)")

    def effective_tax_rate(self) -> float | None:
        """Normalised effective tax rate (%), ignoring negative/absurd years."""
        series = [
            v for v in self.income.series("Effective Tax Rate")
            if v is not None and 0 <= v <= 45
        ]
        if series:
            return weighted_recent(series)
        spot = self.stat("Taxes", "Effective Tax Rate")
        if spot is not None and 0 <= spot <= 45:
            return spot
        return None

    def cost_of_debt(self) -> float | None:
        """Interest expense / total debt (%), from the income statement."""
        interest = self.income.latest("Interest Expense")
        debt = self.stat("Balance Sheet", "Total Debt")
        if debt in (None, 0):
            debt = self.ratios.latest("Total Debt")
        if interest is None or not debt:
            return None
        rate = abs(interest) / abs(debt) * 100.0
        return rate if 0.5 <= rate <= 20 else None

    def exit_multiples(self) -> dict[str, float | None]:
        """Median historical EV multiples, used to cross-check terminal value."""
        return {
            "ev_ebitda": median_of(self.ratios.series("EV/EBITDA Ratio")),
            "ev_fcf": median_of(self.ratios.series("EV/FCF Ratio")),
            "ev_sales": median_of(self.ratios.series("EV/Sales Ratio")),
        }

    def capex_profile(self) -> dict:
        """Operating cash flow, capex and D&A as a % of revenue.

        Free cash flow subtracts *all* capex, so a company mid-buildout looks
        like it is burning cash even while the underlying business gushes it.
        Amazon's trailing capex is 22.3 % of revenue against 9.7 % of
        depreciation — the 12.6-point gap is capacity being built, which an
        FCF-based DCF charges as a permanent outflow. These three ratios are
        what a normalised-capex cross-check needs.
        """
        rev = self.stat("Income Statement", "Revenue")
        ocf = self.stat("Cash Flow", "Operating Cash Flow")
        capex = self.stat("Cash Flow", "Capital Expenditures")
        da = self.stat("Cash Flow", "Depreciation & Amortization")
        if not rev:
            return {}

        def pct(v):
            return round(abs(v) / rev * 100, 2) if v is not None else None

        capex_pct, da_pct = pct(capex), pct(da)
        return {
            "revenue": rev,
            "operating_cash_flow": ocf,
            "operating_cash_flow_pct": pct(ocf),
            "capex_pct": capex_pct,
            "depreciation_pct": da_pct,
            "growth_capex_pct": (
                round(capex_pct - da_pct, 2)
                if capex_pct is not None and da_pct is not None
                else None
            ),
        }

    def analyst_coverage(self) -> dict:
        """Estimate counts / revision balance / thin-coverage flags from Nasdaq."""
        out = {
            "num_estimates": None,
            "revisions_up": 0,
            "revisions_down": 0,
            "thin_coverage": False,
            "analyst_count": self.stat("Analyst Forecast", "Analyst Count"),
        }
        if not self.forecast:
            return out
        raw = (self.forecast.get("fallback") or {}).get("nasdaq_raw") or []
        if raw:
            first = raw[0]
            out["num_estimates"] = first.get("num_estimates")
            out["revisions_up"] = sum(r.get("revisions_up") or 0 for r in raw)
            out["revisions_down"] = sum(r.get("revisions_down") or 0 for r in raw)
        filled = (self.forecast.get("fallback") or {}).get("filled") or []
        out["thin_coverage"] = any(f.get("thin_coverage") for f in filled)
        return out

    def price_target(self) -> dict | None:
        if not self.forecast:
            return None
        pt = self.forecast.get("price_target")
        return pt if isinstance(pt, dict) else None
