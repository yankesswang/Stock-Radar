from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


# --- Pagination ---
class Pagination(BaseModel):
    page: int
    per_page: int
    total: int
    total_pages: int


class PaginatedResponse(BaseModel):
    data: list
    pagination: Pagination


# --- Industry ---
class IndustryOut(BaseModel):
    id: int
    name: str
    stock_count: int = 0

    class Config:
        from_attributes = True


class IndustrySummary(IndustryOut):
    avg_pe: Optional[float] = None
    avg_profit_margin: Optional[float] = None
    overvalued_count: int = 0
    undervalued_count: int = 0
    top_undervalued: list[StockBrief] = []


# --- Stock ---
class StockBrief(BaseModel):
    ticker: str
    company_name: Optional[str] = None
    industry: str = ""
    current_price: Optional[float] = None
    valuation_status: Optional[str] = None
    valuation_diff_pct: Optional[float] = None

    class Config:
        from_attributes = True


class MetricOut(BaseModel):
    snapshot_date: date
    current_price: Optional[float] = None
    market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None
    pe: Optional[float] = None
    pe_forward: Optional[float] = None
    ps: Optional[float] = None
    pb: Optional[float] = None
    peg_ratio: Optional[float] = None
    ev_ebitda: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    profit_margin: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    roic: Optional[float] = None
    current_ratio: Optional[float] = None
    debt_equity: Optional[float] = None
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    fcf: Optional[float] = None
    eps: Optional[float] = None
    dividend_yield: Optional[float] = None
    beta: Optional[float] = None
    revenue_5y: Optional[float] = None
    eps_5y: Optional[float] = None
    price_target: Optional[float] = None
    analyst_count: Optional[float] = None
    earnings_yield: Optional[float] = None
    fcf_yield: Optional[float] = None

    class Config:
        from_attributes = True


class ForecastOut(BaseModel):
    snapshot_date: date
    current_eps: Optional[float] = None
    current_eps_growth: Optional[float] = None
    next_year_eps: Optional[float] = None
    next_year_eps_growth: Optional[float] = None
    current_revenue: Optional[float] = None
    current_revenue_growth: Optional[float] = None
    next_year_revenue: Optional[float] = None
    next_year_revenue_growth: Optional[float] = None

    class Config:
        from_attributes = True


class ValuationOut(BaseModel):
    ticker: str
    company_name: Optional[str] = None
    industry: str = ""
    snapshot_date: date
    fiscal_year: int
    estimated_pe: Optional[float] = None
    current_year_eps: Optional[float] = None
    current_year_fair_price: Optional[float] = None
    next_year_eps: Optional[float] = None
    next_year_fair_price: Optional[float] = None
    pe_median_5y: Optional[float] = None
    pe_median_price: Optional[float] = None
    next_year_pe_median_price: Optional[float] = None
    current_price: Optional[float] = None
    market_cap_display: Optional[str] = None
    valuation_status: Optional[str] = None
    valuation_diff_pct: Optional[float] = None
    next_year_valuation_status: Optional[str] = None
    next_year_valuation_diff_pct: Optional[float] = None
    revenue_growth_5y: Optional[float] = None
    eps_growth_5y: Optional[float] = None
    eps_growth_past: Optional[float] = None

    class Config:
        from_attributes = True


class StockDetail(BaseModel):
    ticker: str
    company_name: Optional[str] = None
    industry: str = ""
    latest_metric: Optional[MetricOut] = None
    latest_forecast: Optional[ForecastOut] = None
    latest_valuation: Optional[ValuationOut] = None

    class Config:
        from_attributes = True


# --- Compare ---
class CompareResponse(BaseModel):
    stocks: list[StockDetail]


# --- Screener ---
class FilterCondition(BaseModel):
    field: str
    op: str  # lt, gt, eq, lte, gte
    value: float


class ScreenRequest(BaseModel):
    filters: list[FilterCondition] = []
    industries: list[str] = []
    valuation_status: Optional[str] = None  # overvalued, undervalued
    search: Optional[str] = None
    sort_by: str = "ticker"
    sort_dir: str = "asc"
    page: int = 1
    per_page: int = 50


# --- Data Status ---
class DataStatus(BaseModel):
    total_stocks: int
    total_industries: int
    latest_pe_date: Optional[date] = None
    latest_ratio_date: Optional[date] = None
    latest_forecast_date: Optional[date] = None
    snapshot_count: int = 0


# Rebuild models that have forward references
IndustrySummary.model_rebuild()
