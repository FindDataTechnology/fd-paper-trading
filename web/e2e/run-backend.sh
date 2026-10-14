#!/usr/bin/env bash
# Start the fd-paper-trading backend for the Playwright e2e suite.
#
# - Builds the SPA if web/dist is missing (FastAPI serves it at / in production).
# - Uses a file-based SQLite DB (no Postgres required) and the simulated
#   market-data provider (no broker / network).
# - Initializes the schema via Base.metadata.create_all (same tables Alembic
#   creates), then starts the app on :8000.
#
# Run from the repo root. Playwright's webServer invokes this with cwd=repo root.
set -euo pipefail

# Fresh DB each start so the suite is deterministic (no leftover users).
rm -f e2e.db

# Build the SPA if it has not been built yet.
if [ ! -f web/dist/index.html ]; then
  (cd web && npm run build)
fi

export DATABASE_URL="sqlite+aiosqlite:///./e2e.db"
export MARKET_DATA_PROVIDER="simulated"
export OPENAI_API_KEY="test-mock-key"
export OPENAI_API_BASE="http://localhost:8001/mock-openai"

# Initialize the schema in the file-based SQLite DB. The models module must be
# imported so its tables register with Base.metadata before create_all.
uv run python -c "import asyncio; import fd_paper_trading.persistence.models; from fd_paper_trading.persistence.base import create_database, init_db; asyncio.run(init_db(create_database('sqlite+aiosqlite:///./e2e.db')))"

# Serve the built SPA + API + /ws on a dedicated e2e port (8001) so the suite
# never reuses a developer's :8000 server (which may run a different config).
exec uv run uvicorn fd_paper_trading.main:app --host 127.0.0.1 --port 8001
