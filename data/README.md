# Data directory

This directory separates safe, curated reference data from local runtime state.

## Tracked files

- `ticker_sectors.json` — ticker-to-sector reference mapping.
- `etf_holdings.json` — sector ETF constituent reference data.
- `examples/` — sanitized templates for local portfolio features.

## Local-only files

The following files may contain positions, account names, cost bases, deposits,
or personal watchlists. They are intentionally ignored by Git:

- `portfolio.csv`
- `portfolio_meta.json`
- `portfolio_deposits.json`
- `alerts.json`
- `watchlists.json`

To enable the portfolio features, copy only the templates you need:

```bash
cp data/examples/portfolio.example.csv data/portfolio.csv
cp data/examples/portfolio_meta.example.json data/portfolio_meta.json
cp data/examples/portfolio_deposits.example.json data/portfolio_deposits.json
cp data/examples/alerts.example.json data/alerts.json
cp data/examples/watchlists.example.json data/watchlists.json
```

Replace the example values locally. Do not force-add these destination files.

## Generated data

Scraped snapshots, AI reports, databases, valuation workbooks, caches, and
pipeline state are reproducible runtime artifacts. Their locations are listed
in `.gitignore`; generate them with the relevant command documented in
[`scripts/README.md`](../scripts/README.md).
