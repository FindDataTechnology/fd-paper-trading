"""Order placement/cancellation with simulated fills."""

from decimal import Decimal


def _dec(v) -> Decimal:
    return Decimal(str(v))


async def test_place_market_buy_fills_at_ask(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")
    assert r.status_code == 202
    order = r.json()
    assert order["status"] == "FILLED"
    assert _dec(order["filled_quantity"]) == Decimal("10")
    assert _dec(order["avg_fill_price"]) > 0
    assert len(order["fills"]) == 1
    avg = _dec(order["avg_fill_price"])

    # Position opened long 10 at the fill price.
    rp = await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)
    assert rp.status_code == 200
    positions = rp.json()
    assert len(positions) == 1
    assert _dec(positions[0]["quantity"]) == Decimal("10")
    assert _dec(positions[0]["avg_price"]) == avg

    # Cash reduced by 10 * avg.
    rs = await client.get(f"/api/v1/accounts/{aid}", headers=h)
    cash = _dec(rs.json()["cash_balance"])
    assert abs(cash - (Decimal("100000") - Decimal("10") * avg)) < Decimal("0.01")


async def test_market_sell_after_buy_reduces_position(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    buy = (await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")).json()
    sell = await place_order(h, aid, symbol="AAPL", side="SELL", order_type="MARKET", quantity="4")
    assert sell.status_code == 202
    assert sell.json()["status"] == "FILLED"

    rp = await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)
    assert _dec(rp.json()[0]["quantity"]) == Decimal("6")  # 10 - 4


async def test_limit_order_rests_when_not_crossing(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    # A tiny limit price (1.00) will not cross the ask -> order rests OPEN.
    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="LIMIT", quantity="10", limit_price="1.00")
    assert r.status_code == 202
    order = r.json()
    assert order["status"] == "OPEN"
    assert _dec(order["filled_quantity"]) == Decimal("0")

    # No position, one open order.
    rp = await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)
    assert rp.json() == []
    ro = await client.get(f"/api/v1/accounts/{aid}/orders?status=OPEN", headers=h)
    assert len(ro.json()) == 1


async def test_limit_order_fills_when_crossing(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    # A very high limit (99999) crosses the ask -> fills immediately.
    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="LIMIT", quantity="10", limit_price="99999")
    assert r.status_code == 202
    assert r.json()["status"] == "FILLED"


async def test_insufficient_buying_power_rejected(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("1000"))).json()
    aid = acct["id"]

    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="100000")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "insufficient_buying_power"


async def test_cancel_open_limit_order(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    placed = (await place_order(h, aid, symbol="AAPL", side="BUY", order_type="LIMIT", quantity="10", limit_price="1.00")).json()
    r = await client.delete(f"/api/v1/orders/{placed['id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"


async def test_cancel_filled_order_rejected(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    placed = (await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")).json()
    assert placed["status"] == "FILLED"
    r = await client.delete(f"/api/v1/orders/{placed['id']}", headers=h)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "not_cancellable"


async def test_cross_account_order_denied(client, auth_headers, create_account, place_order):
    ha = await auth_headers("alice@example.com")
    hb = await auth_headers("bob@example.com")
    acct_a = (await create_account(ha, starting_balance=Decimal("100000"))).json()

    r = await place_order(hb, acct_a["id"], symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_order_requires_auth(client, create_account, auth_headers):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    r = await client.post(
        "/api/v1/orders",
        json={"account_id": acct["id"], "symbol": "AAPL", "side": "BUY", "order_type": "MARKET", "quantity": "10"},
    )
    assert r.status_code == 401


async def test_limit_requires_limit_price(client, auth_headers, create_account):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    r = await client.post(
        "/api/v1/orders",
        json={"account_id": acct["id"], "symbol": "AAPL", "side": "BUY", "order_type": "LIMIT", "quantity": "10"},
        headers=h,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


# ---- 2026-11-13：两条拒单原因此前零测试覆盖 -------------------------------
#
# `execution/service.py` 的下单入口有两道闸：
#     instrument = provider.get_instrument(symbol)   → None ⇒ OrderRejected("unknown_instrument")
#     quote      = await provider.get_quote(symbol)  → None ⇒ OrderRejected("no_quote")
#
# 这两道闸是"未知标的不能下单"的**唯一**承重墙：A 股 provider 的 `get_instrument` 只做
# 句法解析（接口是同步的、库是异步的，见 astock.py:700 的 docstring），所以任何形如
# A 股代码的字符串都会拿到一个合成 Instrument，真正拦住它的是 no_quote。
#
# 换句话说：**删掉 no_quote 那道检查，未知标的就变成可下单了**，而此前没有任何测试
# 会红。这两条钉的就是这个。


async def test_missing_quote_refuses_the_order(client, auth_headers, create_account, place_order, app, monkeypatch):
    """quote 为 None ⇒ 400 `no_quote`（不是成交、也不是 500）。"""
    provider = app.state.market_data_provider

    async def _no_quote(symbol):
        return None

    monkeypatch.setattr(provider, "get_quote", _no_quote)

    h = await auth_headers("alice@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]
    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "no_quote"


async def test_unknown_instrument_refuses_the_order(client, auth_headers, create_account, place_order, app, monkeypatch):
    """instrument 为 None ⇒ 400 `unknown_instrument`（先于 quote 检查）。"""
    provider = app.state.market_data_provider
    monkeypatch.setattr(provider, "get_instrument", lambda symbol: None)

    h = await auth_headers("alice@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]
    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_instrument"


async def test_unknown_instrument_is_reachable_with_the_real_provider(
    client, auth_headers, create_account, place_order, app
):
    """不 monkeypatch 也要拒：真实模拟源对"不像标的"的字符串返回 None。

    上面那条把 `get_instrument` 直接压成 None——它证明的是"闸的反应对"，
    而不是"闸在默认配置下打得开"。2026-11-13 实测这两件事并不等价：
    `SimulatedMarketDataProvider` 原先对**任意字符串**都返回合约
    （连 `UNKNOWN-SYMBOL-XYZ` 都返回），而模拟源正是 `MARKET_DATA_SOURCE_ID`
    未设置时的默认选择。于是默认配置下这道闸**永远不触发**：打错一个字母照样
    下单成功，两条拒单原因里只有 no_quote 会响，而模拟源的 quote 对任意字符串
    也都有值。规格 market-data「Instrument resolution」要求"不认识的标的返回 None"，
    这条就是它在默认配置下的判据。
    """
    provider = app.state.market_data_provider
    assert provider.get_instrument("UNKNOWN-SYMBOL-XYZ") is None  # 前提：真 provider 认不出

    h = await auth_headers("alice@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]
    r = await place_order(
        h, aid, symbol="UNKNOWN-SYMBOL-XYZ", side="BUY", order_type="MARKET", quantity="10"
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_instrument"


# ---- 2026-11-13：`balance_after` 是规格明写的一列，全仓测试零命中 ------------
#
# order-execution「Fill ledgering」：每次成交都要记一条 `trade` 类型的
# BalanceTransaction，并且**带上成交后的余额**（"with the resulting
# `balance_after`"）。paper-accounts 也提到这一列。
#
# 但 `grep balance_after tests/` 零命中——只有实现里写了它。这正是最容易静默错位的
# 那种列：`account.cash_balance += cash_delta` 与 `balance_after=account.cash_balance`
# 的**先后顺序**决定它是成交后余额还是成交前余额；写反了，账户流水逐笔都对不上，
# 而没有一条测试会红（余额本身、持仓、成交价全都还是对的）。
#
# 判据：流水不只看"有一条 trade"，还要看它记的余额等于成交后的真实余额，
# 且 上一笔余额 + 本笔金额 == 本笔记录的余额（可对账的链）。


async def _balance_transactions(app, account_id):
    import uuid as _uuid

    from sqlalchemy import select

    from fd_paper_trading.persistence.models import BalanceTransaction

    if not isinstance(account_id, _uuid.UUID):
        account_id = _uuid.UUID(str(account_id))
    async with app.state.db.session_factory() as session:
        rows = (await session.execute(
            select(BalanceTransaction)
            .where(BalanceTransaction.account_id == account_id)
            .order_by(BalanceTransaction.created_at, BalanceTransaction.id)
        )).scalars().all()
        return [(t.type, t.amount, t.balance_after, t.reference) for t in rows]


async def test_fill_records_a_trade_ledgered_at_the_resulting_balance(
    client, auth_headers, create_account, place_order, app
):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    order = (await place_order(
        h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10"
    )).json()
    avg = _dec(order["avg_fill_price"])
    cash = _dec((await client.get(f"/api/v1/accounts/{aid}", headers=h)).json()["cash_balance"])

    txns = await _balance_transactions(app, aid)
    trades = [t for t in txns if t[0] == "trade"]
    assert len(trades) == 1, f"一次成交一条 trade 流水，实际 {len(trades)}：{txns}"

    kind, amount, balance_after, reference = trades[0]
    assert amount == -(Decimal("10") * avg)          # 买入扣现金
    assert balance_after == cash                     # ← 成交后的余额，不是成交前的
    assert reference == order["id"]                  # 能回指到订单
    # 可对账的链：上一笔余额 + 本笔金额 == 本笔记录的余额
    prev = [t for t in txns if t is not trades[0]]
    assert prev, "起始入金应当也在流水里"
    assert prev[-1][2] + amount == balance_after


async def test_deposit_ledgers_the_resulting_balance(
    client, auth_headers, create_account, app
):
    """同一条判据在入金路径上：`balance_after` 必须是入金**后**的数。"""
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("1000"))).json()
    aid = acct["id"]

    r = await client.post(
        f"/api/v1/accounts/{aid}/deposit", json={"amount": "500"}, headers=h
    )
    assert r.status_code in (200, 201), r.text

    txns = await _balance_transactions(app, aid)
    deposits = [t for t in txns if t[0] == "deposit"]
    assert [t[2] for t in deposits] == [Decimal("1000"), Decimal("1500")]
