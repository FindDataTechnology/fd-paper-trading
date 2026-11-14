"""账户日度盈亏：必须真的是「今天」，且基准必须是「昨收」。

改前这张卡（`web/src/components/DailyPnl.tsx`）算的是**开户以来**：

```ts
const realizedPnl = positions?.reduce((acc, p) => acc + Number(p.realized_pnl), 0) ?? 0;
acc + unrealizedPnl(p.quantity, p.avg_price, String(mark));   // 基准＝成本价
```

而 spec 三条都写"今天/日界/昨收"。根因是数据层缺口：`fills` 行没有逐笔已实现盈亏，
"今天的已实现"无从求和，前端只能拿持仓行上的终身累计凑数。

本文件判五件事，每条对应一处曾经算错的口径：今日已实现只算日界之后 / 未实现以昨收为
基准 / 拿不到昨收就报缺口而不拿成本价顶 / 日界可配且真生效 / 卖出成交自带实现盈亏。

时间靠"真实成交 + 回拨 fills.ts"来造，不合成孤儿成交行——`fills.order_id` 是 NOT NULL，
而这正好也让用例走的是生产同一条记账路径。
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from fd_paper_trading.marketdata.provider import Bar, Quote
from fd_paper_trading.marketdata.simulated import SimulatedMarketDataProvider
from fd_paper_trading.persistence.models import Fill, Position


class ScriptedProvider(SimulatedMarketDataProvider):
    """可脚本化的行情源：按测试给的日线与报价回答（缺省回落到模拟源）。"""

    def __init__(self) -> None:
        self.bars: dict[str, list[Bar]] = {}
        self.quotes: dict[str, Quote | None] = {}

    async def get_bars(self, symbol, timeframe="1d", limit=100):
        if symbol in self.bars:
            return list(self.bars[symbol])[-limit:]
        return await super().get_bars(symbol, timeframe, limit)

    async def get_quote(self, symbol):
        if symbol in self.quotes:
            return self.quotes[symbol]
        return await super().get_quote(symbol)


def _bar(d: date, close: str) -> Bar:
    ts = int(datetime.combine(d, datetime.min.time(), timezone.utc).timestamp())
    return Bar(ts=ts, open=Decimal(close), high=Decimal(close),
               low=Decimal(close), close=Decimal(close), volume=Decimal("1000"))


def _quote(price: str) -> Quote:
    return Quote(instrument_id="AAPL.SIM", bid=Decimal(price), ask=Decimal(price))


@pytest.fixture
def scripted(app):
    provider = ScriptedProvider()
    app.state.execution_service.provider = provider
    app.state.realtime.provider = provider
    app.state.market_data_provider = provider
    return provider


async def _setup(app, client, auth_headers, create_account, place_order, scripted, *, qty="10"):
    """开账户、买 qty 股、把成本价钉成 100（成交价来自模拟源，不可控）。"""
    headers = await auth_headers("pnl@example.com")
    acct = (await create_account(headers, starting_balance=1000000)).json()
    account_id = uuid.UUID(acct["id"])
    buy = await place_order(headers, acct["id"], symbol="AAPL", side="BUY", quantity=qty)
    assert buy.status_code == 202, buy.text
    async with app.state.db.session_factory() as session:
        pos = (await session.execute(
            select(Position).where(Position.account_id == account_id)
        )).scalar_one()
        pos.avg_price = Decimal("100")
        await session.commit()
    return headers, account_id


async def _fill_ids(app, account_id) -> set:
    async with app.state.db.session_factory() as session:
        return set((await session.execute(
            select(Fill.id).where(Fill.account_id == account_id)
        )).scalars().all())


async def _sell(client, app, headers, account_id, place_order, scripted, *, price, qty="5"):
    """下一笔卖单并返回**它自己**的成交行。

    不能按 `ts desc` 取"最新"：本文件的用例会把成交回拨到过去，而真实时钟可能比回拨点
    更早（UTC 00:49 跑测试、成交被拨到 08:00），"最新"就会指向上一笔。按 id 差集定位。
    """
    scripted.quotes["AAPL"] = _quote(price)
    before = await _fill_ids(app, account_id)
    r = await place_order(headers, str(account_id), symbol="AAPL", side="SELL", quantity=qty)
    assert r.status_code == 202, r.text
    new_ids = (await _fill_ids(app, account_id)) - before
    assert len(new_ids) == 1, f"期望恰好一笔新成交，实际 {new_ids}"
    fill_id = new_ids.pop()
    async with app.state.db.session_factory() as session:
        return await session.get(Fill, fill_id)


async def _backdate(app, fill_id, when: datetime) -> None:
    async with app.state.db.session_factory() as session:
        fill = await session.get(Fill, fill_id)
        fill.ts = when
        await session.commit()


async def _get(client, headers, account_id):
    resp = await client.get(f"/api/v1/accounts/{account_id}/daily-pnl", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_lifetime_realized_does_not_leak_into_today(
    app, client, auth_headers, create_account, place_order, scripted
):
    """昨天实现的 400 不进今天——旧实现在这里会显示终身累计值。"""
    headers, account_id = await _setup(
        app, client, auth_headers, create_account, place_order, scripted
    )
    fill = await _sell(client, app, headers, account_id, place_order, scripted, price="180")
    assert fill.realized_pnl == Decimal("400"), "5 股 ×(180-100)"
    await _backdate(app, fill.id, datetime.now(timezone.utc) - timedelta(days=1))

    scripted.bars["AAPL"] = [_bar(date.today() - timedelta(days=1), "100")]
    scripted.quotes["AAPL"] = _quote("100")
    body = await _get(client, headers, account_id)

    assert Decimal(body["realized_today"]) == Decimal("0"), (
        f"今日没有成交，已实现却是 {body['realized_today']}（读的是终身累计）"
    )
    async with app.state.db.session_factory() as session:
        pos = (await session.execute(
            select(Position).where(Position.account_id == account_id)
        )).scalar_one()
        assert pos.realized_pnl == Decimal("400"), "终身累计仍在（持仓视图要用）"


async def test_unrealized_uses_previous_close_not_entry_price(
    app, client, auth_headers, create_account, place_order, scripted
):
    """成本 100、昨收 110、现价 120 → 未实现必须是 qty×10，不是 qty×20。"""
    headers, account_id = await _setup(
        app, client, auth_headers, create_account, place_order, scripted, qty="10"
    )
    scripted.bars["AAPL"] = [_bar(date.today() - timedelta(days=1), "110")]
    scripted.quotes["AAPL"] = _quote("120")

    body = await _get(client, headers, account_id)
    assert Decimal(body["unrealized"]) == Decimal("100.00"), (
        f"未实现={body['unrealized']}；以昨收为基准应是 10×(120-110)=100，"
        f"用成本价会得到 10×(120-100)=200"
    )
    assert body["baseline"] == "previous_close"


async def test_missing_previous_close_is_reported_not_substituted(
    app, client, auth_headers, create_account, place_order, scripted
):
    """拿不到昨收：该标的不计入、进 gaps，绝不回落到成本价冒充。"""
    headers, account_id = await _setup(
        app, client, auth_headers, create_account, place_order, scripted
    )
    # 只有今天这根（盘中最新），没有更早的 ⇒ 无法确定"昨收"
    scripted.bars["AAPL"] = [_bar(date.today(), "130")]
    scripted.quotes["AAPL"] = _quote("120")

    body = await _get(client, headers, account_id)
    assert Decimal(body["unrealized"]) == Decimal("0")
    assert [g["symbol"] for g in body["gaps"]] == ["AAPL"]
    assert body["gaps"][0]["reason"] == "no_previous_close"
    assert Decimal(body["daily_pnl"]) == Decimal(body["realized_today"])


class _FrozenDatetime(datetime):
    """把路由模块里的 `datetime.now()` 钉在今天的 12:00 UTC。

    日界用例必须能**在任何真实时刻**跑出同一个结论：用真实时钟时，UTC 0 点这一小时
    里"今天、但在边界之前"的窗口是空集——第一版因此 `pytest.skip`，而那一小时的注入
    自证等于没跑（一条会跳过的判据，在跳过时就不存在）。钉死"现在"之后窗口恒为
    [00:00, 09:00)，不再依赖跑测试的时刻。
    """

    pinned = datetime.combine(date.today(), datetime.min.time(), timezone.utc) + timedelta(hours=12)

    @classmethod
    def now(cls, tz=None):
        return cls.pinned if tz is not None else cls.pinned.replace(tzinfo=None)


async def test_configured_day_boundary_is_applied(
    app, client, auth_headers, create_account, place_order, scripted, monkeypatch
):
    """日界设在 9 点：今天 08:00 的成交属于上一交易日，不算今日。

    两笔成交都显式回拨（08:00 / 10:00），"现在"钉在 12:00：0 点日界会把两笔都算进去
    （850），9 点日界只算 10:00 那笔（450）。写死 `time(0)` 会立刻红。

    这条判据踩过两次坑：第一版拿"昨天"的成交造数，两种日界都排除它，注入不红；第二版
    用真实时钟 + 当前小时，在 UTC 0 点整条 skip，注入同样不红。**判据本身太弱，和被测
    的实现一样会撒谎。**
    """
    from fd_paper_trading.api.routes import accounts as accounts_route
    from fd_paper_trading.config import get_settings

    monkeypatch.setattr(accounts_route, "datetime", _FrozenDatetime)

    headers, account_id = await _setup(
        app, client, auth_headers, create_account, place_order, scripted
    )
    scripted.bars["AAPL"] = [_bar(date.today() - timedelta(days=1), "100")]
    base = datetime.combine(date.today(), datetime.min.time(), timezone.utc)

    early = await _sell(client, app, headers, account_id, place_order, scripted, price="180")
    assert early.realized_pnl == Decimal("400")
    await _backdate(app, early.id, base + timedelta(hours=8))  # 今天 08:00，边界(9 点)之前
    late = await _sell(client, app, headers, account_id, place_order, scripted, price="190")
    assert late.realized_pnl == Decimal("450")
    await _backdate(app, late.id, base + timedelta(hours=10))  # 今天 10:00，边界之后

    monkeypatch.setattr(get_settings(), "pnl_day_start_utc_hour", 9)
    body = await _get(client, headers, account_id)
    assert Decimal(body["realized_today"]) == Decimal("450"), (
        f"08:00 那笔（400）在配置的日界(9 点)之前，不该计入今日；"
        f"实际今日已实现 {body['realized_today']}（写死 0 点会得到 850）"
    )
    assert body["day_start"].endswith("T09:00:00Z") or "09:00:00" in body["day_start"], (
        f"day_start 应落在今天 09:00，实际 {body['day_start']}"
    )


async def test_a_sell_fill_carries_its_own_realized_pnl(
    app, client, auth_headers, create_account, place_order, scripted
):
    """逐笔记账：卖出成交行必须带自己实现的那部分（否则"今天"永远求不出来）。"""
    headers, account_id = await _setup(
        app, client, auth_headers, create_account, place_order, scripted
    )
    fill = await _sell(client, app, headers, account_id, place_order, scripted, price="150")
    assert fill.realized_pnl == Decimal("250"), (
        f"5 股 ×(150-100) 应为 250，实际 {fill.realized_pnl}"
    )


async def test_legacy_fill_without_attribution_is_reported_not_counted_as_zero(
    app, client, auth_headers, create_account, place_order, scripted
):
    """本列上线前的成交是 NULL，不是 0：端点要说"有几笔没有归属"，别当保本平仓。

    把归属填 0 等于替一笔可能实现过大额盈亏的成交声明"零盈亏"。这条判据把 NULL
    与 0 分开：`realized_today` 只加有归属的，笔数单独报。
    """
    headers, account_id = await _setup(
        app, client, auth_headers, create_account, place_order, scripted
    )
    fill = await _sell(client, app, headers, account_id, place_order, scripted, price="180")
    assert fill.realized_pnl == Decimal("400")

    async with app.state.db.session_factory() as session:
        legacy = await session.get(Fill, fill.id)
        legacy.realized_pnl = None  # 模拟升级前写入的历史成交
        await session.commit()

    scripted.bars["AAPL"] = [_bar(date.today() - timedelta(days=1), "100")]
    scripted.quotes["AAPL"] = _quote("100")  # 现价=昨收 ⇒ 未实现为 0，看的只是归属
    body = await _get(client, headers, account_id)

    assert body["unattributed_fills"] == 1, (
        f"窗口内有一笔 NULL 归属的成交，端点却报 {body['unattributed_fills']} 笔"
    )
    assert Decimal(body["realized_today"]) == Decimal("0"), (
        f"NULL 归属不能被当成 0 之外的任何数：实际 {body['realized_today']}"
    )
