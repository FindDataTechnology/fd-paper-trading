## ADDED Requirements

### Requirement: Backend chat endpoint e2e test

The system SHALL provide a `tests/e2e/test_chat_e2e.py` suite that drives the `/api/v1/chat` endpoint through httpx's ASGI transport. The suite SHALL cover: unauthenticated requests return 401; missing OpenAI API key returns 500 with `openai_not_configured` error; successful chat message without tool calls returns AI response; chat message triggering `list_accounts` tool returns account list; chat message triggering `place_order` tool places order and returns confirmation; chat message triggering `get_account` tool returns account details; chat message triggering `list_orders` tool returns order list; chat message triggering `cancel_order` tool cancels order and returns confirmation; tool invocation on another user's account returns error; malformed tool arguments return error. The suite SHALL mock LiteLLM responses to avoid external API calls.

#### Scenario: Unauthenticated chat request rejected
- **WHEN** a POST request to `/api/v1/chat` is made without authentication
- **THEN** the response status is 401

#### Scenario: Chat without OpenAI key returns error
- **WHEN** `OPENAI_API_KEY` is not set and a chat message is sent
- **THEN** the response status is 500 with error code `openai_not_configured`

#### Scenario: Chat message without tool calls
- **WHEN** user sends "Hello" and the AI responds without tool calls
- **THEN** the response contains the AI message and `tool_calls` is null

#### Scenario: Chat triggers list_accounts tool
- **WHEN** user sends "Show my accounts" and the AI invokes `list_accounts`
- **THEN** the response contains the user's accounts and `tool_calls` includes the invocation

#### Scenario: Chat triggers place_order tool
- **WHEN** user sends "Buy 10 shares of AAPL" and the AI invokes `place_order`
- **THEN** an order is placed in the database and the response confirms the order

#### Scenario: Chat tool invocation respects account ownership
- **WHEN** user asks to check another user's account
- **THEN** the tool returns an error and no data is leaked

### Requirement: Frontend chat UI e2e test

The system SHALL provide a `web/e2e/chat.spec.ts` Playwright test that verifies the CopilotKit chat interface in a real browser. The test SHALL cover: chat sidebar is hidden by default; clicking the chat icon opens the sidebar; user can type a message and send it; AI response appears in the chat; loading indicator shows while AI is processing; error message displays when chat fails; chat persists across page navigation within the app. The test SHALL run headless and mock the `/api/v1/chat` endpoint to avoid external API calls.

#### Scenario: Chat sidebar opens on click
- **WHEN** user clicks the chat icon in the UI
- **THEN** the CopilotKit sidebar appears with a text input and message history

#### Scenario: User sends message and receives response
- **WHEN** user types "Hello" and clicks send
- **THEN** the message appears in the chat history and an AI response is rendered

#### Scenario: Chat shows loading state
- **WHEN** user sends a message and the AI is processing
- **THEN** a loading indicator appears until the response arrives

#### Scenario: Chat error displays message
- **WHEN** the `/api/v1/chat` endpoint returns an error
- **THEN** an error message is displayed in the chat interface

### Requirement: Chat integration test with mocked LLM

The system SHALL provide a test that verifies the full chat flow with a mocked LiteLLM response. The test SHALL inject a mock that returns predefined tool calls, execute the chat endpoint, and verify that tools are invoked correctly and the final response includes tool call metadata. This ensures deterministic testing without external API dependencies.

#### Scenario: Mocked LLM returns tool call
- **WHEN** LiteLLM mock returns a `place_order` tool call
- **THEN** the order is placed in the database and the response includes `tool_calls` with the invocation details

#### Scenario: Mocked LLM returns text response
- **WHEN** LiteLLM mock returns a text response without tool calls
- **THEN** the response contains the text and `tool_calls` is null
