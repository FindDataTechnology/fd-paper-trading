"""Auth: registration, login, JWT and API tokens, isolation, error format."""

import uuid


async def test_register_success(client, register):
    r = await register("alice@example.com")
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert "id" in body
    assert "password" not in body
    assert "password_hash" not in body


async def test_register_duplicate_rejected(client, register):
    await register("alice@example.com")
    r = await register("alice@example.com")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_exists"


async def test_register_validation_error(client, register):
    r = await register("alice@example.com", password="short")
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["fields"]


async def test_login_success(client, register, login):
    await register("alice@example.com")
    r = await login("alice@example.com")
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert uuid.UUID(body["user_id"])


async def test_login_invalid_credentials(client, register, login):
    await register("alice@example.com")
    r = await login("alice@example.com", password="wrong-password")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


async def test_unauthenticated_rejected(client):
    r = await client.get("/api/v1/accounts")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_api_token_create_list_revoke(client, auth_headers):
    headers = await auth_headers("alice@example.com")

    r = await client.post("/api/v1/tokens", json={"name": "bot"}, headers=headers)
    assert r.status_code == 201
    plaintext = r.json()["token"]
    assert plaintext.startswith("fdt_")
    token_id = r.json()["id"]

    listed = await client.get("/api/v1/tokens", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["token"] is None  # plaintext never returned on list

    # API token authenticates just like a JWT.
    via_token = await client.get(
        "/api/v1/accounts", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert via_token.status_code == 200

    revoked = await client.delete(f"/api/v1/tokens/{token_id}", headers=headers)
    assert revoked.status_code == 204

    after_revoke = await client.get(
        "/api/v1/accounts", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert after_revoke.status_code == 401


async def test_revoked_token_leaves_jwt_valid(client, auth_headers):
    headers = await auth_headers("alice@example.com")
    r = await client.post("/api/v1/tokens", json={"name": "bot"}, headers=headers)
    plaintext = r.json()["token"]
    token_id = r.json()["id"]
    await client.delete(f"/api/v1/tokens/{token_id}", headers=headers)

    # The original JWT in `headers` still works.
    r2 = await client.get("/api/v1/accounts", headers=headers)
    assert r2.status_code == 200
    # ...but the revoked API token does not.
    r3 = await client.get("/api/v1/accounts", headers={"Authorization": f"Bearer {plaintext}"})
    assert r3.status_code == 401


# ---- 2026-11-13：`user_inactive` 这道门零测试 --------------------------------
#
# user-auth 规格两句话：
#   「An inactive user account MUST return `403` with code `user_inactive`」（登录）
#   「inactive users MUST be rejected」（bearer 依赖）
#
# 实测：三处检查（auth.py 登录、auth/dependencies.py、api/routes/ws.py）都在，
# 而 `grep user_inactive tests/` 零命中——没有任何测试碰过这个码。
# 更要紧的是**没有产品入口把 is_active 设成 False**（routes 里没有任何写入点），
# 所以这道门迄今只可能被直接改库触发，也就一直没人验过它是否真的拦得住。
# 下面三条用直接改库（唯一存在的路径）把门逼出来：
#   - 登录：403 user_inactive（规格指定）
#   - 已签发的 JWT：401（"凭证被拒"，不是"用户不存在"）
# 两种码不一致是**规格有意的**，一并钉住：将来谁想"统一错误码"，这里会红。


async def _set_active(app, email: str, active: bool) -> None:
    from sqlalchemy import select

    from fd_paper_trading.persistence.models import User

    async with app.state.db.session_factory() as session:
        user = (await session.execute(
            select(User).where(User.email == email)
        )).scalars().first()
        assert user is not None, f"用户 {email} 不存在"
        user.is_active = active
        await session.commit()


async def test_inactive_user_cannot_log_in(client, register, login, app):
    await register("alice@example.com")
    await _set_active(app, "alice@example.com", False)

    r = await login("alice@example.com")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "user_inactive"


async def test_inactive_user_token_is_rejected_with_401(client, auth_headers, app):
    headers = await auth_headers("alice@example.com")   # 停用**前**签发的 JWT
    assert (await client.get("/api/v1/accounts", headers=headers)).status_code == 200

    await _set_active(app, "alice@example.com", False)

    r = await client.get("/api/v1/accounts", headers=headers)
    assert r.status_code == 401, "停用后旧令牌必须立刻失效，且是 401（凭证被拒）"
    assert r.json()["error"]["code"] == "unauthorized"


async def test_reactivating_the_user_restores_access(client, auth_headers, app):
    """停用是状态，不是单向操作：改回 True 之后同一个令牌又能用。

    这条防的是"顺手加个缓存/黑名单"式改法——把停用做成不可逆，
    会让人以为改库回滚是安全的，实际不是。
    """
    headers = await auth_headers("alice@example.com")
    await _set_active(app, "alice@example.com", False)
    assert (await client.get("/api/v1/accounts", headers=headers)).status_code == 401

    await _set_active(app, "alice@example.com", True)
    assert (await client.get("/api/v1/accounts", headers=headers)).status_code == 200


# ---- 2026-11-13：`FD_ADMIN_EMAILS` 引导管理员这条路径零测试 ------------------
#
# user-groups 规格：「The first admin(s) SHALL be bootstrapped via the
# `FD_ADMIN_EMAILS` environment variable (comma-separated emails granted
# `is_admin` at registration)」+ 场景「Env-bootstrapped admin」。
#
# 而 `grep FD_ADMIN_EMAILS tests/` 零命中——**没有任何测试走过这条路**。
# 更说明问题的是 e2e 那边：需要管理员的用例直接改库提权
# （`e2e/test_strategy_code_gate.py`：`user.is_admin = True; await session.commit()`，
# 注释写着 "Promote to admin (directly) so we can create a group"）。
# 于是即使引导逻辑彻底坏掉，所有测试照样全绿——而第一次部署的人会发现自己没进管理台。
#
# 这条还有一个静默失败的入口：`get_settings()` 是 lru_cache，环境变量必须在首次读取前
# 设好、或读完清缓存。写错顺序的实现会让"设了没生效"与"没设"完全同形——所以下面每条
# 都在 fixture 里显式 cache_clear，并断言 clearance 真的改变了行为（见反面对照）。


import pytest as _pytest


@_pytest.fixture
def admin_emails(monkeypatch):
    """设 FD_ADMIN_EMAILS 并清 settings 缓存；结束时还原。"""
    from fd_paper_trading.config import get_settings

    def _set(raw: str):
        monkeypatch.setenv("FD_ADMIN_EMAILS", raw)
        get_settings.cache_clear()
        return raw

    yield _set
    get_settings.cache_clear()


async def test_env_listed_email_registers_as_admin(client, admin_emails):
    admin_emails("boss@example.com, ops@example.com")

    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "boss@example.com", "password": "password123"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["is_admin"] is True


async def test_email_outside_the_list_registers_as_non_admin(client, admin_emails):
    admin_emails("boss@example.com")

    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "someone@example.com", "password": "password123"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["is_admin"] is False


async def test_admin_matching_is_case_insensitive_and_trims_spaces(client, admin_emails):
    """`BOSS@Example.com ` 与 `boss@example.com` 是同一个邮箱。

    少一个 `.lower()` 或 `.strip()`，运维写 `Boss@Example.com` 就悄悄拿不到管理员权限，
    而注册是成功的、返回 201——只有"进不去管理台"这个症状，几周后才会被人发现。
    """
    admin_emails("  BOSS@Example.COM  ")

    # 注册侧也走大写：两边都规范化才算真的比较得对。
    # （第一版两边都写小写，注入「去掉 .lower()」时**不红**——判据没打到那一侧。）
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "Boss@Example.com", "password": "password123"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["is_admin"] is True


async def test_without_the_variable_nobody_is_admin(client, monkeypatch):
    from fd_paper_trading.config import get_settings

    monkeypatch.delenv("FD_ADMIN_EMAILS", raising=False)
    get_settings.cache_clear()
    try:
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": "anyone@example.com", "password": "password123"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["is_admin"] is False
    finally:
        get_settings.cache_clear()
