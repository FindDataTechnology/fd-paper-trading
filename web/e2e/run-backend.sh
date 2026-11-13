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
# No market-data selector: an empty MARKET_DATA_SOURCE_ID *is* the simulated
# provider (spec: market-data "Provider is swappable"). The old
# MARKET_DATA_PROVIDER selector was removed in add-price-supply-and-caliber task
# 6.4 and is now a **startup error** — keeping it here meant the Playwright suite
# could never boot a backend, so the e2e job exercised nothing at all.
# OPENAI_API_KEY / OPENAI_API_BASE went with the bundled CopilotKit runtime
# (commit d3dec0b) and have no consumer left; they are not set here either.

# --- fd-quant compute plane -------------------------------------------------
# The backtest path refuses to fabricate stats without the compute plane
# (api/routes/strategies.py turns ComputePlaneUnavailable into EvaluationError),
# so an e2e that runs a backtest needs a live one. This mirrors what
# tests/conftest.py does for the pytest suite — including the refusal to skip:
# an unreachable plane must fail the run, not quietly turn the backtest spec
# into an empty pass.
FD_QUANT="${FD_QUANT_REPO:-../fd-quant}"
if [ ! -f "$FD_QUANT/fd_quant/compute/server.py" ]; then
  echo "fd-quant checkout not found at $FD_QUANT (set FD_QUANT_REPO)" >&2
  exit 1
fi
PLANE_PY="$FD_QUANT/.venv/bin/python"
[ -x "$PLANE_PY" ] || PLANE_PY="python3"
PLANE_PORT="${FD_QUANT_COMPUTE_PORT:-8799}"
( cd "$FD_QUANT" && "$PLANE_PY" -m fd_quant.compute.server --http "$PLANE_PORT" ) &
PLANE_PID=$!
trap 'kill "$PLANE_PID" 2>/dev/null || true' EXIT
export FD_QUANT_COMPUTE_ENDPOINT="http://127.0.0.1:$PLANE_PORT/mcp"
# Wait for the port to answer. Probe with curl and judge on the HTTP status, not
# on a /dev/tcp test: the latter reports CLOSED for a socket that is listening
# but not yet accepting, and any status other than 000 proves it answered.
for _ in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PLANE_PORT/mcp" || true)
  [ "$code" != "000" ] && break
  sleep 1
done
if [ "${code:-000}" = "000" ]; then
  echo "fd-quant compute plane did not answer on :$PLANE_PORT" >&2
  exit 1
fi

# Initialize the schema in the file-based SQLite DB. The models module must be
# imported so its tables register with Base.metadata before create_all.
uv run python -c "import asyncio; import fd_paper_trading.persistence.models; from fd_paper_trading.persistence.base import create_database, init_db; asyncio.run(init_db(create_database('sqlite+aiosqlite:///./e2e.db')))"

# Serve the built SPA + API + /ws on a dedicated e2e port (8001) so the suite
# never reuses a developer's :8000 server (which may run a different config).
# No `exec`: the EXIT trap has to run so the compute plane child dies with us.
uv run uvicorn fd_paper_trading.main:app --host 127.0.0.1 --port 8001
