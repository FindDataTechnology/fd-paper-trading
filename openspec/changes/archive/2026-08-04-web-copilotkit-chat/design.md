## Context

The web app currently provides a professional trading dashboard with watchlist, chart, order panel, and position management. Users interact through UI components (forms, buttons, tables). Adding a chat interface allows conversational interaction with the trading system, leveraging the existing MCP server that already exposes trading operations as tools.

CopilotKit is a React framework for building AI copilots. It provides:
- React components for chat UI (`<CopilotChat />`, `<CopilotSidebar />`)
- Integration with AI providers (OpenAI, Anthropic, etc.)
- Tool execution framework that can call backend functions
- Built-in message history and context management

The existing MCP server (`src/fd_trade/mcp/server.py`) already exposes the trading tools we need. We need to bridge CopilotKit's tool execution to our MCP server.

## Goals / Non-Goals

**Goals:**
- Add a chat interface accessible from all authenticated pages
- Integrate with existing MCP tools (place_order, cancel_order, list_accounts, get_account, list_orders)
- Maintain authentication and authorization (chat uses user's JWT)
- Provide real-time feedback during tool execution
- Handle errors gracefully with user-friendly messages

**Non-Goals:**
- Persist chat history across sessions (initial implementation is session-only)
- Support multiple AI providers (start with one, likely OpenAI or Anthropic)
- Build a custom AI model (use existing LLM APIs)
- Replace the existing UI (chat is complementary, not a replacement)

## Decisions

### 1. CopilotKit integration approach

**Decision:** Use CopilotKit's `<CopilotSidebar />` component as a floating sidebar that can be toggled from any page. This provides a non-intrusive chat experience without disrupting the existing dashboard layout.

**Rationale:** A sidebar is less disruptive than a modal or full-page chat. Users can keep the dashboard visible while chatting. CopilotKit's sidebar component handles positioning, toggling, and responsive behavior out of the box.

**Alternatives considered:**
- Floating chat bubble (like Intercom) — takes up screen space, harder to read long conversations
- Full-page chat — disrupts trading workflow, users would need to navigate back and forth
- Modal dialog — blocks the UI, not suitable for ongoing conversations

### 2. Backend architecture: Direct MCP vs. proxy endpoint

**Decision:** Create a new FastAPI endpoint `/api/v1/chat` that acts as a proxy between CopilotKit and the MCP server. CopilotKit sends user messages to this endpoint, which forwards them to the MCP server, executes tools, and returns responses.

**Rationale:** This keeps the MCP server unchanged (it's already working and tested) and provides a clean separation of concerns. The proxy endpoint handles authentication (extracts JWT from request), forwards to MCP, and returns responses in CopilotKit's expected format.

**Alternatives considered:**
- Direct MCP integration in the frontend — would require exposing MCP server to the browser, security risk
- Replace MCP with CopilotKit's native tool system — duplicates logic, harder to maintain
- Server-Sent Events (SSE) for streaming — adds complexity, not needed for initial implementation

### 3. AI provider selection

**Decision:** Start with OpenAI's GPT-4 (or GPT-3.5-turbo for cost savings). CopilotKit has built-in support for OpenAI, making integration straightforward.

**Rationale:** OpenAI is well-supported by CopilotKit, has good tool-calling capabilities, and is familiar to most developers. We can add support for other providers (Anthropic, etc.) later if needed.

**Alternatives considered:**
- Anthropic Claude — also good, but CopilotKit's OpenAI integration is more mature
- Local LLM (Ollama) — adds infrastructure complexity, not needed for initial implementation
- Multiple providers from day one — over-engineering, can add later

### 4. Authentication flow

**Decision:** The chat endpoint uses the same JWT authentication as the rest of the app. When the user sends a message, the frontend includes their JWT in the request headers. The backend extracts the JWT and passes it to the MCP server, which uses it to authorize tool invocations.

**Rationale:** Reusing existing auth keeps the implementation simple and secure. Users don't need to authenticate separately for chat. The MCP server already expects a JWT in the `TRADE_API_TOKEN` environment variable, so we just forward it.

**Alternatives considered:**
- Separate API key for chat — adds complexity, users would need to manage multiple tokens
- No auth for chat — security risk, anyone could execute trades

### 5. Tool execution context

**Decision:** The AI assistant automatically knows the user's active account (from the UI state) and uses it for tool invocations. If the user has multiple accounts, the assistant can ask which account to use or default to the active one.

**Rationale:** This reduces friction — users don't need to specify account IDs in every message. The frontend can pass the active account ID to the chat endpoint, which includes it in the tool invocation context.

**Alternatives considered:**
- Always require account ID in messages — tedious for users
- Let AI guess the account — error-prone, could execute trades on wrong account

## Risks / Trade-offs

- **[AI provider costs]** → GPT-4 is expensive. Mitigation: Start with GPT-3.5-turbo, upgrade to GPT-4 only if needed. Monitor usage and set rate limits.
- **[Tool execution latency]** → LLM inference + tool execution can be slow (5-10 seconds). Mitigation: Show clear loading indicators, stream responses if possible.
- **[Hallucination risk]** → AI might misinterpret user requests or execute wrong tools. Mitigation: Require confirmation for destructive operations (e.g., "Are you sure you want to cancel this order?"), validate tool parameters before execution.
- **[Security risk]** → Chat interface could be exploited to execute unauthorized trades. Mitigation: Strict authentication, validate all tool invocations, log all chat interactions for audit.
- **[CopilotKit limitations]** → CopilotKit is a relatively new framework, might have bugs or limitations. Mitigation: Keep the integration simple, be prepared to switch to a different framework if needed.
