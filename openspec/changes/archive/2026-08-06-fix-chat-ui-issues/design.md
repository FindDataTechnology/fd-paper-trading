## Design Decisions

### 1. Chat styling — import CopilotKit CSS

The `@copilotkit/react-ui` package ships a `styles.css` file that provides all the visual styling for the sidebar, message bubbles, input, and buttons. This file must be imported once at the application root.

**Decision**: Add `import "@copilotkit/react-ui/styles.css";` to `web/src/main.tsx` (next to the existing `import "./styles.css";`). This is the standard CopilotKit setup pattern.

### 2. Gate chat behind auth — move ChatProvider inside ProtectedRoute

Currently `ChatProvider` wraps all routes in `App.tsx`, including `/login` and `/register`. This causes the chat sidebar to render on unauthenticated pages.

**Decision**: Move `<ChatProvider>` from wrapping all `<Routes>` to wrapping only the `<ProtectedRoute>` subtree. The `ChatProvider` needs the `token` (for the `Authorization` header) and `activeAccountId` (for the `X-Account-Id` header), both of which are only meaningful when authenticated.

```tsx
// Before: ChatProvider wraps everything
<ChatProvider activeAccountId={activeAccountId}>
  <Routes>
    <Route path="/login" ... />
    ...
  </Routes>
</ChatProvider>

// After: ChatProvider wraps only protected routes
<Routes>
  <Route path="/login" element={<Login />} />
  <Route path="/register" element={<Register />} />
  <Route element={
    <ProtectedRoute>
      <ChatProvider activeAccountId={activeAccountId}>
        <Layout />
      </ChatProvider>
    </ProtectedRoute>
  }>
    ...
  </Route>
</Routes>
```

### 3. Auto-bind active account — inject into agent system prompt

The `AccountSwitcher` component already auto-selects the first account on load (via `useEffect`). The `activeAccountId` flows through `useUI` store → `AppContent` → `ChatProvider` props → `X-Account-Id` header.

However, the CopilotKit runtime's agent prompt doesn't know about the active account. The MCP tools (`place_order`, etc.) require an `account_id` parameter.

**Decision**: Pass the active account ID to the runtime via the `X-Account-Id` header (already done) AND inject it into the agent's system prompt in the runtime server. The runtime reads the header and appends the account context to the agent prompt.

**Alternative considered**: Pass account ID as a tool parameter automatically. Rejected — the LLM should decide when to use the account, not have it forced. The system prompt approach is cleaner.

### 4. Runtime server reads X-Account-Id header

The runtime server (`web/copilot-runtime/server.js`) needs to read the `X-Account-Id` header from incoming requests and inject it into the agent's system prompt.

**Decision**: Add middleware in the Express handler that reads `X-Account-Id` from the request headers and passes it to the agent via the CopilotKit runtime's context mechanism.

## Files Changed

| File | Change |
|------|--------|
| `web/src/main.tsx` | Add `import "@copilotkit/react-ui/styles.css";` |
| `web/src/App.tsx` | Move `ChatProvider` inside `ProtectedRoute` |
| `web/src/components/ChatProvider.tsx` | No change needed (already passes `activeAccountId`) |
| `web/copilot-runtime/server.js` | Read `X-Account-Id` header and inject into agent prompt |
