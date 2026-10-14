# web-mobile-shell Specification

## Purpose

约束移动端 H5 外壳（/m）：自适应布局（竖屏底栏单列/横屏侧栏）、与桌面同源同凭据、行情/交易/持仓/订单/策略五 tab 与按 tab 作用域的实时订阅。
## Requirements

### Requirement: Mobile trading route under shared auth

The system SHALL provide a `/m` route, protected by the same JWT-based
`ProtectedRoute` as the desktop shell, that renders a separate mobile shell
(not the desktop `Layout`).

#### Scenario: Authenticated user opens /m on a phone

- **WHEN** an authenticated user opens `/m`
- **THEN** the SPA renders the mobile shell with a tab bar and the Watch tab active

#### Scenario: Unauthenticated visitor opens /m

- **WHEN** an unauthenticated visitor opens `/m`
- **THEN** the SPA redirects to `/login` and returns the user to `/m` after a successful login

### Requirement: Auto-adapting responsive layout

The mobile shell SHALL reflow by viewport width: a fixed bottom tab bar with a
single-column content area on portrait phones, and a left side rail with the
content filling the remaining width on landscape/tablet viewports (>=768px).

#### Scenario: Portrait phone viewport

- **WHEN** the viewport is narrower than 768px
- **THEN** the tab bar renders fixed at the bottom of the screen and each tab's content is a single column

#### Scenario: Landscape or tablet viewport

- **WHEN** the viewport is 768px or wider
- **THEN** the tab bar becomes a vertical left side rail and the content area expands to fill the remaining width

### Requirement: Account summary in the top bar

The mobile shell SHALL show an account selector and the active account's
indicative equity in a sticky top bar, reusing the existing account hooks and
components.

#### Scenario: Account switcher visible on mobile
- **WHEN** an authenticated user opens /m
- **THEN** the top bar shows the account selector and the active account's equity

#### Scenario: Equity reflects the selected account
- **WHEN** the user switches the active account
- **THEN** the indicative equity updates to that account's summary
auto-selecting the first account when none is active.

### Requirement: Five actionable trading tabs

The mobile shell SHALL provide five tabs - Watch, Trade, Positions, Orders, and
Strategies - each reusing the existing REST hooks and the `/ws` realtime layer
rather than duplicating backend endpoints.

- **Watch**: typeahead symbol search plus a live watchlist of streaming quotes; tapping a symbol selects it and switches to the Trade tab.
- **Trade**: a symbol picker, a price chart, and the order ticket (market and limit orders).
- **Positions**: position cards with Close and Reduce actions.
- **Orders**: order cards with a Cancel action for open orders, updated in place from realtime order/fill events.
- **Strategies**: strategy cards showing live run status with Start paper run and Stop run actions.

#### Scenario: Place an order from the Trade tab

- **WHEN** the user submits a valid order ticket on `/m`
- **THEN** the order is placed via `POST /orders` and the orders, positions, and account caches are invalidated

#### Scenario: Cancel an open order

- **WHEN** the user taps Cancel on an open order card
- **THEN** the order is cancelled via `DELETE /orders/{id}`

#### Scenario: Stop a running paper strategy

- **WHEN** the user taps Stop run on a strategy card that has an active run
- **THEN** the run is stopped via `POST /strategy-runs/{id}/stop` (resting limit orders are cancelled; positions are not closed)

### Requirement: Realtime subscriptions scoped to the active tab

The mobile shell SHALL subscribe to quote streams only for the symbols relevant
to the currently mounted tab, unsubscribing on tab unmount via the ref-counted
realtime store so that mobile bandwidth and battery use stay bounded.

#### Scenario: Quotes subscribe on tab mount and stop on unmount

- **WHEN** the user switches away from the Watch tab
- **THEN** the quote subscriptions for that tab's symbols are released via the realtime store

#### Scenario: Order updates keep flowing while the Orders tab is mounted

- **WHEN** an order or fill event arrives while the Orders tab is mounted
- **THEN** the affected order cards update in place without a refetch

### Requirement: Desktop entry point and return link

The desktop layout SHALL link to `/m` from its header, and the mobile shell
SHALL provide a "Desktop" link back to `/` in its top bar.

#### Scenario: Desktop links to mobile

- **WHEN** an authenticated user views the desktop header
- **THEN** a link to /m is present

#### Scenario: Mobile links back to desktop

- **WHEN** the user taps "Desktop" in the mobile top bar
- **THEN** the browser navigates to /
