## Why

The CopilotKit chat integration shipped with three UX defects that make it unusable: the chat panel renders without CopilotKit's stylesheet (raw unstyled text), the sidebar appears on the login/register pages where there is no authenticated user, and the chat has no awareness of the active account context. These need fixing before the chat feature is usable.

## What Changes

- **Fix chat styling**: Import `@copilotkit/react-ui/styles.css` so the sidebar, message bubbles, and input render with CopilotKit's intended UI instead of plain unstyled DOM.
- **Gate chat behind auth**: Move `ChatProvider` (and its `CopilotSidebar`) inside the `ProtectedRoute` subtree so the chat only renders on authenticated pages (Dashboard, Orders, etc.), never on `/login` or `/register`.
- **Auto-bind active account**: Ensure the chat context carries the active account ID. The `AccountSwitcher` already auto-selects the first account on load; the chat already receives `activeAccountId` via the `X-Account-Id` header. Verify the flow and surface the active account in the chat's system prompt so the assistant defaults to operating on it without the user having to specify the account ID each time.

## Capabilities

### New Capabilities
<!-- None — this change fixes an existing capability, no new capability is introduced. -->

### Modified Capabilities
- `web-copilotkit-chat`: Chat panel renders with proper CopilotKit styling; chat only appears on authenticated pages; the assistant is aware of the active account context.

## Impact

- **Frontend code**:
  - `web/src/main.tsx` — add CopilotKit CSS import
  - `web/src/App.tsx` — move `ChatProvider` inside `ProtectedRoute`
  - `web/src/components/ChatProvider.tsx` — inject active account ID into the agent's system context (instructions)
- **No backend changes**: the MCP server, CopilotKit runtime, and FastAPI backend are unchanged.
- **No new dependencies**: `@copilotkit/react-ui` is already installed; only its CSS needs importing.
