# StockRadar — Claude Instructions

## Starting the App

Always use `./start.sh` from the project root to start both services:

```bash
./start.sh
```

- **Backend**: `http://localhost:6000` (FastAPI + uvicorn via `uv`)
- **Frontend**: `http://localhost:4000` (Vite + React)

Note: under Arthur.OS the whole stack is started by the root `start-all.sh` (owned by
launchd), and StockRadar is served through the gateway at `/stocks/`. Running this
`start.sh` standalone kills whatever holds :6000/:4000 and opens its own tunnel, so
don't run it while Arthur.OS is up.

## Testing Services

After any backend change, verify the service is working before telling the user it's done:

```bash
# Health check
curl -s http://localhost:6000/api/health

# Test a specific endpoint (replace AAPL / path as needed)
curl -s http://localhost:6000/api/stocks/AAPL/summary | python3 -m json.tool | head -40
```

If the backend isn't running, start it with:

```bash
uv run python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 6000 --reload
```

## Project Structure

```
stock-radar/
├── start.sh                      # Launch both services
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI app + router registration
│   │   ├── config.py             # Settings (ports, paths, DB URL)
│   │   ├── routers/              # One file per feature area
│   │   └── models.py / schemas.py
│   └── stock_valuation.db        # SQLite DB
├── frontend/
│   ├── vite.config.js            # Proxy: /api → localhost:6000
│   └── src/
│       ├── pages/                # Full page components
│       └── components/           # Shared UI components
└── data/                         # Scraped JSON/CSV data files
    ├── statistics/               # {TICKER}_statistics_{date}.json
    ├── forecast/detail/          # {TICKER}_forecast_{date}.json
    ├── financials/               # {TICKER}_ratios_{date}.csv
    └── pe/fullratio/             # {TICKER}_fullratio_pe_{date}.json
```

## Key Conventions

- **uv** is the package/runtime manager — use `uv run python` instead of `.venv/bin/python`
- The `.venv` in this project may have a broken shebang path; always prefer `uv run`
- Backend runs from the **project root** (`backend.app.main:app`), not from inside `backend/`
- New routers must be imported and registered in `backend/app/main.py`
- Frontend proxy is set in `frontend/vite.config.js` — backend port is **6000** (override with `STOCKS_BACKEND_PORT`)
- Frontend port is **4000**
- Routing uses wouter with a runtime `base` set in `frontend/src/main.jsx`: `''` when served
  at `/` (direct :4000), `/stocks` when served through the gateway. New routes need no
  change; never hardcode `/stocks` into a `<Link>`.

## Data Scrapers

Run scrapers from the project root:

```bash
uv run python scripts/scrape_fullratio_pe.py --ticker AAPL
uv run python scripts/scrape_ratios.py --ticker AAPL
```
