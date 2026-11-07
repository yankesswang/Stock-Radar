from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .background import (
    start_background_jobs,
    stop_background_jobs,
    try_acquire_leader_lock,
)
from .config import settings
from .database import initialize_database
from .jobs import job_registry
from .observability import request_context_middleware
from .security import mutation_is_authorized
from .routers import stocks, valuations, industries, search, compare, screener, data, metrics_breakdown, statistics, ratios, forecast, dcf, scrape, pe, chart, signals, portfolio, earnings, scheduler, summary, industry_compare, alerts, watchlists, notifications, market, mimi, options, macro, finance_digest, stock_digest, market_news, news, ai_analysis, ai_report, sectors, war_room, opportunities, earnings_notes, segments, financials, stock_chat
from .routers import tabs

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    tasks = []
    leader_lock = None
    if settings.BACKGROUND_JOBS_ENABLED:
        leader_lock = try_acquire_leader_lock(settings.BACKGROUND_JOB_LOCK_FILE)
        if leader_lock is None:
            logger.warning("Background jobs disabled in this worker: leader lock is held")
        else:
            tasks = start_background_jobs(
                [
                    ("stock-radar-scheduler", scheduler.scheduler_loop),
                    ("stock-radar-notifications", notifications.telegram_notification_loop),
                ]
            )
    app.state.background_tasks = tasks
    app.state.background_job_leader = leader_lock is not None
    try:
        yield
    finally:
        await job_registry.shutdown()
        await stop_background_jobs(tasks)
        if leader_lock is not None:
            leader_lock.release()


app = FastAPI(title="Stock Valuation API", version="1.0.0", lifespan=lifespan)
app.middleware("http")(request_context_middleware)


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": f"http_{exc.status_code}", "message": message}},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )


@app.middleware("http")
async def protect_mutation_endpoints(request, call_next):
    client_host = request.client.host if request.client else None
    if not mutation_is_authorized(
        method=request.method,
        path=request.url.path,
        client_host=client_host,
        headers=request.headers,
        configured_token=settings.ADMIN_API_TOKEN,
    ):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "mutation_forbidden",
                    "message": "Write access requires loopback or a valid admin API token.",
                }
            },
        )
    return await call_next(request)

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
app.include_router(market_news.router,      prefix="/api/market-news",       tags=["market-news"])
app.include_router(earnings_notes.router, prefix="/api/earnings-notes", tags=["earnings-notes"])
app.include_router(segments.router,     prefix="/api/segments",       tags=["segments"])
app.include_router(financials.router,   prefix="/api/financials",     tags=["financials"])
app.include_router(news.router,             prefix="/api/stocks",            tags=["news"])
app.include_router(ai_analysis.router,      prefix="/api/stocks",            tags=["ai-analysis"])
app.include_router(ai_report.router,        prefix="/api/ai-report",         tags=["ai-report"])
app.include_router(war_room.router,         prefix="/api/war-room",          tags=["war-room"])
app.include_router(opportunities.router,    prefix="/api/opportunities",     tags=["opportunities"])
app.include_router(stock_chat.router,       prefix="/api/stock-chat",        tags=["stock-chat"])
app.include_router(tabs.router,             prefix="/api/stocks",            tags=["tabs"])


@app.api_route("/api/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}
