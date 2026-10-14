#!/usr/bin/env bash
# Run Alembic migrations to head, then serve. The DB is per-service and
# single-instance, so a blocking migrate on boot is safe (no concurrent
# writers during rollout).
set -euo pipefail
cd /app
uv run --no-dev alembic upgrade head
exec uv run --no-dev fd-paper-trading
