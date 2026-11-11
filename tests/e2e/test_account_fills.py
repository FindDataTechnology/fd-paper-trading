"""账户级成交流水（把已写好却没人调的 `FillRepository.list_for_account` 接上）。

为什么单独一条：`GET /orders/{id}` 已带 `fills`，但那是逐单视角，
答不了"这个账户这两天成交了什么"。仓储早就存在，缺的只是一个调用方。
"""
from __future__ import annotations

import pytest


async def _account(create_account, h, **kw):
    """建账户并取出 id（`create_account` 返回的是响应对象，不是 id）。"""
    r = await create_account(h, **kw)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_fills_are_listed_for_the_account(client, register, login, auth_headers,
                                               create_account, place_order):
    h = await auth_headers()
    account_id = await _account(create_account, h)
    await place_order(h, account_id, symbol="AAPL", side="BUY", quantity="10")
    await place_order(h, account_id, symbol="MSFT", side="BUY", quantity="5")

    r = await client.get(f"/api/v1/accounts/{account_id}/fills", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 2
    assert {row["side"] for row in rows} == {"BUY"}
    assert all(float(row["price"]) > 0 and float(row["quantity"]) > 0 for row in rows)
    # 倒序：仓储按 Fill.ts desc 排
    assert rows[0]["ts"] >= rows[-1]["ts"]


@pytest.mark.asyncio
async def test_limit_bounds_the_list(client, auth_headers, create_account, place_order):
    h = await auth_headers()
    account_id = await _account(create_account, h)
    for _ in range(3):
        await place_order(h, account_id, symbol="AAPL")

    r = await client.get(f"/api/v1/accounts/{account_id}/fills?limit=2", headers=h)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_invalid_limit_is_refused(client, auth_headers, create_account):
    h = await auth_headers()
    account_id = await _account(create_account, h)
    r = await client.get(f"/api/v1/accounts/{account_id}/fills?limit=0", headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_limit"


@pytest.mark.asyncio
async def test_another_users_account_yields_nothing(
        client, register, login, auth_headers, create_account, place_order):
    """别人的账户 id → 空列表（不给 403，也不用它当存在性探针）。"""
    h1 = await auth_headers()
    account_id = await _account(create_account, h1)
    await place_order(h1, account_id, symbol="AAPL")

    await register("other@example.com", "Pw123456!")
    r = await login("other@example.com", "Pw123456!")
    h2 = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = await client.get(f"/api/v1/accounts/{account_id}/fills", headers=h2)
    assert r.status_code == 200, r.text
    assert r.json() == []
