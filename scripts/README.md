# Operational scripts

The `scripts/` directory contains executable workflows that sit above the
reusable packages in `src/` and `backend/`. Run commands from the repository
root so imports and data paths resolve consistently.

## Daily operations

| Command | Purpose |
|---|---|
| `uv run python scripts/daily_analysis.py` | Run the legacy daily analysis entry point. |
| `uv run python scripts/war_room.py` | Build the portfolio decision briefing. |
| `uv run python scripts/war_room_report.py` | Render the structured War Room response. |
| `uv run python scripts/market_scan.py` | Rank discovery-universe opportunities. |
| `uv run python scripts/market_news.py` | Build the market and holdings news digest. |
| `uv run python scripts/daily_manifest.py` | Verify expected daily pipeline outputs. |

Supporting modules such as `decision_ledger.py`, `exec_state.py`,
`pipeline_state.py`, `portfolio_risk.py`, `report_guard.py`, `report_meta.py`,
and `zh_terms.py` enforce continuity, safety, freshness, and report contracts.

## Data refresh and ingestion

| Command | Purpose |
|---|---|
| `uv run python scripts/refresh_prices.py` | Refresh cached market prices. |
| `uv run python scripts/refresh_discovery_universe.py` | Rebuild the discovery universe cache. |
| `uv run python scripts/regenerate_forecast.py` | Regenerate analyst forecast snapshots. |
| `uv run python scripts/rebuild_ai_overview.py` | Refresh stored AI overview cards. |
| `uv run python scripts/backfill_stockanalysis.py` | Backfill StockAnalysis datasets. |
| `uv run python scripts/scrape_forecast.py` | Fetch forecast data. |
| `uv run python scripts/scrape_income.py` | Fetch income statements. |
| `uv run python scripts/scrape_cashflow.py` | Fetch cash-flow statements. |
| `uv run python scripts/scrape_metrics.py` | Fetch company metrics. |
| `uv run python scripts/scrape_ratios.py` | Fetch financial ratios. |
| `uv run python scripts/scrape_statistics.py` | Fetch company statistics. |
| `uv run python scripts/scrape_fullratio_pe.py` | Refresh historical valuation ratios. |

Scraped outputs are local runtime data and are intentionally excluded from Git.

## Research utilities

| Command | Purpose |
|---|---|
| `uv run python scripts/compute_dcf.py` | Calculate DCF valuation scenarios. |
| `uv run python scripts/backtest_dcf.py` | Backtest DCF assumptions and outputs. |
| `uv run python scripts/cagr_calculator.py` | Calculate historical growth rates. |
| `uv run python scripts/export_mimi_json.py` | Export the local Mimi strategy source. |
| `uv run python scripts/sync_vault_notes.py` | Synchronize local research notes. |

## Diagnostics

`check_earnings_page.py`, `check_quarterly.py`, and
`test_forecast_fallback.py` are manual diagnostic commands. Automated tests
belong in `tests/` and run with `uv run pytest`.

## Earnings workflow

The multi-stage earnings pipeline lives in `scripts/earnings/`. Its inputs,
commands, validation rules, and synchronization flow are documented in
[`scripts/earnings/README.md`](earnings/README.md).

## Adding a script

Prefer extending an existing workflow or reusable service first. If a new
entry point is necessary, give it a narrow responsibility, add a module
docstring, expose a `main()` function, return a non-zero exit code on failure,
and document its command in this file.
