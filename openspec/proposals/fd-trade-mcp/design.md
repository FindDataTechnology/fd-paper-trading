# fd-paper-trading-mcp Design Document

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Claude Code                              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Tools Available:                                         │   │
│  │ • place_order(account_id, symbol, side, order_type, ...) │   │
│  │ • cancel_order(order_id)                                 │   │
│  │ • list_orders(account_id, status=None)                  │   │
│  │ • list_accounts()                                        │   │
│  │ • get_account(account_id)                                │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────┬───────────────────────────────────────────┘
                      │ MCP stdio (JSON-RPC)
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                      fd-paper-trading-mcp                               │
│                    FastMCP server                               │
│                                                                 │
│  @mcp.tool                                                      │
│  def place_order(...):                                         │
│      """Place a new paper trading order."""                    │
│      return api.place_order(...)                               │
│                                                                 │
│  @mcp.tool                                                      │
│  def cancel_order(order_id: str): ...                          │
│                                                                 │
│  ...                                                            │
│                                                                 │
│  env: PAPER_TRADING_API_URL, PAPER_TRADING_API_TOKEN                           │
└─────────────────────┬───────────────────────────────────────────┘
                      │ HTTP + Bearer Token
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                            │
│  /api/v1/accounts                                               │
│  /api/v1/orders                                                 │
│  │                                                              │
│  └─▶ PaperExecutionService                                     │
│      └─▶ NautilusFillEngine (stateless matching)               │
└─────────────────────┬───────────────────────────────────────────┘
                      │ SQLAlchemy 2.0 Async
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                      PostgreSQL                                 │
│  accounts | orders | fills | positions | balances              │
└─────────────────────────────────────────────────────────────────┘
```

## Module Structure

**Decision:** Single-file MVP (ponytail: no boilerplate scaffolding for `client.py`)

```
src/fd_paper_trading/mcp/
└── server.py            # All-in-one: FastMCP tools + HTTP client in one file
```

## Key Implementation Decisions

### 1. Lazy: Single File, Sync HTTP Client

**Decision:** One file (`server.py`), no `client.py`, sync `requests` instead of async.

**Rationale:** 
- Reuses all business logic in `PaperExecutionService` via REST
- FastMCP requires sync tools (no `async` def allowed)
- Helper function `_api_call()` handles all error parsing centrally
- Less files = less mental overhead for user

**Trade-off:** Slightly more code per tool, but under 200 lines total.

### 2. Type Safety via Pydantic

**Decision:** Let FastMCP infer schemas from function signatures.

```python
@mcp.tool
def place_order(
    account_id: str,        # auto-validated as string
    quantity: Decimal,      # auto-validated as decimal
    limit_price: Decimal | None = None,  # optional
) -> dict:
```

**Benefit:** No separate schema registration; errors surface on type mismatch.

### 3. Error Handling Strategy

**Decision:** Two modes:
- **Handled exceptions**: Return structured error dict
- **Unhandled exceptions**: Raise → FastMCP surfaces as tool failure

**Example handled:**
```python
try:
    response = self._client.post("/orders", json=payload)
    response.raise_for_status()
except requests.HTTPError as e:
    if e.response.status_code == 404:
        return {"error": True, "code": "not_found", "message": "Account not found"}
```

### 4. Account Discovery Pattern

**Decision:** Always include `list_accounts()` so Claude can discover IDs dynamically.

**Why:** Users typically don't memorize UUIDs. Workflow becomes:
```
Claude: "Let me check your accounts first..."
       → list_accounts() → shows ID
       → place_order(account_id="...", symbol="AAPL", ...)
```

### 5. snake_case Tool Names

**Decision:** Follow FastMCP conventions, not REST naming.

| REST Endpoint         | MCP Tool Name     |
|-----------------------|-------------------|
| POST /orders          | place_order       |
| DELETE /orders/:id    | cancel_order      |
| GET /accounts/:id     | get_account       |

**Reasoning:** FastMCP host tools expect snake_case; camelCase causes runtime errors.

### 6. No Auth Prompts

**Decision:** Environment variables only.

```python
import os
from pydantic import Field

PAPER_TRADING_API_URL = os.getenv("PAPER_TRADING_API_URL", "http://localhost:8000")
PAPER_TRADING_API_TOKEN = os.getenv("PAPER_TRADING_API_TOKEN")  # required, no default

if not PAPER_TRADING_API_TOKEN:
    raise ValueError("PAPER_TRADING_API_TOKEN environment variable is required")
```

**Why prompts are bad:**
- Slow (user interaction each time)
- Fragile (what if token expires?)
- Harder to debug (token hidden in prompt history)

### 7. Order Status Lifecycle

**Assumption:** Orders transition synchronously during `place_order`:
- Market order: `PENDING` → `FILLED` (or `REJECTED`)
- Limit order: `PENDING` → `FILLED` OR `OPEN` (resting)

**Caveat:** This depends on how your fill engine works. If LIMIT orders stay PENDING longer, `cancel_order` must handle racing state.

Current backend code (`orders.py` line 65):
```python
await _broadcast_order(request, user.id, order)  # includes fills
```

So after `place_order` returns, fills are already embedded. Safe to assume immediate resolution for v1.

## Data Flow: Place Order Example

```
1. Claude calls: place_order(account_id="...", symbol="AAPL", ...)
   ↓
2. server.py validates types → constructs payload dict
   ↓
3. client.py POSTs JSON to /api/v1/orders + Bearer token
   ↓
4. FastAPI auth middleware validates JWT → extracts user_id
   ↓
5. orders.py route places order via exec_service.place_order()
   ↓
6. PaperExecutionService:
   - Looks up instrument
   - Fetches live quote
   - Checks buying power
   - Runs Nautilus fill engine
   - Applies fills to DB
   ↓
7. Response serialized → OrderOut schema → JSON
   ↓
8. client.py parses response → returns to server.py
   ↓
9. server.py wraps in tool result → MCP stdio transport
   ↓
10. Claude receives: {"id": "...", "status": "FILLED", "fills": [...]}
```

## Security Considerations

| Concern | Mitigation |
|---------|------------|
| Token leakage | Store only in settings.json, never logged |
| CSRF | Not applicable (stdio transport, no cookies) |
| XXE/XSS | Pydantic/requests sanitize; backend escapes JSON |
| Privilege escalation | JWT validation enforces user/account boundary |
| Replay attacks | JWT short expiry (configurable) |

## Performance Profile

| Operation | Latency | Notes |
|-----------|---------|-------|
| Tool call overhead | <1ms | In-memory |
| API roundtrip | 5-50ms | Depends on backend load |
| Fill computation | 10-20ms | Nautilus threadpool |
| Total (place_order) | ~25-70ms | Well under Claude timeout |

## Testing Strategy

### Manual Testing
```bash
# Start MCP server in dev mode
fastmcp dev src/fd_paper_trading/mcp/server.py

# List tools
fastmcp inspect http://localhost:8000/mcp

# Call a tool
fastmcp call http://localhost:8000/mcp list_accounts
```

### Integration Tests
```python
# tests/unit/mcp/test_server.py
async def test_place_order_success(mocker):
    mocker.patch("fd_paper_trading.mcp.client.ApiClient.post", return_value={"id": "x", "status": "FILLED"})
    result = await place_order("uuid", "AAPL", "BUY", "MARKET", Decimal("1"))
    assert result["status"] == "FILLED"
```

## Rollout Plan

### Phase 1: MVP (This Change)
- ✅ Proposal & spec approved
- [ ] Implement server.py + client.py
- [ ] Test locally with `fastmcp run`
- [ ] Update CLAUDE.md with config snippet

### Phase 2: Extensions (Future)
- [ ] `search_symbols(query)` - instrument discovery
- [ ] `get_positions(account_id)` - current holdings
- [ ] Webhook support for real-time updates

### Phase 3: Production Hardening
- [ ] Token rotation logic
- [ ] Rate limiting awareness
- [ ] Observability (structured logging)

## Risks & Unknowns

| Risk | Impact | Mitigation |
|------|--------|------------|
| Backend API changes | High | Wrap API calls in client.py layer |
| Token security | Medium | Document secure storage in README |
| Nautilus version drift | Low | Pin nautilus_trader version |
| Network failures | Medium | Retries + clear error messages |

## Appendix: FastMCP Patterns

### Basic Tool Pattern
```python
from fastmcp import FastMCP

mcp = FastMCP("fd-paper-trading")

@mcp.tool
def my_tool(arg1: str, arg2: int = 10) -> str:
    """Description shown in tool docs."""
    return f"{arg1}: {arg2}"
```

### Structured Output
```python
from typing import TypedDict

class OrderInfo(TypedDict):
    id: str
    status: str
    fills: list[dict]

@mcp.tool
def get_order_details(order_id: str) -> OrderInfo:
    ...
```

### Error Handling Pattern
```python
import requests
from typing import Any

def safe_call(endpoint: str, data: dict) -> dict:
    try:
        resp = self._client.post(endpoint, json=data)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        return {"error": True, "message": str(e)}
```
