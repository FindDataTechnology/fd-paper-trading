"""Instrument search and historical bars endpoints under /api/v1/instruments."""

from fastapi import APIRouter, Depends, Query, Request

from ...auth.dependencies import get_current_user
from ...marketdata import (
    DEFAULT_BAR_LIMIT,
    MAX_BAR_LIMIT,
    SUPPORTED_TIMEFRAMES,
)
from ...persistence.models import User
from ..errors import APIError
from ..schemas import BarOut, InstrumentSearchResultOut

router = APIRouter(tags=["instruments"])


def _provider(request: Request):
    return request.app.state.market_data_provider


@router.get("/instruments/search", response_model=list[InstrumentSearchResultOut])
async def search_instruments(
    request: Request,
    q: str = Query(default=""),
    user: User = Depends(get_current_user),
) -> list[InstrumentSearchResultOut]:
    """Search the instrument universe by symbol or name (case-insensitive)."""
    results = await _provider(request).search_instruments(q)
    return [
        InstrumentSearchResultOut(symbol=r.symbol, name=r.name, venue=r.venue)
        for r in results
    ]


@router.get("/instruments/{symbol}/bars", response_model=list[BarOut])
async def get_bars(
    request: Request,
    symbol: str,
    timeframe: str = Query(default="1m"),
    limit: int = Query(default=DEFAULT_BAR_LIMIT, ge=1, le=MAX_BAR_LIMIT),
    user: User = Depends(get_current_user),
) -> list[BarOut]:
    """Return up to ``limit`` OHLCV bars for ``symbol`` (oldest-first)."""
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise APIError(
            "invalid_timeframe",
            f"Unsupported timeframe '{timeframe}'. Supported: {sorted(SUPPORTED_TIMEFRAMES)}",
            400,
        )
    bars = await _provider(request).get_bars(symbol, timeframe, limit)
    return [
        BarOut(
            ts=b.ts,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
        for b in bars
    ]
