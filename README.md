# PE Valuation Analysis Tool

A Python-based stock valuation analyzer that scrapes financial data from multiple sources and generates comprehensive valuation reports. It evaluates whether stocks are overvalued or undervalued based on PE ratios, growth forecasts, and historical metrics across 150+ stocks in 18 industry sectors.

**Now includes a full-stack web application** with a React frontend and FastAPI backend for interactive valuation browsing.

## Features

- **Multi-source Data Scraping**: Collects financial data from StockAnalysis.com, MacroTrends.net, and Yahoo Finance
- **Comprehensive Metrics**: 60+ financial ratios, 5-year historical PE medians, revenue/EPS growth forecasts
- **Growth-based Valuation**: Estimates fair value using PE multipliers calibrated to EPS growth rates
- **Industry-organized Reports**: Excel output with one worksheet per sector, transposed for easy comparison
- **Parallel Scraping**: Runs ratio, PE, and forecast scrapers concurrently via threading
- **Web Application**: React + FastAPI web service for searching stocks and viewing valuations
- **SQLite Database**: Scraped data is automatically stored in SQLite for the web service

## Quick Start - Web Application

### 1. Install Dependencies

```bash
# Backend
uv pip install -r requirements.txt
uv pip install -r backend/requirements.txt

# Frontend
cd frontend && npm install && cd ..
```

### 2. Load Data into Database

```bash
uv run python -m backend.load_data
```

### 3. Run the Application

```bash
# Terminal 1: Backend API (http://localhost:8000)
uv run uvicorn backend.app.main:app --reload

# Terminal 2: Frontend (http://localhost:5173)
cd frontend && npm run dev
```

Open [http://localhost:5173](http://localhost:5173) to use the web app.

## Project Structure

```
PE_valuation/
├── main.py                         # Entry point (analysis, scraping, single-stock test)
├── requirements.txt                # Python dependencies
├── src/                            # Core application package
│   ├── __init__.py                 # PROJECT_ROOT, DATA_DIR, OUTPUT_DIR constants
│   ├── config.py                   # Stock lists, ticker mappings, metric names
│   ├── utils.py                    # HTTP fetching, HTML parsing, IQR filtering
│   ├── runner.py                   # Parallel scraper executor (saves to JSON + DB)
│   ├── db_service.py               # Database save functions for scrapers
│   ├── scrapers/                   # Data collection modules
│   │   ├── ratio_scraper.py        # 60+ financial metrics from StockAnalysis.com
│   │   ├── pe_scraper.py           # 5-year historical PE ratios from MacroTrends.net
│   │   ├── forecast_scraper.py     # Revenue/EPS growth forecasts from StockAnalysis.com
│   │   ├── data_scraper.py         # Combined scraper using pre-collected data + yfinance
│   │   ├── financials_scraper.py   # Balance sheet & income statement data
│   │   └── metrics_scraper.py      # Additional quarterly/annual metrics
│   └── analysis/                   # Valuation logic
│       └── valuation_analyzer.py   # Core valuation engine (no scraping, data-only)
├── scripts/                        # Utility & standalone scripts
│   ├── scrape_ratios.py            # Financial ratios scraper (saves to CSV + DB)
│   ├── scrape_metrics.py           # Metrics breakdown scraper
│   ├── scrape_statistics.py        # Key statistics scraper
│   └── ...
├── data/                           # Scraped data (JSON/CSV, timestamped)
│   ├── pe/                         # stock_list_PE_YYYY-MM-DD.json
│   ├── ratio/                      # stock_list_metrics_YYYY-MM-DD.json
│   ├── forecast/                   # stock_list_forecasts_YYYY-MM-DD.json
│   ├── financials/                 # Per-ticker CSV: ratios, quarterly/annual financials
│   └── ...
├── backend/                        # FastAPI backend serving data to frontend
│   ├── app/
│   │   ├── main.py                 # App setup, CORS, router registration
│   │   ├── database.py             # SQLAlchemy + SQLite configuration
│   │   ├── models.py               # ORM models (stocks, metrics, valuations, etc.)
│   │   ├── schemas.py              # Pydantic response schemas
│   │   └── routers/                # API endpoint modules
│   │       ├── valuations.py       # /api/valuations - valuation history
│   │       ├── stocks.py           # /api/stocks - stock listing & detail
│   │       ├── search.py           # /api/search - live ticker search
│   │       └── ...
│   ├── load_data.py                # Import JSON data into SQLite
│   └── stock_valuation.db          # SQLite database
├── frontend/                       # React + Vite SPA
│   └── src/
│       ├── App.jsx                 # Routing (wouter)
│       ├── pages/
│       │   ├── Home.jsx            # Landing page with search
│       │   ├── ValuationList.jsx   # Screener table with sorting/filtering
│       │   └── StockDetail.jsx     # Per-stock valuation chart
│       └── components/
│           ├── Navbar.jsx
│           └── SearchBar.jsx
└── valuation/                      # Generated Excel reports
    └── stock_data_YYYY-MM-DD.xlsx
```

## Installation

### Prerequisites

- Python 3.8+
- Node.js 18+ (for frontend)
- [uv](https://github.com/astral-sh/uv) - Fast Python package and project manager
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### Setup

```bash
git clone <repository-url>
cd PE_valuation

uv venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

uv pip install -r requirements.txt
uv pip install -r backend/requirements.txt

# Frontend
cd frontend && npm install && cd ..
```

## Usage

All commands are run from the **project root** directory using `uv run`.

### Run Valuation Analysis (using existing data)

```bash
uv run python main.py
```

Loads the latest data from `data/` and generates an Excel report in `valuation/`.

### Scrape Data + Analyze + Save to Database

```bash
uv run python main.py --scrape
```

Runs all three scrapers in parallel, saves to JSON files AND SQLite database, then performs the valuation analysis.

### Run Scrapers Only (with Database Save)

```bash
uv run python -m src.runner                         # All scrapers in parallel + DB save
uv run python -m src.scrapers.ratio_scraper         # Ratio/metrics only
uv run python -m src.scrapers.pe_scraper            # PE ratios only
uv run python -m src.scrapers.forecast_scraper      # Forecasts only
```

All scrapers now automatically save to both JSON files and the SQLite database.

### Scrape Financial Ratios (per-ticker)

```bash
# Single stock (saves to CSV + database)
uv run python scripts/scrape_ratios.py --ticker AAPL

# All stocks
uv run python scripts/scrape_ratios.py --all --workers 3
```

### Scrape Statistics (per-ticker)

```bash
# Single stock (saves to JSON + database)
uv run python scripts/scrape_statistics.py AAPL

# All stocks
uv run python scripts/scrape_statistics.py
```

Scrapes ~104 key statistics across 19 sections (valuation, efficiency, returns, etc.).

### Scrape Metrics Breakdown (per-ticker)

```bash
# Single stock (saves to CSV + database)
uv run python scripts/scrape_metrics.py AAPL

# All stocks
uv run python scripts/scrape_metrics.py
```

Scrapes revenue by segment, geography, gross profit breakdown, etc.

### Test a Single Stock

```bash
uv run python main.py --test AAPL
```

### Web Application

```bash
# Start backend API
uv run uvicorn backend.app.main:app --reload

# Start frontend (in separate terminal)
cd frontend && npm run dev
```

### Load Historical Data into Database

```bash
# Load all JSON data from data/ into SQLite
uv run python -m backend.load_data

# Force reload (drop existing data first)
uv run python -m backend.load_data --force
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/search?q=AAPL` | Search stocks by ticker/name |
| `GET /api/valuations` | List all valuations (paginated) |
| `GET /api/valuations/{ticker}/history` | Valuation history for a stock |
| `GET /api/stocks` | List all stocks |
| `GET /api/industries` | List all industries |

## Data Sources

| Source | Data Collected |
|--------|---------------|
| **StockAnalysis.com** | Financial ratios (PE, ROE, debt, yields), growth forecasts, EPS estimates |
| **MacroTrends.net** | 5-year historical PE ratios |
| **Yahoo Finance** (yfinance) | Current stock prices, beta values |

## Valuation Methodology

### Growth-based PE Estimation

The tool estimates fair PE ratios based on 5-year EPS growth forecasts:

| EPS Growth Rate | PE Multiplier |
|----------------|---------------|
| 0-5%           | 0.8x          |
| 5-10%          | 1.0x          |
| 10-15%         | 1.1x          |
| 15-20%         | 1.2x          |
| 20-30%         | 1.5x          |
| 30%+           | 2.0x          |

### Output Metrics (per stock)

- Revenue & EPS Growth Forecast (5Y)
- Estimated PE and fair prices (current + next year)
- 5-year PE median and median-based fair prices
- Market cap and current stock price
- Valuation status (overvalued/undervalued) with percentage difference

## Industry Sectors

The tool tracks 150+ stocks across 18 sectors:

Big Tech, Airlines/Cruises, Banks, Healthcare, Payment, Retail, Food/Beverage, Semiconductors, Oil, Travel/Hotels, Industrial, SaaS, Software, Memory/Storage, Cybersecurity, AI Infrastructure, Robotics/Automation, Growth Stocks

Edit `src/config.py` to add/remove stocks or modify industry groupings.

## Configuration

- **Stock lists & ticker mappings**: `src/config.py`
- **Scraper settings** (delays, headers): Individual scraper files in `src/scrapers/`
- **Valuation multipliers**: `src/analysis/valuation_analyzer.py`
- **Database settings**: `backend/app/config.py`
- **CORS origins**: `backend/app/config.py` (defaults to localhost:5173)

## Notes

- Core scrapers (`src/runner.py`) use 10-30 second random delays between requests
- Per-ticker scrapers (`scripts/`) default to 5-15 second delays, configurable via `--delay-min`/`--delay-max`
- Keep parallel workers low (2-3 max) to avoid getting rate-limited
- Data files are timestamped; the analyzer and API always load the most recent file
- A full scraping run for all 181 stocks takes approximately 30-60 minutes
- Scrapers automatically save to both JSON and SQLite database

## Dependencies

### Python
- `fastapi` - Web framework for API
- `uvicorn` - ASGI server
- `sqlalchemy` - Database ORM
- `pydantic` - Data validation
- `pandas` - Data manipulation and Excel export
- `beautifulsoup4` - HTML parsing
- `requests` - HTTP requests
- `yfinance` - Yahoo Finance API
- `numpy` - Numerical operations

### Frontend (Node.js)
- `react` - UI framework
- `vite` - Build tool
- `axios` - HTTP client
- `wouter` - Routing
- `recharts` - Charts
- `lucide-react` - Icons

