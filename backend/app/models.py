from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, UniqueConstraint, Index
from datetime import datetime, date as date_type
from sqlalchemy.orm import relationship

from .database import Base


class Industry(Base):
    __tablename__ = "industries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)

    stocks = relationship("Stock", back_populates="industry")


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, unique=True, index=True)
    company_name = Column(String)
    industry_id = Column(Integer, ForeignKey("industries.id"), nullable=False)

    industry = relationship("Industry", back_populates="stocks")
    metrics = relationship("Metric", back_populates="stock")
    forecasts = relationship("Forecast", back_populates="stock")
    pe_medians = relationship("PeMedian", back_populates="stock")
    valuations = relationship("Valuation", back_populates="stock")
    statistics = relationship("Statistic", back_populates="stock")
    metrics_breakdown = relationship("MetricsBreakdown", back_populates="stock")

    __table_args__ = (
        Index("idx_stocks_industry", "industry_id"),
    )


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    snapshot_date = Column(Date, nullable=False)

    # Price & Market
    current_price = Column(Float)
    market_cap = Column(Float)
    enterprise_value = Column(Float)

    # Valuation Ratios
    pe = Column(Float)
    pe_forward = Column(Float)
    ps = Column(Float)
    ps_forward = Column(Float)
    pb = Column(Float)
    peg_ratio = Column(Float)
    ev_earnings = Column(Float)
    ev_sales = Column(Float)
    ev_ebitda = Column(Float)
    ev_ebit = Column(Float)
    ev_fcf = Column(Float)

    # Profitability
    gross_margin = Column(Float)
    operating_margin = Column(Float)
    pretax_margin = Column(Float)
    profit_margin = Column(Float)
    ebitda_margin = Column(Float)
    ebit_margin = Column(Float)
    fcf_margin = Column(Float)
    roe = Column(Float)
    roa = Column(Float)
    roic = Column(Float)
    roce = Column(Float)

    # Financial Health
    current_ratio = Column(Float)
    quick_ratio = Column(Float)
    debt_equity = Column(Float)
    debt_ebitda = Column(Float)
    debt_fcf = Column(Float)
    interest_coverage = Column(Float)

    # Income
    revenue = Column(Float)
    gross_profit = Column(Float)
    operating_income = Column(Float)
    pretax_income = Column(Float)
    net_income = Column(Float)
    ebitda = Column(Float)
    ebit = Column(Float)
    eps = Column(Float)

    # Balance Sheet
    total_cash = Column(Float)
    debt = Column(Float)
    net_cash = Column(Float)
    net_cash_per_share = Column(Float)
    equity = Column(Float)
    bvps = Column(Float)
    working_capital = Column(Float)

    # Cash Flow
    ncfo = Column(Float)
    capex = Column(Float)
    fcf = Column(Float)
    fcf_per_share = Column(Float)

    # Dividends & Returns
    dividend_yield = Column(Float)
    dividend_growth = Column(Float)
    dividend_growth_years = Column(Float)
    payout_ratio = Column(Float)
    buyback_yield = Column(Float)
    earnings_yield = Column(Float)
    fcf_yield = Column(Float)

    # Growth
    revenue_5y = Column(Float)
    eps_5y = Column(Float)

    # Analyst
    price_target = Column(Float)
    analyst_count = Column(Float)

    # Shares
    shares_out = Column(Float)
    shares_growth_yoy = Column(Float)
    float_shares = Column(Float)

    # Technical
    beta = Column(Float)
    change_1y = Column(Float)
    sma_50 = Column(Float)
    sma_200 = Column(Float)
    rsi = Column(Float)

    # Short
    short_interest = Column(Float)
    short_float = Column(Float)
    short_ratio = Column(Float)

    stock = relationship("Stock", back_populates="metrics")

    __table_args__ = (
        UniqueConstraint("stock_id", "snapshot_date"),
        Index("idx_metrics_stock_date", "stock_id", "snapshot_date"),
    )


class Forecast(Base):
    __tablename__ = "forecasts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    snapshot_date = Column(Date, nullable=False)

    current_eps = Column(Float)
    current_eps_growth = Column(Float)
    next_year_eps = Column(Float)
    next_year_eps_growth = Column(Float)
    current_revenue = Column(Float)
    current_revenue_growth = Column(Float)
    next_year_revenue = Column(Float)
    next_year_revenue_growth = Column(Float)

    stock = relationship("Stock", back_populates="forecasts")

    __table_args__ = (
        UniqueConstraint("stock_id", "snapshot_date"),
        Index("idx_forecasts_stock_date", "stock_id", "snapshot_date"),
    )


class PeMedian(Base):
    __tablename__ = "pe_medians"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    snapshot_date = Column(Date, nullable=False)
    pe_median = Column(Float)

    stock = relationship("Stock", back_populates="pe_medians")

    __table_args__ = (
        UniqueConstraint("stock_id", "snapshot_date"),
        Index("idx_pe_stock_date", "stock_id", "snapshot_date"),
    )


class Valuation(Base):
    __tablename__ = "valuations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    snapshot_date = Column(Date, nullable=False)
    fiscal_year = Column(Integer, nullable=False)

    estimated_pe = Column(Float)
    current_year_eps = Column(Float)
    current_year_fair_price = Column(Float)
    next_year_eps = Column(Float)
    next_year_fair_price = Column(Float)
    pe_median_5y = Column(Float)
    pe_median_price = Column(Float)
    next_year_pe_median_price = Column(Float)
    current_price = Column(Float)
    market_cap_display = Column(String)
    valuation_status = Column(String)
    valuation_diff_pct = Column(Float)
    next_year_valuation_status = Column(String)
    next_year_valuation_diff_pct = Column(Float)
    revenue_growth_5y = Column(Float)
    eps_growth_5y = Column(Float)
    eps_growth_past = Column(Float)

    stock = relationship("Stock", back_populates="valuations")

    __table_args__ = (
        UniqueConstraint("stock_id", "snapshot_date"),
        Index("idx_valuations_stock_date", "stock_id", "snapshot_date"),
        Index("idx_valuations_status", "valuation_status"),
    )


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_type = Column(String, nullable=False)
    file_name = Column(String, nullable=False, unique=True)
    snapshot_date = Column(Date, nullable=False)
    stock_count = Column(Integer)


class Statistic(Base):
    """Key statistics scraped from stockanalysis.com/stocks/{ticker}/statistics/"""
    __tablename__ = "statistics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    snapshot_date = Column(Date, nullable=False)
    section = Column(String, nullable=False)  # e.g., "Total Valuation", "Valuation Ratios"
    label = Column(String, nullable=False)    # e.g., "Market Cap", "PE Ratio"
    value = Column(Float)                     # Parsed numeric value
    display = Column(String)                  # Original display string

    stock = relationship("Stock", back_populates="statistics")

    __table_args__ = (
        Index("idx_statistics_stock_date", "stock_id", "snapshot_date"),
        Index("idx_statistics_section", "section"),
    )


class MetricsBreakdown(Base):
    """Metrics breakdown scraped from stockanalysis.com/stocks/{ticker}/metrics/"""
    __tablename__ = "metrics_breakdown"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    snapshot_date = Column(Date, nullable=False)
    category = Column(String, nullable=False)  # e.g., "Revenue by Segment", "Revenue by Geography"
    metric = Column(String, nullable=False)     # e.g., "iPhone", "Americas"
    period = Column(String, nullable=False)     # e.g., "Q1 2025", "FY 2024"
    value = Column(Float)                       # Parsed numeric value

    stock = relationship("Stock", back_populates="metrics_breakdown")

    __table_args__ = (
        Index("idx_metrics_breakdown_stock_date", "stock_id", "snapshot_date"),
        Index("idx_metrics_breakdown_category", "category"),
    )



class Portfolio(Base):
    __tablename__ = "portfolios"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String, nullable=False)
    description = Column(String, default="")
    created_at  = Column(DateTime, default=datetime.utcnow)

    holdings = relationship("Holding", back_populates="portfolio", cascade="all, delete-orphan")


class Holding(Base):
    __tablename__ = "holdings"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    ticker       = Column(String, nullable=False)
    shares       = Column(Float, nullable=False)
    avg_cost     = Column(Float, nullable=False)   # cost per share
    added_date   = Column(Date, default=date_type.today)
    notes        = Column(String, default="")

    portfolio = relationship("Portfolio", back_populates="holdings")

    __table_args__ = (
        Index("idx_holdings_portfolio", "portfolio_id"),
    )


class PortfolioMeta(Base):
    """Starting capital and other per-portfolio config."""
    __tablename__ = "portfolio_meta"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_name   = Column(String, nullable=False, unique=True, index=True)
    starting_capital = Column(Float)


class PortfolioDeposit(Base):
    """Individual deposit records used for MWR / XIRR calculation."""
    __tablename__ = "portfolio_deposits"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_name = Column(String, nullable=False, index=True)
    deposit_date   = Column(Date, nullable=False)
    amount         = Column(Float, nullable=False)

    __table_args__ = (
        Index("idx_deposit_name_date", "portfolio_name", "deposit_date"),
    )


class PortfolioSnapshot(Base):
    """One row per (portfolio_name, date) — daily portfolio-level summary."""
    __tablename__ = "portfolio_snapshots"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_name = Column(String, nullable=False, index=True)
    snapshot_date  = Column(Date, nullable=False)
    total_value    = Column(Float)
    total_cash     = Column(Float)
    cash_pct       = Column(Float)
    net_gain       = Column(Float)
    net_return_pct = Column(Float)
    mwr            = Column(Float)
    position_count = Column(Integer)

    holdings = relationship(
        "HoldingSnapshot", back_populates="snapshot", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("portfolio_name", "snapshot_date"),
        Index("idx_ps_name_date", "portfolio_name", "snapshot_date"),
    )


class HoldingSnapshot(Base):
    """One row per holding within a PortfolioSnapshot."""
    __tablename__ = "holding_snapshots"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id  = Column(Integer, ForeignKey("portfolio_snapshots.id"), nullable=False)
    ticker       = Column(String, nullable=False)
    account      = Column(String, default="")
    shares       = Column(Float)
    avg_cost     = Column(Float)
    market_value = Column(Float)
    weight_pct   = Column(Float)
    return_pct   = Column(Float)

    snapshot = relationship("PortfolioSnapshot", back_populates="holdings")

    __table_args__ = (
        Index("idx_hs_snapshot", "snapshot_id"),
        Index("idx_hs_ticker", "ticker"),
    )
