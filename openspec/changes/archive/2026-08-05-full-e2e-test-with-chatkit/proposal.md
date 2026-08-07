## Why

The project has comprehensive e2e tests for the trading flow (register → login → create account → place order → see position → cancel order) but **zero test coverage for the CopilotKit chat interface**. The chat feature (`/api/v1/chat` endpoint + `ChatProvider.tsx` frontend) is a core capability that allows users to interact with an AI assistant for trading operations. Without e2e tests, regressions in chat authentication, tool invocation, or UI rendering go undetected.

## What Changes

- Add backend e2e test for `/api/v1/chat` endpoint: verify authentication, tool invocation (place_order, list_accounts, get_account, list_orders, cancel_order), error handling when OpenAI is not configured, and account ownership validation
- Add frontend Playwright e2e test for CopilotKit chat UI: verify chat panel opens, user can send messages, AI responses render, tool calls show loading states, and errors display correctly
- Add integration test that verifies the full chat flow: user sends message → backend invokes tool → frontend renders result with tool call metadata

## Capabilities

### New Capabilities
- `chat-e2e-tests`: End-to-end tests for the CopilotKit chat interface, covering backend API endpoint, frontend UI, and full integration flow

### Modified Capabilities
- `test-suite`: Add chat e2e tests to the existing test pyramid (backend e2e, frontend e2e, integration)

## Impact

- **Test files**: New test files in `tests/e2e/test_chat_e2e.py` (backend), `web/e2e/chat.spec.ts` (frontend Playwright)
- **Test fixtures**: May need to mock OpenAI API responses for deterministic testing
- **CI**: Existing CI pipeline already runs all tests; no changes needed
- **Dependencies**: No new dependencies (uses existing pytest, Playwright, httpx)
- **APIs**: Tests exercise existing `/api/v1/chat` endpoint; no API changes
