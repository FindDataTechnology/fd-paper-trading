# fd-paper-trading-mcp: MCP Server for Paper Trading

## Change Summary

Add a FastMCP-based server that exposes paper-trading operations (place order, cancel order, list orders, discover accounts) to Claude via Model Context Protocol.

## Motivation

Claude needs to interact with your trading system. Currently:
- API requires HTTP requests + JWT tokens
- No natural interface for tool use
- Account discovery is manual

This change gives Claude first-class tool access to place/cancel/query orders and discover accounts.

## Scope

### In Scope

- FastMCP server (`src/fd_paper_trading/mcp/server.py`)
- 5 tools: `place_order`, `cancel_order`, `list_orders`, `list_accounts`, `get_account`
- Environment variable auth (`PAPER_TRADING_API_URL`, `PAPER_TRADING_API_TOKEN`)
- snake_case tool naming per FastMCP conventions
- Structured error responses on failure

### Out of Scope

- WebSocket real-time updates (orders are queryable after placement)
- Market data / instrument search (add later if needed)
- Position queries (can derive from account endpoint)
- Configuration UI or interactive prompts

## Design Decisions

### Architecture

```
┌──────────────┐      HTTP          ┌──────────────┐      Postgres
│   Claude     │◀───────▶│      │   FastAPI      │
│              │      Tool        │   Backend    │
│   Tools:     │      Calls       │              │
│   • place    │                  │   App State  │
│   • cancel   │◀───────▶│      │              │
│   • list_*   │      HTTP Resp │              │
└──────────────┘                  └──────────────┘
         ▲                                │
         │ MCP Stdio                     │ SQL Alchemy
         │
┌──────────────┐
│ fd-paper-trading-mcp │
│ FastMCP srv  │
│ env vars:    │
│ • URL        │
│ • TOKEN      │
└──────────────┘
```

### Implementation Approach

**Lazy Version:** HTTP client wrapper around existing REST endpoints
- Reuses all business logic in `PaperExecutionService`
- No database coupling
- Same validation/error handling as web/API
- Simple: one file, minimal dependencies

### API Endpoints Used

| Tool | Endpoint | Method |
|------|----------|--------|
| `list_accounts` | `/api/v1/accounts` | GET |
| `get_account` | `/api/v1/accounts/{account_id}` | GET |
| `place_order` | `/api/v1/orders` | POST |
| `cancel_order` | `/api/v1/orders/{order_id}` | DELETE |
| `list_orders` | `/api/v1/accounts/{account_id}/orders` | GET |

### Error Handling

Exceptions from API calls return structured errors:

```python
{
    "error": True,
    "code": "insufficient_buying_power",
    "message": "Not enough cash for this order"
}
```

Raised exceptions surface as tool failure messages to Claude.

### Auth Strategy

Environment variables only - no prompts:
- `PAPER_TRADING_API_URL` (default: `http://localhost:8000`)
- `PAPER_TRADING_API_TOKEN` (required - stored securely in Claude settings)

### Naming Conventions

- **Tools**: snake_case (`place_order`, not `placeOrder`) per FastMCP conventions
- **Docs**: Natural language descriptions Claude can reason over
- **Error codes**: Match existing REST API codes (e.g., `unknown_instrument`)

## Tasks

1. Create server module structure (`src/fd_paper_trading/mcp/server.py`)
2. Implement API client helper class
3. Register 5 tools with proper type hints and docstrings
4. Add environment variable defaults
5. Test with `fastmcp run` and `fastmcp dev`

## Rollout Plan

**Phase 1 (MVP):** Core trading tools only
- `place_order`, `cancel_order`, `list_orders`, `list_accounts`

**Phase 2 (Optional extensions):**
- `search_symbols`: Search Nautilus instrument registry
- `get_positions`: Current holdings per account
- `watch_order`: Subscribe to order status updates via websocket

**Phase 3 (If needed):**
- Auth token rotation / refresh
- Multiple account profiles
- Rate limiting awareness

## Success Criteria

- [x] Proposal approved
- [ ] Code implemented
- [ ] Can call `fastmcp dev src/fd_paper_trading/mcp/server.py` successfully
- [ ] All 5 tools callable via `fastmcp call`
- [ ] Errors surfaced clearly to user
- [ ] Documentation updated (CLAUDE.md or README)

## References

- FastMCP docs: https://github.com/ModelContextPython/fastmcp
- Your API: `src/fd_paper_trading/api/routes/orders.py`
- Existing patterns: `ponytail-mcp/index.js` (same tooling, different language)
