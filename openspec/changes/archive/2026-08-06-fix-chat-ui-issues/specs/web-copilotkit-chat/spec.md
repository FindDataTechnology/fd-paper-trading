## ADDED Requirements

### Requirement: Chat UI renders with proper styling

The chat sidebar SHALL render with CopilotKit's built-in styles. Message bubbles, the input field, the send button, and the panel header SHALL be styled consistently with the rest of the application.

#### Scenario: Chat panel is opened
- **WHEN** the user clicks the chat icon on an authenticated page
- **THEN** the sidebar opens with styled message bubbles, a styled input field, and a styled header showing "Trading Assistant"

#### Scenario: Messages are displayed
- **WHEN** the AI assistant responds to a user message
- **THEN** the response appears in a styled message bubble with proper typography, spacing, and color

### Requirement: Chat only appears on authenticated pages

The chat sidebar SHALL only be rendered on pages that require authentication. The chat SHALL NOT appear on the login or register pages.

#### Scenario: Unauthenticated user visits login page
- **WHEN** a user navigates to `/login` or `/register`
- **THEN** no chat sidebar or chat icon is visible

#### Scenario: Authenticated user visits dashboard
- **WHEN** a logged-in user navigates to `/` (dashboard) or any protected route
- **THEN** the chat icon appears and the sidebar can be opened

### Requirement: Chat assistant is aware of active account

The chat assistant SHALL know which account is currently active. When the user asks to place an order, check balance, or list orders, the assistant SHALL use the active account context without requiring the user to specify an account ID.

#### Scenario: User asks to place an order
- **WHEN** the user says "Buy 10 shares of AAPL" and an account is active
- **THEN** the assistant uses the active account ID automatically to place the order

#### Scenario: No account is active
- **WHEN** the user asks to place an order but no account is active
- **THEN** the assistant informs the user they need to create or select an account first
