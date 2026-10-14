"""Tests for fd-paper-trading MCP server."""

import os
from decimal import Decimal
from unittest.mock import Mock, patch

# Set env var before importing server module
os.environ["PAPER_TRADING_API_TOKEN"] = "test-token"


@patch("fd_paper_trading.mcp.server.requests.post")
def test_place_order_success(mock_post):
    """Test successful order placement."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "order-1", "status": "FILLED"}
    mock_response.raise_for_status = Mock()
    mock_post.return_value = mock_response

    from fd_paper_trading.mcp.server import place_order

    result = place_order(
        account_id="550e8400-e29b-41d4-a716-446655440000",
        symbol="AAPL",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("10"),
    )

    assert result["success"] is True
    assert result["order"]["status"] == "FILLED"


@patch("fd_paper_trading.mcp.server.requests.delete")
def test_cancel_order_success(mock_delete):
    """Test successful order cancellation."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "order-1", "status": "CANCELLED"}
    mock_response.raise_for_status = Mock()
    mock_delete.return_value = mock_response

    from fd_paper_trading.mcp.server import cancel_order

    result = cancel_order(order_id="order-1")

    assert result["success"] is True
    assert result["order"]["status"] == "CANCELLED"


@patch("fd_paper_trading.mcp.server.requests.get")
def test_list_accounts_success(mock_get):
    """Test successful account listing."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"id": "acc-1", "name": "Primary", "cash_balance": 10000.0}
    ]
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    from fd_paper_trading.mcp.server import list_accounts

    results = list_accounts()

    assert len(results) == 1
    assert results[0]["name"] == "Primary"


if __name__ == "__main__":
    test_place_order_success()
    test_cancel_order_success()
    test_list_accounts_success()
    print("All tests passed!")
