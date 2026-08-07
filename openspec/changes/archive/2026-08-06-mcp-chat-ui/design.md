## Architecture

```
┌──────────────┐   SSE streaming     ┌──────────────────────┐
│ CopilotKit   │ ◄────────────────── │ web/copilot-runtime/ │
│ Sidebar      │ ──── POST ──────── │                      │
│ (React)      │                     │  @copilotkit/runtime │
└──────────────┘                     │  + MCP client (stdio)│
                                     │                      │
                                     │  spawns:             │
                                     │  uv run src.fd_trade │
                                     │  .mcp.server         │
                                     └──────────┬───────────┘
                                                │ MCP stdio
                                     ┌──────────▼───────────┐
                                     │ MCP Server (FastMCP)  │
                                     │                       │
                                     │ TRADE_API_TOKEN=      │
                                     │  <service token>      │
                                     │ TRADE_API_URL=        │
                                     │  http://localhost:8000│
                                     └──────────┬───────────┘
                                                │ REST
                                     ┌──────────▼───────────┐
                                     │ FastAPI (existing)    │
                                     └──────────────────────┘
```

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Runtime location | `web/copilot-runtime/` | Colocated with frontend, separate from Python backend |
| Auth | Service token (env var) | Paper trading; all web chat users share one identity |
| MCP transport | stdio (spawned by runtime) | No extra HTTP server for MCP; runtime manages lifecycle |
| MCP HTTP mode | Separate entry point (`http_server.py`) | Keeps `server.py` clean for Claude Code stdio usage |
| `chat.py` | Deleted | MCP server owns the tools; eliminates duplication |
| litellm | Removed from deps | Only used by deleted `chat.py` |
| Vite proxy | `/copilotkit` → runtime port | Avoids conflict with existing `/api` → FastAPI proxy |

## Files changed

### New
- `web/copilot-runtime/package.json` — Node.js deps (`@copilotkit/runtime`)
- `web/copilot-runtime/server.js` — CopilotKit runtime server (~40 lines)
- `src/fd_trade/mcp/http_server.py` — MCP server HTTP/SSE entry point (~10 lines)

### Modified
- `web/vite.config.ts` — added `/copilotkit` proxy rule
- `web/src/components/ChatProvider.tsx` — `runtimeUrl` → `/copilotkit`
- `src/fd_trade/api/app.py` — removed chat router import/registration
- `src/fd_trade/config.py` — removed `openai_api_key`, `openai_api_base`, `openai_model`
- `pyproject.toml` — removed `litellm` dependency
- `tests/conftest.py` — removed chat test fixtures and unused imports

### Deleted
- `src/fd_trade/api/routes/chat.py`
- `tests/e2e/test_chat_e2e.py`
- `tests/e2e/test_chat_integration.py`
- `web/e2e/chat.spec.ts`

## How to run

```bash
# Terminal 1: FastAPI backend
uv run uvicorn fd_trade.api.app:create_app --factory --reload

# Terminal 2: MCP server (HTTP/SSE mode)
uv run python -m fd_trade.mcp.http_server

# Terminal 3: CopilotKit runtime
cd web/copilot-runtime && npm install && node server.js

# Terminal 4: Frontend dev server
cd web && npm run dev
```

The frontend at `http://localhost:5173` proxies:
- `/api/*` → FastAPI (port 8000)
- `/copilotkit/*` → CopilotKit runtime (port 4100)

The CopilotKit runtime spawns the MCP server (stdio) which calls FastAPI REST with a service token.
