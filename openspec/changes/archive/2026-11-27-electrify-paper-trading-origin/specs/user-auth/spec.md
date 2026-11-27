# Spec Delta

## Purpose

把「用户可获得令牌」变成可验收的能力面：令牌不只是 REST 端点，还必须有页面与导航，
否则产品面承诺的「去服务 Web 铸造」无法兑现。

## MODIFIED Requirements

### Requirement: Revocable API tokens

A authenticated user MAY create a long-lived API token (`POST /api/v1/tokens`, `201`) whose plaintext starts with `fdt_`; the plaintext is returned **exactly once** at creation and only a SHA-256 hash is stored. The system SHALL accept an API token as a bearer credential interchangeably with a JWT. `GET /api/v1/tokens` lists the user's tokens (never returning the plaintext). `DELETE /api/v1/tokens/{id}` revokes a token (`204`); a revoked token MUST no longer authenticate (`401`). Revoking an API token MUST NOT invalidate the user's JWTs.

The SPA SHALL provide a token surface reachable from the app's primary navigation on which an
authenticated user can see their tokens (state only, never the plaintext), mint a new one, and
revoke one. A newly minted plaintext SHALL be shown once together with a plain instruction that
this value is what an external client (for example 壹座's paper-trading credential card) is
waiting for, and SHALL NOT be recoverable after the user leaves the surface.

#### Scenario: Create, use, list, and revoke a token
- **WHEN** a user creates an API token
- **THEN** the `201` response includes the `fdt_`-prefixed plaintext and an `id`
- **AND WHEN** the user lists tokens
- **THEN** each listed token has `token: null`
- **AND WHEN** the plaintext token is used as a bearer credential
- **THEN** the request authenticates successfully
- **AND WHEN** the token is revoked via `DELETE`
- **THEN** the response is `204` and subsequent use of the plaintext token returns `401`

#### Scenario: 令牌可从界面铸出

- **WHEN** 一个已登录用户在 SPA 的令牌面点击「铸造新令牌」并确认
- **THEN** 页面显示该令牌的 `fdt_` 明文一次
- **AND THEN** 明文旁明确写出这是外部客户端要粘贴的值
- **AND THEN** 该页面的列表项不再显示明文，且页面刷新后也无法再取回

#### Scenario: 令牌可从界面吊销

- **WHEN** 用户在令牌面吊销一枚令牌
- **THEN** 列表状态变为已吊销或该行消失
- **AND THEN** 用该令牌发起的请求返回 `401`

#### Scenario: Revoking an API token leaves JWTs valid
- **WHEN** a user revokes one API token
- **THEN** the user's existing JWT continues to authenticate while the revoked API token does not
