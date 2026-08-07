# Tasks

## 1. Import CopilotKit CSS
- [x] Add `import "@copilotkit/react-ui/styles.css";` to `web/src/main.tsx`
- [x] Verify chat panel renders with proper styling

## 2. Gate chat behind authentication
- [x] Move `ChatProvider` inside `ProtectedRoute` in `web/src/App.tsx`
- [x] Verify chat does NOT appear on `/login` or `/register` pages
- [x] Verify chat DOES appear on authenticated pages (Dashboard, Orders, etc.)

## 3. Auto-bind active account to chat
- [x] Update `web/copilot-runtime/server.js` to read `X-Account-Id` header from requests
- [x] Inject active account ID into the agent's system prompt
- [x] Verify the assistant can use the active account without the user specifying it

## 4. Test end-to-end
- [x] Login and verify chat appears
- [x] Open chat and verify it's styled properly
- [x] Ask "What accounts do I have?" and verify response uses active account
- [x] Ask "Buy 10 shares of AAPL" and verify order is placed on active account
- [x] Logout and verify chat disappears
