## Why

The web UI has a CopilotKit sidebar and a backend `/api/v1/chat` endpoint with duplicated tool logic (LiteLLM + function calling). Meanwhile, the MCP server (`fd-trade-mcp`) already defines all trading tools (`place_order`, `cancel_order`, `list_orders`, `list_accounts`, `get_account`). The CopilotKit frontend can't talk to the backend because the backend doesn't speak CopilotKit's streaming protocol. We need a thin CopilotKit runtime that bridges the frontend to the MCP server, eliminating the duplicated `chat.py` logic.

## What Changes

- **New**: `web/copilot-runtime/` — a small Node.js server using `@copilotkit/runtime` that spawns the MCP server via stdio and exposes its tools as CopilotKit actions
- **Modified**: `web/src/components/ChatProvider.tsx` — point `runtimeUrl` to the new runtime server
- **Removed**: `src/fd_trade/api/routes/chat.py` and its router registration — the MCP server owns the tools now
- **Removed**: LiteLLM/OpenAI config from `src/fd_trade/config.py` if no longer needed by anything else

## Capabilities

### New Capabilities
- `copilot-runtime`: Node.js server bridging CopilotKit frontend to MCP server via stdio

### Modified Capabilities
- `web-copilotkit-chat`: Runtime URL changes from `/api/v1/chat` to the new CopilotKit runtime server; tool execution moves from chat.py to MCP server

## Impact

- **Dependencies**: Add `@copilotkit/runtime`, `@modelcontextprotocol/sdk` (or equivalent MCP client) to `web/copilot-runtime/package.json`
- **Remove**: `litellm` dependency from `pyproject.toml` if only used by `chat.py`
- **Runtime**: The Node.js runtime must be started alongside the FastAPI backend during development
- **Auth**: MCP server uses a service token (`TRADE_API_TOKEN`) for all web chat users — acceptable for paper trading
- **API**: `/api/v1/chat` endpoint removed; frontend talks to the CopilotKit runtime directly
