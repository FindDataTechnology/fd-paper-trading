"""Quote staleness: a fill must not use a price the feed stopped updating.

The guard has one deliberate hole — a quote whose source does not stamp
``ts_event`` is treated as "unknown, allow" — because only some providers
stamp it. These tests pin both sides of that line so the hole cannot quietly
widen into "nothing is ever stale".

Rediscovered reason this matters: ``astock``/``fd_open_data`` derive a quote
from the last daily close with a 30-calendar-day lookback, so during an outage
or after a long holiday the price a fill would use can be weeks old. Both
tests below fail on the pre-guard code (it only rejected ``quote is None``).
"""

from decimal import Decimal

import time

import pytest

from fd_paper_trading.execution.service import PaperExecutionService
from fd_paper_trading.marketdata.provider import Quote
from fd_paper_trading.marketdata.simulated import SimulatedMarketDataProvider


class _StampedProvider(SimulatedMarketDataProvider):
    """Simulated feed, but with a caller-chosen ``ts_event`` on every quote."""

    def __init__(self, age_seconds: float) -> None:
        self._ts = int(time.time() - age_seconds)

    async def get_quote(self, symbol: str) -> Quote | None:
        quote = await super().get_quote(symbol)
        if quote is None:
            return None
        return Quote(
            instrument_id=quote.instrument_id,
            bid=quote.bid,
            ask=quote.ask,
            ts_event=self._ts,
        )


async def test_stale_quote_is_rejected(
    client, auth_headers, create_account, place_order, app
):
    """A 30-day-old quote must be refused, not filled as if it were live."""
    aapl, = [s for s in ["AAPL"]]
    stale_ts = int(time.time() - 30 * 86400)
    app.state.market_data_provider = _StampedProvider(age_seconds=30 * 86400)
    app.state.execution_service.provider = app.state.market_data_provider

    h = await auth_headers("alice@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    r = await place_order(h, aid, symbol=aapl, side="BUY", quantity="10")

    assert r.status_code == 400, r.text
    body = r.json()
    detail = str(body)
    assert "stale_quote" in detail, f"expected a stale_quote rejection, got {body}"
    # The message must be diagnosable: which symbol, how old, what the max is.
    assert aapl in detail and "days old" in detail

    # Nothing was traded: no position, cash untouched.
    positions = (await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)).json()
    assert positions == []
    cash = Decimal(str((await client.get(f"/api/v1/accounts/{aid}", headers=h)).json()["cash_balance"]))
    assert cash == Decimal("100000")

    assert stale_ts < time.time()


async def test_unstamped_quote_is_not_treated_as_stale(
    client, auth_headers, create_account, place_order
):
    """``ts_event == 0`` means "source didn't say", not "infinitely old".

    Rejecting unknown would break every provider that does not stamp a
    timestamp — the simulated feed among them — so this pins the allowance.
    """
    h = await auth_headers("alice@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    r = await place_order(h, aid, symbol="AAPL", side="BUY", quantity="10")

    assert r.status_code == 202, f"unstamped quote must still trade: {r.text}"
    assert r.json()["status"] == "FILLED"


def test_quote_age_ignores_unknown_timestamp():
    """Unit-level: age is None for 0/negative, a number for a real stamp."""
    svc = PaperExecutionService(provider=SimulatedMarketDataProvider())
    unknown = Quote(instrument_id="AAPL.SIM", bid=Decimal("1"), ask=Decimal("2"), ts_event=0)
    assert svc._quote_age_seconds(unknown) is None
    assert svc._is_stale_quote(unknown) is False

    now = 1_700_000_000.0
    fresh = Quote(
        instrument_id="AAPL.SIM", bid=Decimal("1"), ask=Decimal("2"), ts_event=int(now - 60)
    )
    assert svc._quote_age_seconds(fresh, now=now) == pytest.approx(60.0)

    # _is_stale_quote reads the real clock, so stamp against it too.
    live = Quote(
        instrument_id="AAPL.SIM", bid=Decimal("1"), ask=Decimal("2"),
        ts_event=int(time.time() - 60),
    )
    assert svc._is_stale_quote(live) is False  # 60s is inside any sane bound

    old = Quote(
        instrument_id="AAPL.SIM", bid=Decimal("1"), ask=Decimal("2"),
        ts_event=int(now - 30 * 86400),
    )
    assert svc._is_stale_quote(old) is True
