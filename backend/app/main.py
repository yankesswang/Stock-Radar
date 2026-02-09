from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import engine, Base
from .routers import stocks, valuations, industries, search, compare, screener, data, metrics_breakdown, statistics, ratios, forecast, dcf


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Stock Valuation API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
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


@app.get("/api/health")
def health():
    return {"status": "ok"}
