"""Entry point to run the fd-paper-trading MCP server over HTTP/SSE.

Used by the CopilotKit runtime (web/copilot-runtime/) to connect to the
MCP tools. The default server.py uses stdio (for Claude Code); this
module runs the same tools over HTTP so the Node.js runtime can call them.

Usage:
    uv run src.fd_paper_trading.mcp.http_server
    # or with custom host/port:
    MCP_HTTP_HOST=0.0.0.0 MCP_HTTP_PORT=8100 uv run src.fd_paper_trading.mcp.http_server
"""

import os

from .server import mcp

host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
port = int(os.getenv("MCP_HTTP_PORT", "8100"))

mcp.run(transport="sse", host=host, port=port)
