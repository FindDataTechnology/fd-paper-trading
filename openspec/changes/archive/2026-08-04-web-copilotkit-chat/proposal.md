## Why

Users need a conversational interface to interact with the paper trading system. Instead of navigating multiple UI components (watchlist, order panel, positions table), users want to chat naturally with an AI assistant that can execute trades, check positions, and provide market insights. CopilotKit provides a React-native chat framework that integrates seamlessly with our existing MCP server tools.

## What Changes

**New:** Add a chat interface powered by CopilotKit to the web app. Users can:
- Ask the AI assistant to place orders ("Buy 100 shares of AAPL")
- Query account status ("What's my cash balance?")
- Get position summaries ("Show my open positions")
- Cancel orders ("Cancel my TSLA order")
- Receive real-time updates and confirmations

**Modified:** The web app gains a new chat panel (likely as a floating widget or sidebar) that connects to the MCP server via CopilotKit's tool integration.

## Capabilities

### New Capabilities
- `web-copilotkit-chat`: Chat interface with AI assistant that can execute MCP tools (place_order, cancel_order, list_accounts, get_account, list_orders)

### Modified Capabilities
- None (existing capabilities preserved)

## Impact

- **Frontend:** New chat UI component using CopilotKit React SDK
- **Backend:** New endpoint to proxy CopilotKit requests to MCP server (or direct MCP integration)
- **Dependencies:** Add `@copilotkit/react-core`, `@copilotkit/react-ui`, and related packages
- **Authentication:** Chat must respect existing JWT/API token auth
- **UI/UX:** New floating chat widget or sidebar panel, accessible from all pages
