"""T+1 settlement, fees, and lot lifecycle end to end (change B).

The unit tests pin the pure rules; this file drives the HTTP paths a client uses:
a same-day sell is refused, a sell after the lot settles works, fees are charged
and recorded, and reset clears lots.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from fd_paper_trading.persistence.models import PositionLot

pytestmark = pytest.mark.asyncio


async def _account(client, headers, balance="100000"):
    r = await client.post(
        "/api/v1/accounts", json={"name": "t1", "starting_balance": balance},
        headers=headers,
    )
    return r.json()["id"]


async def _settle_lots(app, account_id):
    """Backdate every open lot to yesterday (as if bought on a prior day)."""
    uid = account_id if isinstance(account_id, uuid.UUID) else uuid.UUID(str(account_id))
    async with app.state.db.session_factory() as session:
        lots = (await session.execute(
            select(PositionLot).where(PositionLot.account_id == uid)
        )).scalars().all()
        for lot in lots:
            lot.opened_on = date.today() - timedelta(days=1)
        await session.commit()


async def _lots(app, account_id):
    uid = account_id if isinstance(account_id, uuid.UUID) else uuid.UUID(str(account_id))
    async with app.state.db.session_factory() as session:
        rows = list((await session.execute(
            select(PositionLot).where(PositionLot.account_id == uid)
            .order_by(PositionLot.opened_on, PositionLot.created_at)
        )).scalars().all())
        # Detach as plain tuples so callers can read after the session closes.
        return [(r.opened_on, r.unit_cost, r.remaining) for r in rows]


async def test_buy_creates_a_lot_and_charges_commission(client, auth_headers, place_order):
    h = await auth_headers("t1a@example.com")
    aid = await _account(client, h)
    r = await place_order(h, aid, symbol="AAPL", side="BUY", quantity="10")
    assert r.status_code == 202
    fill = r.json()["fills"][0]
    assert Decimal(str(fill["commission"])) > 0
    assert Decimal(str(fill["stamp_tax"])) == 0  # buy has no stamp tax
    assert Decimal(str(fill["slippage_cost"])) == 0  # paper path: no modelled slippage


async def test_same_day_sell_is_refused(client, auth_headers, place_order):
    h = await auth_headers("t1b@example.com")
    aid = await _account(client, h)
    await place_order(h, aid, symbol="AAPL", side="BUY", quantity="10")
    sell = await place_order(h, aid, symbol="AAPL", side="SELL", quantity="4")
    assert sell.status_code == 400
    assert sell.json()["error"]["code"] == "insufficient_position"


async def test_sell_after_settlement_succeeds(app, client, auth_headers, place_order):
    h = await auth_headers("t1c@example.com")
    aid = await _account(client, h)
    await place_order(h, aid, symbol="AAPL", side="BUY", quantity="10")
    await _settle_lots(app, aid)
    sell = await place_order(h, aid, symbol="AAPL", side="SELL", quantity="4")
    assert sell.status_code == 202, sell.text
    assert sell.json()["status"] == "FILLED"
    # The sell charged commission AND stamp tax.
    fill = sell.json()["fills"][0]
    assert Decimal(str(fill["commission"])) > 0
    assert Decimal(str(fill["stamp_tax"])) > 0


async def test_sell_beyond_available_names_both_numbers(app, client, auth_headers, place_order):
    h = await auth_headers("t1d@example.com")
    aid = await _account(client, h)
    await place_order(h, aid, symbol="AAPL", side="BUY", quantity="10")  # today, unsettled
    sell = await place_order(h, aid, symbol="AAPL", side="SELL", quantity="4")
    msg = sell.json()["error"]["message"]
    assert "available" in msg and "held" in msg


async def test_fifo_realized_pnl_uses_lot_cost(app, client, auth_headers, place_order):
    """Two buys at different prices, sell after both settle → FIFO cost basis."""
    h = await auth_headers("t1e@example.com")
    aid = await _account(client, h)
    await place_order(h, aid, symbol="AAPL", side="BUY", quantity="10")
    await place_order(h, aid, symbol="AAPL", side="BUY", quantity="10")
    await _settle_lots(app, aid)
    sell = await place_order(h, aid, symbol="AAPL", side="SELL", quantity="15")
    assert sell.status_code == 202, sell.text
    realized = Decimal(str(sell.json()["fills"][0]["realized_pnl"]))
    sell_px = Decimal(str(sell.json()["avg_fill_price"]))
    # FIFO uses each lot's unit_cost, which includes the buy commission — so read
    # the lots rather than the (pre-commission) avg_fill_price.
    lots = await _lots(app, aid)
    u1, u2 = lots[0][1], lots[1][1]
    gross = (sell_px - u1) * 10 + (sell_px - u2) * 5
    notional = sell_px * 15
    expected = gross - notional * Decimal("0.00025") - notional * Decimal("0.0005")
    assert abs(realized - expected) < Decimal("0.01")


async def test_reset_clears_lots(app, client, auth_headers, place_order):
    h = await auth_headers("t1f@example.com")
    aid = await _account(client, h)
    await place_order(h, aid, symbol="AAPL", side="BUY", quantity="10")
    assert len(await _lots(app, aid)) == 1
    r = await client.post(f"/api/v1/accounts/{aid}/reset", json={}, headers=h)
    assert r.status_code in (200, 201), r.text
    assert len(await _lots(app, aid)) == 0
    # A fresh buy after reset is again same-day unsellable.
    await place_order(h, aid, symbol="AAPL", side="BUY", quantity="5")
    sell = await place_order(h, aid, symbol="AAPL", side="SELL", quantity="5")
    assert sell.status_code == 400
