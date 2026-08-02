# fd-trade-mcp: MCP Server for Paper Trading

## Quick Start

```bash
# Install FastMCP
uv add fastmcp

# Run server locally
fastmcp run src/fd_trade/mcp/server.py

# Or test with CLI
fastmcp inspect http://localhost:8000/mcp  # List tools
fastmcp call http://localhost:8000/mcp list_accounts
```

## Configuration

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "fd-trade": {
      "command": "uv",
      "args": ["run", "fd_trade.mcp.server"],
      "env": {
        "TRADE_API_URL": "http://localhost:8000",
        "TRADE_API_TOKEN": "your-jwt-token-here"
      }
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `place_order` | Place a new paper trading order |
| `cancel_order` | Cancel a pending order |
| `list_orders` | List orders for an account |
| `list_accounts` | Discover all accounts |
| `get_account` | Get account details + balance |

## Development

```bash
# Dev mode (auto-reloads)
fastmcp dev src/fd_trade/mcp/server.py

# Test calls
fastmcp call src/fd_trade/mcp/server.py list_accounts

# Full inspection
fastmcp inspect src/fd_trade/mcp/server.py
```

## Architecture

See [design.md](./design.md) for full architecture diagram.

## Testing

```bash
# Manual testing
export TRADE_API_TOKEN="test-token"
uv run python -m src.fd_trade.mcp.server

# Unit tests
pytest tests/unit/mcp/ -v
```

## Files

- `server.py` - Main FastMCP server with tool definitions
- `client.py` - HTTP client wrapper around REST API
- `proposal.md` - Change proposal
- `spec.md` - Capability specification
- `design.md` - Technical design document

## Related

- [FastMCP Docs](https://github.com/ModelContextPython/fastmcp)
- [Your REST API](../api/)
- [OpenSpec Overview](../../README.md)
