# Contributing to Stock Radar

Thank you for improving Stock Radar. Keep pull requests focused, reviewable,
and safe for a local-first financial application.

## Development setup

```bash
uv sync --dev --frozen
npm --prefix frontend ci
cp backend/.env.example backend/.env
uv run alembic upgrade head
```

Start the complete workspace with `./start.sh`, or run the API and frontend
separately as described in the main README.

## Before opening a pull request

Run the same checks as CI:

```bash
uv run ruff check .
uv run pytest
npm --prefix frontend run lint -- --quiet
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend audit --omit=dev
```

Also run `git diff --check` and inspect `git status` for generated artifacts or
local financial data.

## Change guidelines

- Add or update tests when domain behavior changes.
- Include an Alembic revision for every database schema change.
- Keep API contracts typed and preserve backward compatibility where practical.
- Keep browser requests in the shared frontend API client.
- Document new operational scripts in `scripts/README.md`.
- Update screenshots only when the visible product behavior materially changes.

## Commit style

Use small, imperative commits with a conventional prefix when possible:

```text
feat(api): add portfolio exposure endpoint
fix(frontend): preserve ticker selection on refresh
docs: explain local data initialization
chore(repo): remove generated artifacts
```

## Data and credentials

Never commit `.env` files, API tokens, service-account credentials, databases,
real portfolio exports, cost bases, deposit history, generated reports, or
valuation workbooks. Use the sanitized templates in `data/examples/` when a
fixture is needed.
