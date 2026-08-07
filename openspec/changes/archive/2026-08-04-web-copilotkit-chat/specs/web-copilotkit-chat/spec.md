## ADDED Requirements

### Requirement: Chat interface with AI assistant

The system SHALL provide a chat interface accessible from all authenticated pages. The interface SHALL include a text input for user messages, a message history display, and a send button. The chat SHALL maintain conversation context across multiple interactions within a session.

#### Scenario: User opens chat interface
- **WHEN** user clicks the chat icon or opens the chat panel
- **THEN** a chat interface appears with an input field, send button, and empty message history (or previous session history if persisted)

#### Scenario: User sends a message
- **WHEN** user types a message and clicks send (or presses Enter)
- **THEN** the message appears in the chat history and the AI assistant processes the request

### Requirement: AI assistant executes MCP tools

The AI assistant SHALL have access to the existing MCP tools: `place_order`, `cancel_order`, `list_accounts`, `get_account`, and `list_orders`. When the user requests a trading operation, the assistant SHALL invoke the appropriate tool and return the result in a user-friendly format.

#### Scenario: User asks to place an order
- **WHEN** user says "Buy 100 shares of AAPL at market price"
- **THEN** the assistant invokes `place_order` with the correct parameters and confirms the order was placed, showing order details

#### Scenario: User asks for account balance
- **WHEN** user says "What's my cash balance?"
- **THEN** the assistant invokes `list_accounts` or `get_account` and displays the balance

#### Scenario: User asks to cancel an order
- **WHEN** user says "Cancel my TSLA order"
- **THEN** the assistant invokes `list_orders` to find the order, then `cancel_order` to cancel it, and confirms the cancellation

### Requirement: Chat authentication and authorization

The chat interface SHALL require the user to be authenticated (same as the rest of the app). The AI assistant SHALL only execute operations on accounts owned by the authenticated user. Tool invocations SHALL use the user's JWT or API token for authorization.

#### Scenario: Unauthenticated user cannot access chat
- **WHEN** an unauthenticated user tries to open the chat
- **THEN** they are redirected to the login page

#### Scenario: AI assistant respects account ownership
- **WHEN** user asks to check another user's account
- **THEN** the assistant returns an error or denies the request

### Requirement: Real-time feedback and error handling

The chat interface SHALL show loading indicators while the AI is processing. If a tool invocation fails, the assistant SHALL display a clear error message. The chat SHALL handle network errors gracefully and allow the user to retry.

#### Scenario: Tool invocation shows loading state
- **WHEN** the assistant is executing a tool (e.g., placing an order)
- **THEN** a loading indicator appears in the chat until the tool completes

#### Scenario: Tool invocation fails
- **WHEN** a tool invocation returns an error (e.g., insufficient funds)
- **THEN** the assistant displays the error message and suggests corrective action

#### Scenario: Network error during chat
- **WHEN** the network connection is lost while sending a message
- **THEN** an error message appears with a retry option
