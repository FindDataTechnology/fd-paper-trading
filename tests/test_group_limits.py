"""Group limits bind on the direct order path too.

The admin panel's knobs — ``allowed_symbols`` and ``max_orders_per_day`` — used
to be enforced in two places, neither of which a direct ``POST /api/v1/orders``
went through:

* ``allowed_symbols`` was checked only when a *strategy* was created/edited
  (``api/routes/strategies.py``), so a user could trade any ticker by hand.
* ``max_orders_per_day`` was counted per *run* (``count_for_run``), so a user
  with three active runs got three times the cap and a manual order got none.

An operator who set those limits believed they bound. These tests pin that they
now do — on the same path every order travels.
"""

from decimal import Decimal

from fd_paper_trading.persistence.models import DEFAULT_GROUP_ID, DEFAULT_GROUP_CONFIG, UserGroup
from fd_paper_trading.persistence.repositories import UserRepository


async def _set_group(app, **overrides) -> None:
    """Bind the test user to a group with overridden limits."""
    async with app.state.db.session_factory() as session:
        group = UserGroup(
            id=DEFAULT_GROUP_ID, name="default", config={**DEFAULT_GROUP_CONFIG, **overrides}
        )
        session.add(group)
        user = await UserRepository(session).get_by_email("limits@example.com")
        user.group_id = group.id
        await session.commit()


async def test_direct_order_outside_allowlist_is_rejected(
    app, client, auth_headers, create_account, place_order
):
    """A hand-placed order must respect the symbol allowlist, not just strategies."""
    h = await auth_headers("limits@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]
    await _set_group(app, allowed_symbols=["AAPL"])

    blocked = await place_order(h, aid, symbol="TSLA", side="BUY", quantity="1")
    assert blocked.status_code == 400, blocked.text
    assert "symbol_not_allowed" in blocked.text

    allowed = await place_order(h, aid, symbol="AAPL", side="BUY", quantity="1")
    assert allowed.status_code == 202, allowed.text


async def test_daily_cap_counts_by_account_not_by_run(
    app, client, auth_headers, create_account, place_order
):
    """The cap is per user per day: a third order hits it even with no strategy runs."""
    h = await auth_headers("limits@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]
    await _set_group(app, max_orders_per_day=2)

    first = await place_order(h, aid, symbol="AAPL", side="BUY", quantity="1")
    second = await place_order(h, aid, symbol="AAPL", side="BUY", quantity="1")
    third = await place_order(h, aid, symbol="AAPL", side="BUY", quantity="1")

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert third.status_code == 400, third.text
    assert "daily_order_cap" in third.text


async def test_unrestricted_group_can_trade_freely(
    app, client, auth_headers, create_account, place_order
):
    """Default group: no allowlist, cap far above the test's order count."""
    h = await auth_headers("limits@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]
    await _set_group(app, allowed_symbols=None, max_orders_per_day=20)

    for symbol in ("AAPL", "MSFT", "NVDA"):
        r = await place_order(h, aid, symbol=symbol, side="BUY", quantity="1")
        assert r.status_code == 202, f"{symbol}: {r.text}"
