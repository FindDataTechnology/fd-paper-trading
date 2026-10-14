# fd-paper-trading: one process serves the API, /ws, and the built SPA.
# Build the SPA first (web/dist is copied in), then install the Python
# package from the lockfile. No third-party trading framework.

FROM node:20-slim AS webbuild
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

# uv for fast locked installs; no cache kept in the image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md LICENSE alembic.ini ./
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY --from=webbuild /build/dist ./web/dist

RUN uv sync --frozen --no-dev --no-install-project \
    && uv sync --frozen --no-dev

# Non-root user; alembic migrations run at container start (entrypoint).
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
