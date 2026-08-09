# StockRadar

**Valuation · Signals · Portfolio — in one place.**

A full-stack stock analysis platform that tracks 150+ stocks across 18 sectors. StockRadar scrapes financial data from multiple sources, computes PE-based fair values, and surfaces everything through an interactive React web app — with candlestick charts, a technical signal watchlist, and live portfolio tracking via Google Sheets.

## Features

| Category | Details |
|----------|---------|
| **Valuation** | PE-based fair price, 5-year PE median, current/next-year EPS estimates, over/undervalued status |
| **Metrics** | 60+ financial ratios — margins, ROE/ROA/ROIC, debt, dividends, short interest |
| **Charts** | Candlestick + 14 overlays / 18 oscillator panels — Ichimoku, SuperTrend, Keltner, Donchian, PSAR, anchored VWAP, ADX/DMI, TTM Squeeze, Stoch RSI, MFI, CMF, RS vs SPY ([usage guide](docs/indicators.md)) |
| **Risk analytics** | EWMA volatility, rolling beta/alpha, percentile ranks vs own history, ATR-based stops, pivot points, volume profile, auto-Fibonacci, aggregated technical rating |
| **Support & Resistance** | Swing-high/low detection with clustering, drawn as solid price lines |
| **Signal Watchlist** | Parallel scan of all tracked tickers for RSI/KDJ/MACD/BB signals |
| **Portfolio** | Google Sheets sync — live P&L, valuation overlay, sector allocation |
| **Search** | Global search from any page (⌘K), debounced, keyboard-navigable |
| **Screener** | Sortable/filterable table across all valuation columns |
| **DCF** | Discounted cash flow calculator with sensitivity matrix |
| **Scraping** | Parallel scrapers with caching (StockAnalysis.com, MacroTrends, yfinance) |

## Quick Start

### 1. Clone & install

```bash
git clone <repo-url>
cd stock-radar

# Python backend
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install -r backend/requirements.txt

# Frontend
cd frontend && npm install && cd ..
```

### 2. Configure (optional — for portfolio sync)

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and set GSHEET_ID to your Google Sheet ID
```

Your Google Sheet must be publicly readable (Share → Anyone with the link → Viewer).
See [Sheet layout](#google-sheet-layout) for the expected column format.

### 3. Load data into SQLite

```bash
uv run python -m backend.load_data
```

### 4. Run the app

```bash
# Terminal 1 — API (http://localhost:2000)
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 2000 --reload

# Terminal 2 — Frontend (http://localhost:5000)
cd frontend && npm run dev -- --port 5000
```

Or use the convenience script:

```bash
bash start.sh
```

## Project Structure

```
stock-radar/
├── backend/                    # FastAPI backend
│   ├── .env                    # ← gitignored: your personal config (GSHEET_ID, etc.)
│   ├── .env.example            # Template — copy to .env and fill in values
│   ├── requirements.txt
│   ├── load_data.py            # Import JSON snapshots → SQLite
│   └── app/
│       ├── config.py           # Settings (reads backend/.env)
│       ├── database.py         # SQLAlchemy + SQLite
│       ├── models.py           # ORM models
│       └── routers/
│           ├── stocks.py       # /api/stocks
│           ├── valuations.py   # /api/valuations
│           ├── search.py       # /api/search
│           ├── chart.py        # /api/stocks/{ticker}/chart
│           ├── signals.py      # /api/signals/watchlist
│           ├── portfolio.py    # /api/portfolio + /api/portfolio/gsheet
│           ├── screener.py     # /api/screener
│           ├── dcf.py          # /api/stocks/{ticker}/dcf
│           ├── pe.py           # /api/stocks/{ticker}/pe
│           ├── ratios.py       # /api/stocks/{ticker}/ratios
│           ├── forecast.py     # /api/stocks/{ticker}/forecast
│           ├── compare.py      # /api/compare
│           └── scrape.py       # /api/scrape (trigger on-demand)
│
├── frontend/                   # React + Vite SPA
│   └── src/
│       ├── App.jsx             # Routing (wouter)
│       ├── components/
│       │   ├── Navbar.jsx      # Top nav with global search
│       │   ├── GlobalSearch.jsx# ⌘K search dropdown
│       │   └── PriceChartPanel.jsx  # Candlestick + indicator chart
│       └── pages/
│           ├── Home.jsx
│           ├── ValuationList.jsx    # Screener table
│           ├── StockDetail.jsx      # Per-stock detail + embedded chart
│           ├── StockMetrics.jsx
│           ├── StockRatios.jsx
│           ├── StockForecast.jsx
│           ├── StockDCF.jsx
│           ├── StockPE.jsx
│           ├── StockStatistics.jsx
│           ├── SignalWatchlist.jsx  # Technical signal scanner
│           ├── GSheetPortfolio.jsx  # Google Sheets portfolio view
│           ├── PortfolioList.jsx    # Manual portfolio list
│           └── PortfolioDetail.jsx  # Manual portfolio detail
│
├── src/                        # Core Python package (scrapers + analysis)
│   ├── config.py               # Stock lists, sector definitions
│   ├── runner.py               # Parallel scraper executor
│   ├── scrapers/
│   │   ├── ratio_scraper.py
│   │   ├── pe_scraper.py
│   │   ├── forecast_scraper.py
│   │   └── statistics_scraper.py
│   └── analysis/
│       └── valuation_analyzer.py
│
├── scripts/                    # Standalone utility scripts
├── docs/                       # Technical documentation
│   ├── DCF_README.md
│   ├── indicators.md           # ← 進階指標使用說明（怎麼看、怎麼搭配）
│   └── support_resistance.md
├── data/                       # Scraped JSON snapshots (gitignored)
│   ├── pe/
│   ├── ratio/
│   └── forecast/
├── valuation/                  # Generated Excel reports (gitignored)
├── main.py                     # CLI entry point
└── requirements.txt
```

## API Reference

### Stocks & Valuations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/search?q=AAPL` | Ticker/name search |
| GET | `/api/stocks` | All stocks |
| GET | `/api/valuations` | Valuation list (paginated, filterable) |
| GET | `/api/stocks/{ticker}` | Stock detail |
| GET | `/api/stocks/{ticker}/chart` | OHLCV + indicators + S/R levels |
| GET | `/api/stocks/{ticker}/ratios` | Financial ratios |
| GET | `/api/stocks/{ticker}/forecast` | EPS/revenue forecasts |
| GET | `/api/stocks/{ticker}/pe` | Historical PE data |
| GET | `/api/stocks/{ticker}/dcf` | DCF valuation |
| GET | `/api/stocks/{ticker}/statistics` | Key statistics |
| GET | `/api/compare?tickers=AAPL,MSFT` | Side-by-side comparison |
| GET | `/api/screener` | Screener with filters |

### Signals

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/signals/watchlist` | Scan all tickers for technical signals (cached 5 min) |
| DELETE | `/api/signals/watchlist/cache` | Bust the signal cache |

### Portfolio

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/portfolio/gsheet` | Read portfolio from Google Sheet + valuation overlay |
| GET | `/api/portfolio` | List manual portfolios |
| POST | `/api/portfolio` | Create portfolio |
| GET | `/api/portfolio/{id}` | Portfolio detail with live prices |
| PUT | `/api/portfolio/{id}` | Update portfolio |
| DELETE | `/api/portfolio/{id}` | Delete portfolio |
| POST | `/api/portfolio/{id}/holdings` | Add holding |
| PUT | `/api/portfolio/{id}/holdings/{hid}` | Update holding |
| DELETE | `/api/portfolio/{id}/holdings/{hid}` | Delete holding |

## Scraping

### Refresh all data

```bash
uv run python main.py --scrape      # Scrape + analyze + save to DB
uv run python main.py               # Analyze with existing data only
```

### Individual scrapers

```bash
uv run python -m src.runner                          # All scrapers in parallel
uv run python -m src.scrapers.ratio_scraper          # Ratios/metrics only
uv run python -m src.scrapers.pe_scraper             # PE history only
uv run python -m src.scrapers.forecast_scraper       # Forecasts only
```

### Pro-locked forecast years

StockAnalysis shows the fiscal-year labels but blanks FY+1 and beyond behind
Pro, so `next_year_eps` used to come back empty. `src/scrapers/forecast_fallback.py`
fills those cells from two keyless sources — Nasdaq's analyst earnings-forecast
API (EPS, ~FY+3) and yfinance `revenue_estimate` (revenue, FY+1 only).

Values are **rebased**, not pasted: the fallback's year-over-year change is
applied to the StockAnalysis level. The sources disagree on EPS definition
(ADBE FY2026: StockAnalysis 24.41 vs Nasdaq 19.81) and `compute_valuation`
multiplies `next_year_eps` by a PE median built from StockAnalysis's own
history, so a raw paste would skew the fair price by the definition gap.

Every filled cell is recorded under `fallback` in the saved JSON with its raw
source value, rebasing method, and analyst count. Fills resting on fewer than
3 analysts carry `thin_coverage: true`. Disable with `--no-fallback`.

Around a fiscal-year rollover the source may have already dropped the one year
StockAnalysis still shows — a June filer in August, say — leaving no overlapping
year to calibrate against. Those years are left empty by default rather than
guessing at the level; `--allow-unanchored` takes them raw instead, marked
`method: "raw"` with `anchor_year: null` and a loud note. Don't feed a raw fill
into a PE-median fair value without checking the EPS definition first.

```bash
uv run python scripts/scrape_forecast.py --ticker ADBE   # shows what was filled
uv run python scripts/test_forecast_fallback.py          # offline checks
```

The Growth tab labels these columns with the actual fiscal year (`EPS 2027`,
not `EPS Next Year`) and puts the fill's provenance in the ⓘ hint, so a filled
number is never indistinguishable from a StockAnalysis one.

### Load snapshots into SQLite

```bash
uv run python -m backend.load_data           # Latest snapshot only
uv run python -m backend.load_data --force   # Drop & reload all
```

## Valuation Methodology

Fair price is estimated two ways:

**1. PE Median Fair Value** — `pe_median_5y × current_eps`

**2. Growth-based Fair Value** — PE multiplier applied to current/next-year EPS estimates:

| 5Y EPS Growth | PE Multiplier |
|---------------|---------------|
| < 5% | 0.8× |
| 5–10% | 1.0× |
| 10–15% | 1.1× |
| 15–20% | 1.2× |
| 20–30% | 1.5× |
| > 30% | 2.0× |

Stocks are classified as **Undervalued**, **Fairly Valued**, or **Overvalued** based on the percentage difference between current price and fair price.

## Google Sheet Layout

The portfolio sync (`/api/portfolio/gsheet`) expects a public Google Sheet with this layout:

| Col | Content |
|-----|---------|
| A (0) | Account section header: `etoro` or `firstrade` |
| B (1) | Ticker symbol (e.g. `AAPL`) |
| C (2) | Current price |
| E (4) | Number of shares |
| I (8) | Position market value |
| J (9) | P&L % |
| K (10) | Portfolio weight % |

Section headers (`etoro`, `firstrade`) can appear on the same row as the first ticker in that section.
Rows where column B does not match `^[A-Z]{1,5}$` are ignored.

Set your Sheet ID in `backend/.env`:
```
GSHEET_ID=your_google_sheet_id_here
```

## Data Sources

| Source | Data |
|--------|------|
| StockAnalysis.com | 60+ financial ratios, EPS/revenue forecasts |
| MacroTrends.net | 5-year historical PE ratios |
| Yahoo Finance (yfinance) | Live prices, beta, OHLCV for charts |

## Configuration

| File | Purpose |
|------|---------|
| `src/config.py` | Stock lists, sector definitions, 150+ tickers |
| `backend/app/config.py` | DB URL, CORS origins, fiscal year, `GSHEET_ID` |
| `backend/.env` | Personal overrides — **gitignored, never commit** |
| `src/analysis/valuation_analyzer.py` | PE multiplier table |

## Tech Stack

**Backend:** Python 3.11+, FastAPI, SQLAlchemy, SQLite, yfinance, pandas, BeautifulSoup
**Frontend:** React 18, Vite, Tailwind CSS, Recharts, lightweight-charts v5, wouter, axios, lucide-react

## Notes

- Signal watchlist scans all ~182 tracked tickers in parallel and caches results for 5 minutes
- Full scraping run (all 182 stocks) takes 30–60 minutes due to polite rate-limiting delays
- Chart data is fetched live from yfinance; S/R levels are computed server-side from 5 years of daily OHLCV
- The SQLite database (`backend/stock_valuation.db`) is gitignored — it contains your personal portfolio data
