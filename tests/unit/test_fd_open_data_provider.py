"""Unit tests for the fd-open-data market-data provider.

The fd-open-data library is synchronous and talks to its own ontology DB, so
tests patch the two library seams (``get_database`` / ``read_range``) or the
provider's sync internals; no real ontology is needed.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from fd_paper_trading.marketdata import FdOpenDataMarketDataProvider
from fd_paper_trading.marketdata import fd_open_data as mod


@pytest.fixture
def provider():
    return FdOpenDataMarketDataProvider()


def _patch_closes(closes):
    """Patch the sync fetch so ``get_daily_bars``/``get_latest_close`` see ``closes``."""
    return patch.object(
        FdOpenDataMarketDataProvider, "_fetch_closes_sync", return_value=closes
    )


# ---- adapter: get_daily_bars ----


async def test_daily_bars_map_closes_to_ohlc(provider):
    closes = [("2026-08-05", Decimal("100.50")), ("2026-08-06", Decimal("101.25"))]
    with _patch_closes(closes) as mock_fetch:
        bars = await provider.get_daily_bars("AAPL", "2026-08-05", "2026-08-06")
    mock_fetch.assert_called_once_with("AAPL", "2026-08-05", "2026-08-06")
    assert len(bars) == 2
    # The ontology daily concept is close-only: OHL collapse to the close.
    assert bars[0].open == bars[0].high == bars[0].low == bars[0].close == Decimal("100.50")
    assert bars[0].volume == Decimal("0")
    # ts is midnight UTC of the bar date.
    assert bars[0].ts == 1785888000  # 2026-08-05T00:00:00Z
    assert bars[1].ts - bars[0].ts == 86400


async def test_daily_bars_normalizes_date_formats_and_swaps(provider):
    with _patch_closes([]) as mock_fetch:
        await provider.get_daily_bars("AAPL", date(2026, 8, 6), "20260805")
    # start > end is swapped; both coerced to YYYY-MM-DD.
    mock_fetch.assert_called_once_with("AAPL", "2026-08-05", "2026-08-06")


async def test_daily_bars_empty_on_gap(provider):
    with _patch_closes([]):
        assert await provider.get_daily_bars("UNKNOWN", "2026-08-01", "2026-08-02") == []


# ---- get_latest_close / get_quote ----


async def test_latest_close_returns_last(provider):
    with _patch_closes([("2026-08-05", Decimal("100")), ("2026-08-06", Decimal("101"))]):
        assert await provider.get_latest_close("AAPL") == Decimal("101")


async def test_latest_close_none_on_gap(provider):
    with _patch_closes([]):
        assert await provider.get_latest_close("AAPL") is None


async def test_quote_derives_bid_ask_from_close(provider):
    with _patch_closes([("2026-08-06", Decimal("100.00"))]):
        quote = await provider.get_quote("AAPL")
    assert quote is not None
    # 5 bps half-spread on 100.00 = 0.05
    assert quote.bid == Decimal("99.95")
    assert quote.ask == Decimal("100.05")
    assert quote.instrument_id == "AAPL.SIM"


async def test_quote_half_spread_has_cent_floor(provider):
    with _patch_closes([("2026-08-06", Decimal("5.00"))]):
        quote = await provider.get_quote("AAPL")
    # 5 bps of 5.00 = 0.0025 -> quantized to 0.00 -> floored at 0.01
    assert quote.bid == Decimal("4.99")
    assert quote.ask == Decimal("5.01")


async def test_quote_none_on_gap(provider):
    with _patch_closes([]):
        assert await provider.get_quote("AAPL") is None


async def test_quote_none_on_nonpositive_close(provider):
    with _patch_closes([("2026-08-06", Decimal("0"))]):
        assert await provider.get_quote("AAPL") is None


async def test_quote_none_on_fetch_error(provider):
    with patch.object(
        FdOpenDataMarketDataProvider, "_fetch_closes_sync", side_effect=RuntimeError("db down")
    ):
        assert await provider.get_quote("AAPL") is None


# ---- get_bars (MarketDataProvider interface) ----


async def test_get_bars_rejects_non_daily_timeframe(provider):
    with pytest.raises(ValueError, match="daily bars only"):
        await provider.get_bars("AAPL", "1m", 20)


async def test_get_bars_returns_last_limit_bars(provider):
    closes = [(f"2026-07-{d:02d}", Decimal("100")) for d in range(1, 29)]
    with _patch_closes(closes):
        bars = await provider.get_bars("AAPL", "1d", 5)
    assert len(bars) == 5
    assert bars[-1].close == Decimal("100")


async def test_get_bars_clamps_limit(provider):
    with _patch_closes([]):
        # limit above the 730-day window is clamped, not an error
        assert await provider.get_bars("AAPL", "1d", 5000) == []


# ---- search_instruments ----


async def test_search_hits_ontology_entities(provider):
    hit = MagicMock()
    with patch.object(
        FdOpenDataMarketDataProvider,
        "_search_entities_sync",
        return_value=[hit],
    ):
        assert await provider.search_instruments("600519") == [hit]


async def test_search_falls_back_to_sim_universe(provider):
    with patch.object(
        FdOpenDataMarketDataProvider, "_search_entities_sync", return_value=[]
    ):
        results = await provider.search_instruments("AAP")
    assert any(r.symbol == "AAPL" for r in results)


async def test_search_empty_query(provider):
    assert await provider.search_instruments("") == []


# ---- sync internals: _fetch_closes_sync ----


def _session_returning(concept=None, entity=None):
    """A sync-session mock whose execute() yields concept then entity lookups."""
    concept_res = MagicMock()
    concept_res.scalars.return_value.first.return_value = concept
    entity_res = MagicMock()
    entity_res.scalars.return_value.first.return_value = entity
    session = MagicMock()
    session.execute.side_effect = [concept_res, entity_res]
    return session


def test_fetch_closes_maps_rows_and_skips_nulls(provider):
    concept = MagicMock(id=7)
    entity = MagicMock(id=42)
    session = _session_returning(concept, entity)
    rows = [
        {"date": "2026-08-05", "value": 100.5},
        {"date": "2026-08-06", "value": None},
        {"date": "2026-08-07", "value": "101.25"},
    ]
    with (
        patch.object(mod, "get_database") as mock_db,
        patch.object(mod, "read_range", return_value={7: rows}) as mock_read,
    ):
        mock_db.return_value.get_session.return_value = session
        closes = provider._fetch_closes_sync("aapl", "2026-08-05", "2026-08-07")
    mock_read.assert_called_once_with(session, [7], "fund", 42, "2026-08-05", "2026-08-07")
    assert closes == [("2026-08-05", Decimal("100.5")), ("2026-08-07", Decimal("101.25"))]
    session.close.assert_called_once()


def test_fetch_closes_empty_when_concept_missing(provider):
    session = _session_returning(concept=None)
    with patch.object(mod, "get_database") as mock_db:
        mock_db.return_value.get_session.return_value = session
        assert provider._fetch_closes_sync("AAPL", "2026-08-05", "2026-08-07") == []
    session.close.assert_called_once()


def test_fetch_closes_empty_when_entity_missing(provider):
    session = _session_returning(concept=MagicMock(id=7), entity=None)
    with patch.object(mod, "get_database") as mock_db:
        mock_db.return_value.get_session.return_value = session
        assert provider._fetch_closes_sync("ZZZZ", "2026-08-05", "2026-08-07") == []


# ---- instruments delegate to the SIM universe ----


def test_get_instrument_uses_sim_universe(provider):
    assert provider.get_instrument("AAPL") is not None


# ---- concept family is configuration (paper-trading-standup) ----


def test_family_from_explicit_arguments():
    p = FdOpenDataMarketDataProvider(
        concept_code="CRYPTO_PRICE", entity_type="symbol", frequency="daily"
    )
    assert p._concept_code == "CRYPTO_PRICE"
    assert p._entity_type == "symbol"
    assert p._frequency == "daily"


def test_family_defaults_are_fund_nav(monkeypatch):
    # No explicit args -> settings defaults (nav.unit/fund/daily), the family
    # with real production data. Clear the settings cache so env is irrelevant.
    monkeypatch.delenv("FD_OPEN_DATA_CONCEPT", raising=False)
    from fd_paper_trading.config import get_settings

    get_settings.cache_clear()
    try:
        p = FdOpenDataMarketDataProvider()
        assert p._concept_code == "nav.unit"
        assert p._entity_type == "fund"
        assert p._frequency == "daily"
    finally:
        get_settings.cache_clear()


def test_family_resolved_from_env(monkeypatch):
    monkeypatch.setenv("FD_OPEN_DATA_CONCEPT", "CRYPTO_PRICE")
    from fd_paper_trading.config import get_settings

    get_settings.cache_clear()
    try:
        p = FdOpenDataMarketDataProvider()
        assert p._concept_code == "CRYPTO_PRICE"
    finally:
        get_settings.cache_clear()


def test_deprecated_concept_is_a_gap_not_a_crash(provider):
    # _resolve_concept filters deprecated.is_(False), so a deprecated family
    # resolves to None -> fetch yields [] (data gap), no exception.
    session = _session_returning(concept=None)
    with patch.object(mod, "get_database") as mock_db:
        mock_db.return_value.get_session.return_value = session
        assert provider._fetch_closes_sync("AAPL", "2026-08-05", "2026-08-07") == []


def test_single_concept_selected_when_multiple_match():
    # Two matching concepts (e.g. same code across sources): deterministic
    # selection — verified first, then lowest id — and only that one is read.
    c_verified = MagicMock(id=11)
    c_first = MagicMock(id=5)
    entity = MagicMock(id=42)
    session = MagicMock()
    first_res, second_res = MagicMock(), MagicMock()
    # .first() on the ordered query returns the verified one (id 11)...
    first_res.scalars.return_value.first.return_value = c_verified
    second_res.scalars.return_value.first.return_value = entity
    session.execute.side_effect = [first_res, second_res]
    rows = [{"date": "2026-08-05", "value": 7.0}]
    with (
        patch.object(mod, "get_database") as mock_db,
        patch.object(mod, "read_range", return_value={11: rows}) as mock_read,
    ):
        mock_db.return_value.get_session.return_value = session
        p = FdOpenDataMarketDataProvider()
        closes = p._fetch_closes_sync("AAPL", "2026-08-05", "2026-08-05")
    assert closes == [("2026-08-05", Decimal("7.0"))]
    # exactly one concept id is requested from read_range
    requested = mock_read.call_args[0][1]
    assert requested == [11]


# ---- coverage gate: non-fillable concepts are judged, not filled ----


def test_coverage_complete_for_nonfillable_concept(provider):
    """Regression: a non-fillable concept (nav.*) with a dense series must read
    as COMPLETE, not whole-window missing.

    fill_gaps refuses to fetch non-price concepts (revised values must not be
    pinned), and its early return there is a refusal notice — feeding it to the
    coverage gate reported every nav.window as missing and rejected every NAV
    backtest. The gate must judge coverage from the series' own dates instead.
    """
    concept = MagicMock(id=7, code="nav.unit")
    entity = MagicMock(id=42)
    session = _session_returning(concept, entity)
    rows = [
        MagicMock(date="2026-08-28", value=Decimal("4.6686")),
        MagicMock(date="2026-08-29", value=Decimal("4.7")),
    ]
    with (
        patch.object(mod, "get_database") as mock_db,
        patch.object(mod, "read_cache_range", return_value=rows) as mock_rcr,
        patch(
            "fd_open_data_mcp.fetch.gapfill.series_dates",
            return_value=["2026-08-28", "2026-08-29"],
        ),
    ):
        mock_db.return_value.get_session.return_value = session
        out = provider._coverage_sync("007355", "2026-08-28", "2026-08-29")
    assert out["missing"] == []
    assert len(out["closes"]) == 2
    assert out["closes"][0] == ("2026-08-28", Decimal("4.6686"))
    assert out["fill_failed"] is False
    assert out["fingerprint"]
    mock_rcr.assert_called_once()
    session.close.assert_called_once()


def test_coverage_reports_gap_for_nonfillable_concept(provider):
    """A genuinely missing stretch in a non-fillable series still reports missing."""
    concept = MagicMock(id=7, code="nav.unit")
    entity = MagicMock(id=42)
    session = _session_returning(concept, entity)
    dates = ["2026-08-28", "2026-08-29", "2026-09-15", "2026-09-16"]
    rows = [MagicMock(date=d, value=Decimal("1")) for d in dates]
    with (
        patch.object(mod, "get_database") as mock_db,
        patch.object(mod, "read_cache_range", return_value=rows),
        patch("fd_open_data_mcp.fetch.gapfill.series_dates", return_value=dates),
    ):
        mock_db.return_value.get_session.return_value = session
        out = provider._coverage_sync("007355", "2026-08-28", "2026-09-16")
    assert out["missing"] == [{"start": "2026-08-30", "end": "2026-09-14"}]


def test_coverage_fillable_concept_still_goes_through_fill_gaps(provider):
    """Price concepts keep the fill-then-report path (no behavior change)."""
    concept = MagicMock(id=7, code="price.close")
    entity = MagicMock(id=42)
    session = _session_returning(concept, entity)
    with (
        patch.object(mod, "get_database") as mock_db,
        patch(
            "fd_open_data_mcp.fetch.gapfill.fill_gaps",
            return_value={
                "points": [{"date": "2026-08-28", "value": "10.0"}],
                "missing": [],
                "market_closed": [],
                "fill_attempted": True,
                "fill_failed": False,
                "fingerprint": "abc",
            },
        ) as mock_fill,
    ):
        mock_db.return_value.get_session.return_value = session
        out = provider._coverage_sync("AAPL", "2026-08-28", "2026-08-28")
    mock_fill.assert_called_once()
    assert out["closes"] == [("2026-08-28", Decimal("10.0"))]
    assert out["fingerprint"] == "abc"
