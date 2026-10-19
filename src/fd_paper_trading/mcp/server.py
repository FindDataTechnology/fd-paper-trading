"""fd-paper-trading MCP server: expose paper-trading operations as tools.

Caller identity is delegated: each tool forwards the inbound request's
``Authorization`` credential to the REST API, so on a shared deployment every
caller acts as their own user. When a request carries no credential — the
stdio case — the operator-configured ``PAPER_TRADING_API_TOKEN`` is used.
"""

import os
from decimal import Decimal

import requests
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from .annotations import annotations_for

PAPER_TRADING_API_URL = os.getenv("PAPER_TRADING_API_URL", "http://localhost:8000")
# Fallback for headerless transports (stdio). HTTP callers must present their
# own credential; it is never substituted by this shared token.
PAPER_TRADING_API_TOKEN = os.getenv("PAPER_TRADING_API_TOKEN")

mcp = FastMCP("fd-paper-trading")


def _inbound_bearer() -> str | None:
    """The caller's own credential from the raw HTTP request, if any.

    fastmcp's ``get_http_headers()`` strips Authorization, so read the raw
    ASGI request instead (same trap as facet-ops-mcp's tools_market). Raises
    when there is no request context (stdio) — that just means no header.
    """
    try:
        request = get_http_request()
    except RuntimeError:
        return None
    if request is None:
        return None
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        return None
    return auth


def _resolve_credential() -> str | None:
    """Inbound credential wins; the configured token covers headerless stdio."""
    return _inbound_bearer() or PAPER_TRADING_API_TOKEN


def _api_call(
    method: str,
    endpoint: str,
    json_data: dict | None = None,
    params: dict | None = None,
    authorization: str | None = None,
) -> tuple[bool, dict]:
    """Helper for REST API calls. Returns (success, result).

    Authorization failures — no usable credential at all, or a credential the
    API rejects with 401 — are raised as ``ToolError`` so they reach the caller
    as a *failed* tool result rather than an ordinary one: a consuming platform
    retires a stored credential only on an error result whose text carries the
    rejection, and a caller must never mistake "credential refused" for "you
    have no data" (list-shaped tools would otherwise return an empty list).
    Every other failure stays a structured result so the model can self-repair
    (validation fields, unknown symbol, …).
    """
    bearer = authorization if authorization is not None else _resolve_credential()
    if not bearer:
        raise ToolError(
            "missing_credential: no credential — present an Authorization header "
            "(HTTP transport) or configure PAPER_TRADING_API_TOKEN (stdio transport)"
        )
    headers = {
        "Authorization": bearer if bearer.startswith("Bearer ") else f"Bearer {bearer}",
        "Content-Type": "application/json",
    }
    url = f"{PAPER_TRADING_API_URL}{endpoint}"
    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        elif method == "POST":
            resp = requests.post(url, headers=headers, json=json_data, timeout=30)
        elif method == "PATCH":
            resp = requests.patch(url, headers=headers, json=json_data, timeout=30)
        elif method == "DELETE":
            resp = requests.delete(url, headers=headers, timeout=30)
        else:
            return False, {"error": True, "code": "invalid_method", "message": f"Unknown HTTP method: {method}"}

        resp.raise_for_status()
        return True, resp.json() if resp.content else {}
    except requests.exceptions.HTTPError as e:
        # `if e.response` is WRONG here: requests' Response.__bool__ is
        # self.ok — an error-status response (401) is FALSY, so the truthiness
        # test would silently take the `else 500` branch and the credential
        # rejection would fall through to the structured-body path as an
        # ordinary result. Identity check, always.
        status = e.response.status_code if e.response is not None else 500
        if status == 401:
            # Deliberately an error result (isError=true on the wire) whose text
            # carries the 401 wording: the platform's stale-flip keys on both.
            # Checked BEFORE the structured-body path — the API's 401 body is
            # itself structured, and as an ordinary result it would be
            # indistinguishable from success (list tools would read "empty").
            raise ToolError(
                "401 unauthorized: the paper-trading credential was rejected "
                "(revoked or invalid). Mint a fresh token on the service web and "
                "reconnect it."
            ) from e
        # Surface the standardized {"error": {code, message, fields}} body so
        # the LLM can self-repair (e.g. spec validation fields on 422).
        if e.response is not None:
            try:
                body = e.response.json().get("error")
            except Exception:
                body = None
            if isinstance(body, dict):
                return False, {"error": True, **body}
        if status == 404:
            return False, {"error": True, "code": "not_found", "message": "Resource not found"}
        return False, {"error": True, "code": "api_error", "message": str(e)}
    except requests.exceptions.RequestException as e:
        return False, {"error": True, "code": "connection_error", "message": str(e)}


@mcp.tool(annotations=annotations_for("place_order"))
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


@mcp.tool(annotations=annotations_for("cancel_order"))
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


@mcp.tool(annotations=annotations_for("list_orders"))
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


@mcp.tool(annotations=annotations_for("list_accounts"))
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


@mcp.tool(annotations=annotations_for("get_account"))
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


@mcp.tool(annotations=annotations_for("create_account"))
def create_account(name: str, starting_balance: Decimal | None = None) -> dict:
    """Open a new paper account for the authenticated user.

    Use this when the user has no account yet (check with list_accounts first).
    Accounts start with the deployment's default starting balance when
    starting_balance is omitted.

    Args:
        name: Display name for the account (e.g. "main", "strategy-lab")
        starting_balance: Optional initial virtual cash; defaults to the
            deployment default

    Returns:
        The created account with its ID and starting balance.
        Error dict on failure.
    """
    payload: dict = {"name": name}
    if starting_balance is not None:
        payload["starting_balance"] = float(starting_balance)
    success, data = _api_call("POST", "/api/v1/accounts", json_data=payload)
    if success:
        return data
    return data


@mcp.tool(annotations=annotations_for("list_positions"))
def list_positions(account_id: str) -> list[dict]:
    """List open positions for an account with quantity, average cost, and P&L.

    Args:
        account_id: UUID of the account

    Returns:
        List of positions (symbol, quantity, average price, realized and
        unrealized P&L). Empty list on error.
    """
    success, data = _api_call("GET", f"/api/v1/accounts/{account_id}/positions")
    if success:
        return data
    return []


@mcp.tool(annotations=annotations_for("search_instruments"))
def search_instruments(query: str) -> list[dict]:
    """Search the tradable instrument universe by symbol or company name.

    Args:
        query: Case-insensitive symbol or name fragment (e.g. "apple", "AAPL")

    Returns:
        Matching instruments with symbol, name, and venue. Empty list when
        nothing matches.
    """
    success, data = _api_call(
        "GET", "/api/v1/instruments/search", params={"q": query}
    )
    if success:
        return data
    return []


@mcp.tool(annotations=annotations_for("get_bars"))
def get_bars(symbol: str, timeframe: str = "1d", limit: int = 200) -> list[dict]:
    """Get historical OHLCV bars for a symbol, oldest-first.

    Args:
        symbol: Trading symbol (e.g. "AAPL")
        timeframe: One of 1m, 5m, 15m, 1h, 1d (provider may support a subset)
        limit: Maximum number of bars (capped by the server)

    Returns:
        List of bars with ts, open, high, low, close, volume. Empty list on
        error or data gap.
    """
    success, data = _api_call(
        "GET",
        f"/api/v1/instruments/{symbol}/bars",
        params={"timeframe": timeframe, "limit": limit},
    )
    if success:
        return data
    return []


# ---------------------------------------------------------------------------
# Strategy tools (template + code tracks, backtests, live paper runs)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=annotations_for("list_strategy_templates"))
def list_strategy_templates() -> list[dict]:
    """List the built-in strategy templates with their parameter JSON Schemas.

    Use these to build a template-track strategy spec:
    {"kind": "template", "template": <id>, "symbol": "AAPL", "params": {...}}.

    Returns:
        List of {id, name, description, params_schema}. Empty list on error.
    """
    success, data = _api_call("GET", "/api/v1/strategy-templates")
    if success:
        return data
    return []


@mcp.tool(annotations=annotations_for("create_strategy"))
def create_strategy(name: str, spec: dict, description: str | None = None) -> dict:
    """Create a strategy from a spec.

    Args:
        name: Human-readable strategy name
        spec: Strategy spec. Template track:
            {"kind": "template", "template": "sma_cross", "symbol": "AAPL",
             "params": {"fast": 10, "slow": 30, "quantity": 5}}
            Code track (if enabled for your group):
            {"kind": "code", "symbol": "AAPL",
             "source": "def on_bar(ctx):\\n    ..."}
        description: Optional description

    Returns:
        The created strategy (with id and version) on success. On validation
        failure, an error dict whose "fields" list every spec violation —
        fix them and retry.
    """
    payload: dict = {"name": name, "spec": spec}
    if description is not None:
        payload["description"] = description
    success, data = _api_call("POST", "/api/v1/strategies", json_data=payload)
    if success:
        return {"success": True, "strategy": data}
    return data


@mcp.tool(annotations=annotations_for("update_strategy"))
def update_strategy(
    strategy_id: str,
    name: str | None = None,
    description: str | None = None,
    spec: dict | None = None,
) -> dict:
    """Update a strategy. Changing the spec bumps its version; name/description
    edits do not.

    Args:
        strategy_id: UUID of the strategy
        name: New name (optional)
        description: New description (optional)
        spec: New spec (optional; same shape as create_strategy)

    Returns:
        The updated strategy on success; error dict with "fields" on invalid spec.
    """
    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if spec is not None:
        payload["spec"] = spec
    success, data = _api_call("PATCH", f"/api/v1/strategies/{strategy_id}", json_data=payload)
    if success:
        return {"success": True, "strategy": data}
    return data


@mcp.tool(annotations=annotations_for("list_strategies"))
def list_strategies() -> list[dict]:
    """List all strategies for the authenticated user.

    Returns:
        List of strategies with id, name, spec, spec_summary, and version.
        Empty list on error.
    """
    success, data = _api_call("GET", "/api/v1/strategies")
    if success:
        return data
    return []


@mcp.tool(annotations=annotations_for("get_strategy"))
def get_strategy(strategy_id: str) -> dict:
    """Get one strategy by id.

    Args:
        strategy_id: UUID of the strategy

    Returns:
        Strategy details (spec, version, spec_summary). Error dict on failure.
    """
    success, data = _api_call("GET", f"/api/v1/strategies/{strategy_id}")
    return data


@mcp.tool(annotations=annotations_for("run_backtest"))
def run_backtest(
    strategy_id: str,
    start: str,
    end: str,
    initial_cash: Decimal = Decimal("100000"),
) -> dict:
    """Start a backtest of a strategy over a daily-bar window.

    The backtest runs in the background; poll get_strategy_run(run_id) until
    status is COMPLETED (stats + equity curve) or FAILED (error message).

    Args:
        strategy_id: UUID of the strategy
        start: Window start date, ISO format (e.g. "2025-01-01")
        end: Window end date, ISO format
        initial_cash: Starting cash for the simulated portfolio

    Returns:
        The run (status QUEUED, pinned strategy_version) on success;
        error dict on failure (e.g. window over the group limit).
    """
    payload = {"start": start, "end": end, "initial_cash": float(initial_cash)}
    success, data = _api_call("POST", f"/api/v1/strategies/{strategy_id}/backtests", json_data=payload)
    if success:
        return {"success": True, "run": data,
                "hint": "Poll get_strategy_run until status is COMPLETED or FAILED."}
    return data


@mcp.tool(annotations=annotations_for("get_strategy_run"))
def get_strategy_run(run_id: str) -> dict:
    """Get a strategy run's status, stats, and equity curve.

    Args:
        run_id: UUID of the run

    Returns:
        Run details: status, stats (total_return, max_drawdown, sharpe,
        win_rate, trade_count, final_equity), error (if FAILED), and
        equity_points. Error dict on failure.
    """
    success, data = _api_call("GET", f"/api/v1/strategy-runs/{run_id}")
    return data


@mcp.tool(annotations=annotations_for("list_strategy_runs"))
def list_strategy_runs(
    strategy_id: str | None = None,
    mode: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """List strategy runs (execution history), optionally filtered.

    Args:
        strategy_id: Only runs of this strategy UUID
        mode: "BACKTEST" or "PAPER"
        status: QUEUED, RUNNING, COMPLETED, FAILED, STARTING, STOPPED, ERROR

    Returns:
        List of runs, newest first. Empty list on error.
    """
    params = {k: v for k, v in {
        "strategy_id": strategy_id, "mode": mode, "status": status
    }.items() if v is not None}
    success, data = _api_call("GET", "/api/v1/strategy-runs", params=params or None)
    if success:
        return data
    return []


@mcp.tool(annotations=annotations_for("start_strategy"))
def start_strategy(strategy_id: str, account_id: str) -> dict:
    """Start a live paper run of a strategy against a paper account.

    The strategy's signals are evaluated on each new daily bar and turned
    into orders on the account. Poll get_strategy_run for status
    (STARTING -> RUNNING; ERROR on failure).

    Args:
        strategy_id: UUID of the strategy
        account_id: UUID of the paper account to trade

    Returns:
        The run on success; error dict on failure (e.g. group active-strategy limit).
    """
    payload = {"account_id": account_id}
    success, data = _api_call("POST", f"/api/v1/strategies/{strategy_id}/runs", json_data=payload)
    if success:
        return {"success": True, "run": data}
    return data


@mcp.tool(annotations=annotations_for("stop_strategy"))
def stop_strategy(run_id: str) -> dict:
    """Stop a live paper run. Cancels the run's resting LIMIT orders but never
    closes its positions.

    Args:
        run_id: UUID of the run to stop

    Returns:
        The updated run (status STOPPED) on success; error dict on failure.
    """
    success, data = _api_call("POST", f"/api/v1/strategy-runs/{run_id}/stop")
    if success:
        return {"success": True, "run": data}
    return data


if __name__ == "__main__":
    mcp.run()  # ponytail: minimal boilerplate - FastMCP handles everything
