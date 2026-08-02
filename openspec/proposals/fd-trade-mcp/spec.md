# fd-trade-mcp Capability Specification

## Overview

This capability defines an MCP server that exposes paper-trading operations to Claude as tools. The server uses FastMCP and communicates with your existing FastAPI backend over HTTP.

## Tools

### place_order

Place a new paper trading order for an account.

**Inputs:**
- `account_id` (string): UUID of the target account
- `symbol` (string): Trading symbol (e.g., "AAPL", "ETHUSDT-PERP")
- `side` (string): "BUY" or "SELL"
- `order_type` (string): "MARKET" or "LIMIT"
- `quantity` (decimal): Number of units to trade
- `limit_price` (decimal, optional): Required if order_type is "LIMIT"

**Returns:** Order details including status, fills, and filled quantity.

**Errors:**
- `404` Account not found
- `unknown_instrument` Symbol has no instrument
- `no_quote` No live quote available
- `insufficient_buying_power` Buy order exceeds cash balance
- Validation errors for invalid parameters

**Example:**
```python
place_order(
    account_id="550e8400-e29b-41d4-a716-446655440000",
    symbol="AAPL",
    side="BUY",
    order_type="MARKET",
    quantity=10
)
# Returns: {"id": "...", "status": "FILLED", "fills": [...], ...}
```

### cancel_order

Cancel a pending or partially-filled order.

**Inputs:**
- `order_id` (string): UUID of the order to cancel

**Returns:** Updated order with status "CANCELLED".

**Errors:**
- `404` Order not found
- `not_cancellable` Order in terminal state (FILLED, REJECTED)

**Example:**
```python
cancel_order(order_id="550e8400-e29b-41d4-a716-446655440000")
# Returns: {"id": "...", "status": "CANCELLED", ...}
```

### list_orders

List orders for an account, optionally filtered by status.

**Inputs:**
- `account_id` (string, required): UUID of the account
- `status` (string, optional): Filter by order status ("PENDING", "FILLED", etc.)

**Returns:** List of orders with fills included.

**Example:**
```python
list_orders(account_id="550e8400-e29b-41d4-a716-446655440000")
# Returns: [{"id": "...", "symbol": "AAPL", "status": "FILLED", ...}, ...]
```

### list_accounts

List all accounts for the authenticated user.

**Inputs:** None

**Returns:** List of accounts with balances.

**Example:**
```python
list_accounts()
# Returns: [
#   {"id": "...", "name": "Primary", "cash_balance": 10000.0, ...},
#   ...
# ]
```

### get_account

Get detailed information about a specific account.

**Inputs:**
- `account_id` (string): UUID of the account

**Returns:** Account with current cash balance and metadata.

**Example:**
```python
get_account(account_id="550e8400-e29b-41d4-a716-446655440000")
# Returns: {"id": "...", "cash_balance": 10000.0, "created_at": "..."}
```

## Error Schema

All tool failures return structured errors when caught:

```json
{
    "error": true,
    "code": "insufficient_buying_power",
    "message": "Not enough cash for this order"
}
```

Untreated exceptions surface as raw error messages to Claude.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TRADE_API_URL` | No | `http://localhost:8000` | Backend API base URL |
| `TRADE_API_TOKEN` | Yes | - | JWT auth token for API access |

## Constraints & Assumptions

1. **Account isolation**: All requests validated against authenticated user
2. **Immediate fills**: Market orders fill synchronously; LIMIT orders may rest
3. **No async streaming**: Orders queryable immediately after placement
4. **Paper trading only**: No real capital at risk, buying power checked but no margin
5. **Nautilus-backed fills**: Pricing follows Nautilus matching engine logic

## Future Extensions

- `search_symbols(query)`: Search Nautilus instrument registry
- `get_positions(account_id)`: Current holdings per instrument
- `get_portfolio()`: Combined view across all accounts
- `watch_order(order_id)`: WebSocket subscription for order updates

## Testing Checklist

- [ ] Server starts with `fastmcp run src/fd_trade/mcp/server.py`
- [ ] Tool discovery works via `fastmcp inspect`
- [ ] Each tool callable via `fastmcp call`
- [ ] Invalid input returns structured errors
- [ ] Missing token shows clear error message
- [ ] Network failure handled gracefully
