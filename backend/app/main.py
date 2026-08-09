import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .config import settings
from .database import engine, Base
from .routers import stocks, valuations, industries, search, compare, screener, data, metrics_breakdown, statistics, ratios, forecast, dcf, scrape, pe, chart, signals, portfolio, earnings, scheduler, summary, industry_compare, alerts, watchlists, notifications, market, mimi, options, macro, finance_digest, stock_digest, news, ai_analysis, ai_report, sectors, war_room, opportunities, earnings_notes

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    asyncio.create_task(scheduler.scheduler_loop())
    asyncio.create_task(notifications.telegram_notification_loop())
    yield


app = FastAPI(title="Stock Valuation API", version="1.0.0", lifespan=lifespan)

# Chart payloads carry ~60 indicator series per bar — gzip keeps them small.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # Allow any loopback origin (Arthur.OS portal/gateway on 127.0.0.1:8080 / :9000,
    # plus other local app frontends) so the portal health probe isn't blocked.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"])
app.include_router(valuations.router, prefix="/api/valuations", tags=["valuations"])
app.include_router(industries.router, prefix="/api/industries", tags=["industries"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(compare.router, prefix="/api/compare", tags=["compare"])
app.include_router(screener.router, prefix="/api/screener", tags=["screener"])
app.include_router(data.router, prefix="/api/data", tags=["data"])
app.include_router(metrics_breakdown.router, prefix="/api/stocks", tags=["metrics-breakdown"])
app.include_router(statistics.router, prefix="/api/stocks", tags=["statistics"])
app.include_router(ratios.router, prefix="/api/stocks", tags=["ratios"])
app.include_router(forecast.router, prefix="/api/stocks", tags=["forecast"])
app.include_router(dcf.router, prefix="/api/stocks", tags=["dcf"])
app.include_router(scrape.router, prefix="/api/scrape", tags=["scrape"])
app.include_router(pe.router, prefix="/api/stocks", tags=["pe"])
app.include_router(chart.router, prefix="/api/stocks", tags=["chart"])
app.include_router(signals.router,   prefix="/api/signals",   tags=["signals"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(earnings.router,    prefix="/api/earnings",    tags=["earnings"])
app.include_router(scheduler.router,   prefix="/api/scheduler",   tags=["scheduler"])
app.include_router(summary.router,          prefix="/api/stocks",           tags=["summary"])
app.include_router(industry_compare.router, prefix="/api/industry-compare",  tags=["industry-compare"])
app.include_router(alerts.router,           prefix="/api/alerts",            tags=["alerts"])
app.include_router(watchlists.router,       prefix="/api/watchlists",        tags=["watchlists"])
app.include_router(notifications.router,    prefix="/api/notifications",     tags=["notifications"])
app.include_router(market.router,           prefix="/api/market",            tags=["market"])
app.include_router(mimi.router,            prefix="/api/mimi",              tags=["mimi"])
app.include_router(options.router,         prefix="/api/stocks",            tags=["options"])
app.include_router(macro.router,           prefix="/api/macro",             tags=["macro"])
app.include_router(sectors.router,         prefix="/api/sectors",           tags=["sectors"])
app.include_router(finance_digest.router,  prefix="/api/finance-digest",    tags=["finance-digest"])
app.include_router(stock_digest.router,     prefix="/api/stock-digest",      tags=["stock-digest"])
app.include_router(earnings_notes.router, prefix="/api/earnings-notes", tags=["earnings-notes"])
app.include_router(news.router,             prefix="/api/stocks",            tags=["news"])
app.include_router(ai_analysis.router,      prefix="/api/stocks",            tags=["ai-analysis"])
app.include_router(ai_report.router,        prefix="/api/ai-report",         tags=["ai-report"])
app.include_router(war_room.router,         prefix="/api/war-room",          tags=["war-room"])
app.include_router(opportunities.router,    prefix="/api/opportunities",     tags=["opportunities"])


@app.api_route("/api/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}
