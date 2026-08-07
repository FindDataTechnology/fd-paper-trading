## Context

The project has a CopilotKit-based chat interface (`ChatProvider.tsx` → `/api/v1/chat`) that uses LiteLLM to invoke trading tools (place_order, cancel_order, list_accounts, get_account, list_orders). The backend endpoint is implemented in `src/fd_trade/api/routes/chat.py`. Existing e2e tests cover the trading flow but not the chat path. The chat endpoint requires `OPENAI_API_KEY` (or compatible LiteLLM provider) which makes deterministic testing challenging.

## Goals / Non-Goals

**Goals:**
- Backend e2e test for `/api/v1/chat` that exercises all 5 trading tools via mocked LLM responses
- Frontend Playwright test that verifies the CopilotKit chat panel opens, sends messages, and renders responses
- Tests must run without a real LLM API key (mock at the litellm boundary)

**Non-Goals:**
- Testing CopilotKit library internals (their problem)
- Testing LLM response quality (non-deterministic, out of scope for e2e)
- Load testing or performance benchmarking of the chat endpoint

## Decisions

### D1: Mock LiteLLM, not the HTTP endpoint

**Decision**: Mock `litellm.acompletion` in backend tests to return canned tool-call responses.

**Rationale**: Mocking at the litellm boundary tests the full chat route (auth → tool dispatch → response assembly) without needing an LLM API key or network. Alternative considered: mocking the entire `/chat` route — rejected because it skips the tool execution logic we need to verify.

### D2: Backend test uses httpx ASGI transport (consistent with existing e2e)

**Decision**: Use the same `tests/e2e/` pattern — `httpx.AsyncClient` + ASGI transport, simulated market data, in-memory SQLite.

**Rationale**: Consistency with existing e2e tests. No need for a separate test harness.

### D3: Frontend chat test extends existing Playwright suite

**Decision**: Add `web/e2e/chat.spec.ts` alongside `happy-path.spec.ts`. Reuse `run-backend.sh` setup.

**Rationale**: Single Playwright project, shared backend setup. The chat panel is part of the same SPA.

### D4: Frontend test mocks the chat endpoint at the network level

**Decision**: Use Playwright's `page.route()` to intercept `/api/v1/chat` and return a canned response.

**Rationale**: Avoids needing a real LLM in the frontend e2e. The backend chat test already covers the real logic; the frontend test verifies UI rendering and CopilotKit integration.

## Risks / Trade-offs

- **[LiteLLM mock drift]** → If `chat.py` changes how it calls litellm (e.g., switches to a different SDK), mocks break. Mitigation: keep mocks close to the actual call signature; the backend test will fail loudly.
- **[CopilotKit version upgrades]** → CopilotKit's internal message format may change between versions, breaking the `page.route()` mock. Mitigation: pin CopilotKit version in `package.json` (already done); update mock when upgrading.
- **[Chat test ordering]** → Chat tests that place orders via tool calls need a pre-existing account. Mitigation: create account in test setup, same as other e2e tests.
