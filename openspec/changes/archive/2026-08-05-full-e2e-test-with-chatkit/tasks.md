## 1. Backend Chat E2E Tests

- [x] 1.1 Create `tests/e2e/test_chat_e2e.py` with pytest fixtures for chat testing
- [x] 1.2 Add test for `/api/v1/chat` authentication (unauthenticated request returns 401)
- [x] 1.3 Add test for OpenAI not configured error (missing API key returns 500 with `openai_not_configured`)
- [x] 1.4 Add test for `list_accounts` tool invocation (mock LiteLLM response, verify tool executes and returns user's accounts)
- [x] 1.5 Add test for `get_account` tool invocation (verify account details returned, ownership validated)
- [x] 1.6 Add test for `place_order` tool invocation (mock LLM, verify order placed and returned in response)
- [x] 1.7 Add test for `list_orders` tool invocation (verify orders returned with optional status filter)
- [x] 1.8 Add test for `cancel_order` tool invocation (verify order cancelled and status updated)
- [x] 1.9 Add test for account ownership validation (tool rejects access to another user's account)
- [x] 1.10 Add test for tool execution error handling (verify graceful error response when tool fails)

## 2. Frontend Chat E2E Tests (Playwright)

- [x] 2.1 Create `web/e2e/chat.spec.ts` with Playwright test structure
- [x] 2.2 Add test for chat panel visibility (CopilotSidebar renders with "Trading Assistant" title)
- [x] 2.3 Add test for chat panel toggle (clicking chat icon opens/closes the sidebar)
- [x] 2.4 Add test for sending a message (type message, press Enter, verify message appears in history)
- [x] 2.5 Add test for AI response rendering (mock `/api/v1/chat` response, verify assistant message displays)
- [x] 2.6 Add test for tool call metadata display (verify tool calls shown in chat UI when present in response)
- [x] 2.7 Add test for chat authentication (unauthenticated user cannot access chat endpoint)
- [x] 2.8 Add test for error handling (mock 500 response, verify error message displayed to user)

## 3. Integration Test

- [x] 3.1 Create `tests/e2e/test_chat_integration.py` for full flow testing
- [x] 3.2 Add test for complete chat flow: user message → LLM mock → tool execution → response with tool_calls metadata
- [x] 3.3 Add test for multi-turn conversation context (verify account_id passed in subsequent requests)
- [x] 3.4 Add test for concurrent chat requests (verify isolation between parallel conversations)

## 4. Test Infrastructure

- [x] 4.1 Add LiteLLM mock fixture to `tests/conftest.py` (mock `litellm.acompletion` with configurable responses)
- [x] 4.2 Add chat-specific fixtures (sample tool call responses, error responses)
- [x] 4.3 Update `web/e2e/run-backend.sh` to set `OPENAI_API_KEY` mock for Playwright tests
- [x] 4.4 Add Playwright route mocking for `/api/v1/chat` endpoint in frontend tests

## 5. Verification

- [x] 5.1 Run `uv run pytest tests/e2e/test_chat_e2e.py` and verify all backend chat tests pass
- [x] 5.2 Run `uv run pytest tests/e2e/test_chat_integration.py` and verify integration tests pass
- [x] 5.3 Run `npx playwright test web/e2e/chat.spec.ts` and verify frontend chat tests pass (or document skip reason)
- [x] 5.4 Run full test suite (`uv run pytest` + `npm test` + `npx playwright test`) and verify no regressions
