# web-mobile-shell Specification

## Purpose

A dedicated mobile H5 trading surface at `/m` that auto-adapts its layout to
phones (portrait and landscape) and tablets, reusing the same JWT auth, `/ws`
realtime layer, and React Query hooks as the desktop dashboard. The desktop
dashboard is left untouched. The surface is fully actionable: account
switching, order placement/cancellation, position close/reduce, and strategy
paper-run start/stop.

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

### Requirement: Desktop entry point and return link

The desktop layout SHALL link to `/m` from its header, and the mobile shell
SHALL provide a "Desktop" link back to `/` in its top bar.
