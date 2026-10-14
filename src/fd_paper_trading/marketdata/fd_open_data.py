"""fd-open-data market-data provider.

Serves real daily bars and close-derived quotes from the fd-open-data-mcp
ontology (akshare/yfinance/etc. under the hood, with a read-through cache).

Scope: daily bars only. Quotes derive from the latest close with a ±5bps
spread ($0.01 floor), mirroring the Polygon free-tier provider — so the whole
app trades in one price world: the last completed trading day.

The fd-open-data library is synchronous (per-date dispatch + cache); all
library calls are wrapped in ``asyncio.to_thread`` so the event loop is never
blocked by a network fetch.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from fd_open_data_mcp.db import get_database
from fd_open_data_mcp.fetch.dispatch import read_range
from fd_open_data_mcp.models import Concept, Entity

from .provider import Bar, InstrumentSearchResult, MarketDataProvider, Quote
from .simulated import SIM_VENUE, SimulatedMarketDataProvider

logger = logging.getLogger(__name__)

_PRICE_CLOSE_CODE = "price.close"
_ENTITY_TYPE = "stock"
_PRICE_FREQUENCY = "daily"

# How far back ``get_latest_close`` looks for a recent bar, and the maximum
# history window served (matches the default group's max_backtest_days).
_QUOTE_LOOKBACK_DAYS = 30
_MAX_WINDOW_DAYS = 730

# Quote derivation: half-spread as a fraction of price (5 bps each side),
# with a $0.01 floor so bid != ask even for very low-priced stocks.
_HALF_SPREAD_BPS = Decimal("0.0005")
_MIN_HALF_SPREAD = Decimal("0.01")
_PRICE_Q = Decimal("0.01")  # equity price precision (cents)


def _normalize_date(value: object) -> str:
    """Coerce date/datetime/'YYYY-MM-DD'/'YYYYMMDD' → 'YYYY-MM-DD'."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _date_to_ts(d: str) -> int:
    """Convert 'YYYY-MM-DD' to a unix timestamp (midnight UTC)."""
    dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class FdOpenDataMarketDataProvider(MarketDataProvider):
    """Daily bars + close-derived quotes from the fd-open-data ontology."""

    def __init__(self) -> None:
        # Instruments and the search fallback share the SIM equity universe,
        # same as the Polygon provider.
        self._sim = SimulatedMarketDataProvider()

    # ---- public interface ----

    async def get_daily_bars(
        self, symbol: str, start: date | str, end: date | str
    ) -> list[Bar]:
        """Daily bars for ``symbol`` over [start, end], oldest-first.

        Returns an empty list when the symbol is unknown to the ontology or no
        data covers the window (a data gap).
        """
        start_s, end_s = _normalize_date(start), _normalize_date(end)
        if start_s > end_s:
            start_s, end_s = end_s, start_s
        closes = await asyncio.to_thread(self._fetch_closes_sync, symbol, start_s, end_s)
        bars: list[Bar] = []
        for d, close in closes:
            bars.append(
                Bar(
                    ts=_date_to_ts(d),
                    # The ontology's daily price concept is close-only; OHL
                    # collapse to the close so limit-crossing logic stays sane.
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("0"),
                )
            )
        return bars

    async def get_latest_close(self, symbol: str) -> Decimal | None:
        """Most recent close within the lookback window; None on a data gap."""
        end = date.today()
        start = end - timedelta(days=_QUOTE_LOOKBACK_DAYS)
        closes = await asyncio.to_thread(
            self._fetch_closes_sync, symbol, start.isoformat(), end.isoformat()
        )
        if not closes:
            return None
        return closes[-1][1]

    async def get_quote(self, symbol: str) -> Quote | None:
        """Quote derived from the latest close ±5 bps; None on a data gap."""
        try:
            close = await self.get_latest_close(symbol)
        except Exception:
            logger.exception("get_quote: error fetching close for %s", symbol)
            return None
        if close is None or close <= 0:
            return None
        half = (close * _HALF_SPREAD_BPS).quantize(_PRICE_Q)
        if half < _MIN_HALF_SPREAD:
            half = _MIN_HALF_SPREAD
        return Quote(
            instrument_id=f"{symbol}.{SIM_VENUE}",
            bid=(close - half).quantize(_PRICE_Q),
            ask=(close + half).quantize(_PRICE_Q),
        )

    def get_instrument(self, symbol: str):
        # Same SIM equity universe as the simulated/Polygon providers.
        return self._sim.get_instrument(symbol)

    async def search_instruments(self, q: str) -> list[InstrumentSearchResult]:
        """Search the ontology's stock entities; fall back to the SIM universe."""
        needle = (q or "").strip()
        if not needle:
            return []
        hits = await asyncio.to_thread(self._search_entities_sync, needle)
        if hits:
            return hits
        return await self._sim.search_instruments(q)

    async def get_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        """Daily bars only; any other timeframe is rejected."""
        if timeframe != "1d":
            raise ValueError(f"fd-open-data provider supports daily bars only, got {timeframe!r}")
        limit = min(max(1, limit), _MAX_WINDOW_DAYS)
        end = date.today()
        start = end - timedelta(days=limit)
        bars = await self.get_daily_bars(symbol, start, end)
        return bars[-limit:]

    # ---- sync internals (run via asyncio.to_thread) ----

    def _fetch_closes_sync(
        self, symbol: str, start: str, end: str
    ) -> list[tuple[str, Decimal]]:
        """(date, close) series for a symbol via read_range; [] on any gap."""
        session = get_database().get_session()
        try:
            concept = self._price_close_concept(session)
            if concept is None:
                logger.warning("fd-open-data: %s concept not seeded", _PRICE_CLOSE_CODE)
                return []
            entity_id = self._resolve_entity_id(session, symbol)
            if entity_id is None:
                return []
            result = read_range(
                session, [concept.id], _ENTITY_TYPE, entity_id, start, end
            )
            rows = result.get(concept.id) or []
            out: list[tuple[str, Decimal]] = []
            for row in rows:
                value = row.get("value")
                if value is None:
                    continue
                out.append((row["date"], Decimal(str(value))))
            return out
        finally:
            session.close()

    def _search_entities_sync(self, q: str) -> list[InstrumentSearchResult]:
        session = get_database().get_session()
        try:
            like = f"%{q}%"
            res = session.execute(
                select(Entity)
                .where(
                    Entity.entity_type == _ENTITY_TYPE,
                    Entity.code.ilike(like) | Entity.name_en.ilike(like),
                )
                .limit(20)
            )
            return [
                InstrumentSearchResult(
                    symbol=e.code, name=e.name_en or e.code, venue=SIM_VENUE
                )
                for e in res.scalars().all()
            ]
        finally:
            session.close()

    @staticmethod
    def _price_close_concept(session) -> Concept | None:
        """The daily price.close stock concept (prefer verified, non-deprecated)."""
        res = session.execute(
            select(Concept)
            .where(
                Concept.code == _PRICE_CLOSE_CODE,
                Concept.entity_type == _ENTITY_TYPE,
                Concept.frequency == _PRICE_FREQUENCY,
                Concept.deprecated.is_(False),
            )
            .order_by(Concept.verified.desc(), Concept.id.asc())
        )
        return res.scalars().first()

    @staticmethod
    def _resolve_entity_id(session, symbol: str) -> int | None:
        """Map a ticker to the ontology's internal entity id (int PK)."""
        res = session.execute(
            select(Entity).where(
                Entity.entity_type == _ENTITY_TYPE, Entity.code == symbol.upper()
            )
        )
        entity = res.scalars().first()
        return entity.id if entity is not None else None
