"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import get_settings
from ..execution import PaperExecutionService
from ..marketdata import MarketDataProvider, build_market_data_provider
from ..persistence.base import Database, create_database
from ..realtime import ConnectionManager
from ..strategy.runner import PaperRunSupervisor
from .errors import (
    APIError,
    api_error_handler,
    http_exception_handler,
    validation_exception_handler,
)
from .routes import accounts, auth, groups, instruments, orders, strategies
from .routes import ws as ws_route


def _default_web_dist() -> Path:
    """Default location of the built SPA.

    Overridable via the ``FD_WEB_DIST`` environment variable (useful for
    deployments that build the UI elsewhere); defaults to ``<repo>/web/dist``.
    """
    import os

    override = os.environ.get("FD_WEB_DIST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.realtime.start()
    # Resume live paper runs that were active when the process last exited
    # (design D8 recovery); their tasks poll for new daily bars.
    await app.state.strategy_runner.recover()
    try:
        yield
    finally:
        # Cancel runner tasks without touching run status so the next boot's
        # recover() resumes them.
        await app.state.strategy_runner.shutdown()
        await app.state.realtime.stop()


def create_app(
    database_url: str | None = None,
    market_data_provider: MarketDataProvider | None = None,
    web_dist_dir: Path | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    ``database_url`` defaults to ``settings.database_url`` (Postgres in prod,
    sqlite for tests). ``market_data_provider`` defaults to the simulated
    provider; tests pass their own or rely on the default. ``web_dist_dir``
    is the built SPA directory to serve at ``/`` (production); when it exists,
    unmatched non-API GET paths fall back to ``index.html`` for client-side
    routing. Defaults to ``<repo>/web/dist``.
    """
    settings = get_settings()
    url = database_url or settings.database_url

    app = FastAPI(
        title="fd-trade paper trading API",
        version="0.1.0",
        description="Multi-tenant stock paper-trading service built on Nautilus Trader.",
        lifespan=lifespan,
    )
    app.state.db: Database = create_database(
        url, pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow
    )
    app.state.settings = settings
    if market_data_provider is not None:
        provider = market_data_provider
    else:
        provider = build_market_data_provider(settings)
    app.state.market_data_provider = provider
    app.state.execution_service = PaperExecutionService(
        provider=provider,
        rate_limit_per_second=settings.order_rate_per_second,
        rate_limit_burst=settings.order_rate_burst,
    )
    app.state.realtime = ConnectionManager(provider=provider)
    app.state.strategy_runner = PaperRunSupervisor(app)

    # Standardized error responses.
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # Versioned API surface, registered before the SPA fallback so they win.
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(accounts.router, prefix="/api/v1")
    app.include_router(orders.router, prefix="/api/v1")
    app.include_router(strategies.router, prefix="/api/v1")
    app.include_router(groups.router, prefix="/api/v1")
    app.include_router(instruments.router, prefix="/api/v1")
    app.include_router(ws_route.router)  # mounts /ws at root

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok"}

    # ---- SPA static serving (production) ----
    # Only mounted when the built UI exists, so dev/tests without a build are
    # unaffected. API and /ws routes are registered above and matched first.
    dist = web_dist_dir if web_dist_dir is not None else _default_web_dist()
    if dist.is_dir():
        index = dist / "index.html"
        dist_resolved = dist.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            # Never let the SPA fallback shadow the API or WebSocket surface.
            if full_path.startswith(("api/", "ws")) or full_path == "health":
                raise HTTPException(status_code=404)
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist_resolved)
            except ValueError:
                # Path escaped the dist dir; serve index.html rather than leak.
                return FileResponse(index)
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    return app
