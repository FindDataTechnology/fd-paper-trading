# Spec Delta

## Purpose

让「导航里能到达」成为可验收的判据——一个只能手敲 URL 或手敲 curl 才有入口的能力面，
在产品上等同于不存在。

## MODIFIED Requirements

### Requirement: Single-page application shell
The system SHALL provide a single-page React application shell with client-side routing, a persistent navigation layout, and a default landing route that redirects authenticated users to the dashboard and unauthenticated users to the login screen. The persistent navigation SHALL include an entry for the API-token surface required by `user-auth`, so a signed-in user reaches it without typing a URL.

#### Scenario: Authenticated user lands on the app
- **WHEN** an authenticated user opens the root URL `/`
- **THEN** the SPA renders the application shell with navigation and routes the user to the accounts dashboard

#### Scenario: Unauthenticated user lands on the app
- **WHEN** an unauthenticated visitor opens any protected route
- **THEN** the SPA redirects the user to `/login` and after a successful login returns them to the originally requested route

#### Scenario: 令牌面在导航里可达

- **WHEN** 一个已登录用户查看任何一页的持久导航
- **THEN** 入口里有一项通向令牌面
- **AND THEN** 点击它到达的是令牌页，而不是登录页或 404
