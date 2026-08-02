"""fd-trade MCP server: expose paper-trading operations as Claude tools."""

import os
from decimal import Decimal

import requests
from fastmcp import FastMCP

# ponytail: env var auth only - no prompts, just configure once in settings.json
TRADE_API_URL = os.getenv("TRADE_API_URL", "http://localhost:8000")
TRADE_API_TOKEN = os.getenv("TRADE_API_TOKEN")

if not TRADE_API_TOKEN:
    raise RuntimeError(
        "TRADE_API_TOKEN environment variable is required. "
        "Configure it in your Claude settings under mcpServers.fd-trade.env"
    )

mcp = FastMCP("fd-trade")


def _api_call(method: str, endpoint: str, json_data: dict | None = None, params: dict | None = None) -> tuple[bool, dict]:
    """Helper for REST API calls. Returns (success, result)."""
    headers = {
        "Authorization": f"Bearer {TRADE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{TRADE_API_URL}{endpoint}"
    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        elif method == "POST":
            resp = requests.post(url, headers=headers, json=json_data, timeout=30)
        elif method == "DELETE":
            resp = requests.delete(url, headers=headers, timeout=30)
        else:
            return False, {"error": True, "code": "invalid_method", "message": f"Unknown HTTP method: {method}"}

        resp.raise_for_status()
        return True, resp.json()
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else 500
        if status == 404:
            return False, {"error": True, "code": "not_found", "message": "Resource not found"}
        elif status == 400:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            return False, {"error": True, "code": "bad_request", "message": detail}
        else:
            return False, {"error": True, "code": "api_error", "message": str(e)}
    except requests.exceptions.RequestException as e:
        return False, {"error": True, "code": "connection_error", "message": str(e)}


@mcp.tool
def place_order(
    account_id: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: Decimal,
    limit_price: Decimal | None = None,
) -> dict:
    """Place a new paper trading order (MARKET or LIMIT).

    Args:
        account_id: UUID of the target account
        symbol: Trading symbol (e.g., "AAPL", "ETHUSDT-PERP")
        side: "BUY" or "SELL"
        order_type: "MARKET" or "LIMIT"
        quantity: Number of units to trade
        limit_price: Required if order_type is "LIMIT"

    Returns:
        Order details including status, fills, and filled quantity on success.
        Error dict on failure.
    """
    payload = {
        "account_id": account_id,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "quantity": float(quantity),
    }
    if limit_price:
        payload["limit_price"] = float(limit_price)

    success, data = _api_call("POST", "/api/v1/orders", json_data=payload)
    if success:
        return {"success": True, "order": data}
    return data


@mcp.tool
def cancel_order(order_id: str) -> dict:
    """Cancel a pending or partially-filled order.

    Args:
        order_id: UUID of the order to cancel

    Returns:
        Updated order with status "CANCELLED" on success.
        Error dict on failure.
    """
    success, data = _api_call("DELETE", f"/api/v1/orders/{order_id}")
    if success:
        return {"success": True, "order": data}
    return data


@mcp.tool
def list_orders(account_id: str, status: str | None = None) -> list[dict]:
    """List orders for an account, optionally filtered by status.

    Args:
        account_id: UUID of the account
        status: Optional filter (PENDING, FILLED, CANCELLED, etc.)

    Returns:
        List of orders with fills included. Empty list on error.
    """
    params = {"status": status} if status else None
    success, data = _api_call("GET", f"/api/v1/accounts/{account_id}/orders", params=params)
    if success:
        return data
    return []


@mcp.tool
def list_accounts() -> list[dict]:
    """List all accounts for the authenticated user.

    Returns:
        List of accounts with IDs, names, and current cash balances.
        Empty list on error.
    """
    success, data = _api_call("GET", "/api/v1/accounts")
    if success:
        return data
    return []


@mcp.tool
def get_account(account_id: str) -> dict:
    """Get detailed information about a specific account.

    Args:
        account_id: UUID of the account

    Returns:
        Account details including current cash balance.
        Error dict on failure.
    """
    success, data = _api_call("GET", f"/api/v1/accounts/{account_id}")
    if success:
        return data
    return data


if __name__ == "__main__":
    mcp.run()  # ponytail: minimal boilerplate - FastMCP handles everything
