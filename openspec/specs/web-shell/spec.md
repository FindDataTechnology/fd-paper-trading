# web-shell Specification

## Purpose

约束 Web 外壳与认证路由：受保护路由跳转、登录/注册流与错误呈现、桌面布局导航与登出、桌面与移动入口互通。
## Requirements

### Requirement: Single-page application shell
The system SHALL provide a single-page React application shell with client-side routing, a persistent navigation layout, and a default landing route that redirects authenticated users to the dashboard and unauthenticated users to the login screen.

#### Scenario: Authenticated user lands on the app
- **WHEN** an authenticated user opens the root URL `/`
- **THEN** the SPA renders the application shell with navigation and routes the user to the accounts dashboard

#### Scenario: Unauthenticated user lands on the app
- **WHEN** an unauthenticated visitor opens any protected route
- **THEN** the SPA redirects the user to `/login` and after a successful login returns them to the originally requested route

### Requirement: Login and registration UI
The system SHALL provide login and registration screens that authenticate against the existing `POST /api/v1/auth/register` and `POST /api/v1/auth/login` endpoints and store the resulting JWT access token in a client session.

#### Scenario: Successful login
- **WHEN** a user submits valid credentials on the login screen
- **THEN** the SPA obtains a JWT from `/api/v1/auth/login`, stores it in the session, and navigates to the dashboard

#### Scenario: Invalid credentials
- **WHEN** a user submits invalid credentials
- **THEN** the SPA displays the error returned by the API and does not navigate away from the login screen

#### Scenario: Registration
- **WHEN** a new user submits a valid registration form
- **THEN** the SPA registers the user via `/api/v1/auth/register`, then proceeds to login

### Requirement: Authenticated REST and WebSocket requests
The system SHALL attach the stored JWT as an `Authorization: Bearer <token>` header on every REST request and authenticate the WebSocket connection by sending the token in the first message.

#### Scenario: REST request carries the token
- **WHEN** the SPA makes any request to `/api/v1/*`
- **THEN** the request includes the `Authorization: Bearer <token>` header

#### Scenario: Token expiry handling
- **WHEN** a REST request returns `401 Unauthorized`
- **THEN** the SPA clears the stored token and redirects the user to `/login`

### Requirement: Production serving from FastAPI
The system SHALL serve the built SPA from FastAPI `StaticFiles` mounted at `/` in production, with API and WebSocket routes taking precedence and all other unmatched paths falling back to `index.html` for client-side routing.

#### Scenario: Deep link to a client route
- **WHEN** a browser requests `/accounts/123/orders` directly
- **THEN** FastAPI serves `index.html` and the SPA renders the orders view for that account

#### Scenario: API routes are not shadowed
- **WHEN** a client requests `/api/v1/orders` or `/ws`
- **THEN** the request is handled by the API or WebSocket router, not the static file mount

### Requirement: Development proxy to the backend
The system SHALL run a Vite dev server in development that proxies `/api` and `/ws` requests to the FastAPI backend, so the SPA and API share a single effective origin without CORS configuration.

#### Scenario: Dev request reaches the API
- **WHEN** the Vite dev server receives a request to `/api/v1/accounts`
- **THEN** it proxies the request to the FastAPI backend and returns the backend response to the browser
