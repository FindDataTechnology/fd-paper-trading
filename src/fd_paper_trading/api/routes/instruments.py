"""Instrument search and historical bars endpoints under /api/v1/instruments."""

import json

from fastapi import APIRouter, Depends, Query, Request, Response

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


#: 行情响应头：契约对象（与 `contracts/models.py` 的 `PriceSeriesRef` 同形）。
#: 放在头里而不是响应体里——`/bars` 向来返回裸数组，改结构会打断图表与所有
#: 既有消费方；头是纯增量，"每个价格响应都带引用"这条因此不必以破坏兼容为代价。
SERIES_REF_HEADER = "X-Price-Series-Ref"


def _attach_series_ref(response: Response, provider, symbol: str, bars) -> None:
    """provider 能说出处就挂头，说不出就不挂（不给假引用）。

    「没有这个头」= provider 没有这个能力（simulated / polygon）；
    有头但内容不合法是不可能的——provider 侧已用契约校验器自校过。
    """
    build = getattr(provider, "series_ref_for_bars", None)
    if build is None or not bars:
        return
    try:
        ref = build(symbol, bars)
    except Exception:  # noqa: BLE001 - 出处是尽力而为，绝不让行情接口失败
        return
    if not ref:
        return
    response.headers[SERIES_REF_HEADER] = json.dumps(
        ref, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )



@router.get("/instruments/capabilities")
async def capabilities(
    request: Request,
    user: User = Depends(get_current_user),
) -> dict:
    """What the active market-data provider can actually serve.

    The chart asks this instead of offering every service-wide timeframe: a
    daily-only provider would answer a minute-bar request with a 400, and a
    control that can only produce errors is worse than no control.
    """
    served = tuple(
        getattr(_provider(request), "supported_timeframes", ()) or ()
    )
    return {"timeframes": sorted(served) if served else sorted(SUPPORTED_TIMEFRAMES)}


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
    response: Response,
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
    provider = _provider(request)
    # A provider may serve a narrower set than the service-wide one (fd-open-data
    # is daily-only). Checking the active provider's set keeps an unserved
    # timeframe a 400 about the request, instead of a provider-side ValueError
    # surfacing as a 500 from inside the chart.
    served = tuple(getattr(provider, "supported_timeframes", ()) or ())
    if served and timeframe not in served:
        raise APIError(
            "timeframe_not_served",
            f"Provider does not serve timeframe '{timeframe}'. "
            f"Available here: {sorted(served)}",
            400,
        )
    try:
        bars = await provider.get_bars(symbol, timeframe, limit)
    except ValueError as exc:
        # Any provider that still raises on an unserved timeframe must not turn
        # a client mistake into a 500.
        raise APIError("invalid_timeframe", str(exc), 400) from exc
    _attach_series_ref(response, provider, symbol, bars)
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
