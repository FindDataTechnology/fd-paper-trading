# 归档补注（2026-11-13，只说明不勾）

下面四项未勾的测试任务，指向的组件已于 2026-10-14 的 commit `d3dec0b` 被**有意删除**
（"Removes the bundled CopilotKit runtime and SPA chat: the platform pack's persona
replaces it; the SPA keeps dashboard/orders/charts/strategies."）。

这些测试永远不会被写、也永远不会红：勾上等于伪造完成，删掉等于篡改历史。
保留 `[ ]` 并附这句说明 —— 未勾在这里代表「随组件一起作废」，不是「做了一半」。
该 capability 的 Purpose 已于同日 `8e092f1` 改记为「保留为历史约束记录」。

## 1. Backend: Chat Endpoint

- [x] 1.1 Create `/api/v1/chat` endpoint in FastAPI that accepts POST requests with user messages
- [x] 1.2 Implement JWT authentication for the chat endpoint (reuse existing auth middleware)
- [x] 1.3 Create a proxy function that forwards chat requests to the MCP server
- [x] 1.4 Implement tool execution context (pass active account ID, user JWT to MCP)
- [x] 1.5 Handle MCP server responses and format them for CopilotKit
- [x] 1.6 Add error handling for MCP connection failures, tool execution errors
- [ ] 1.7 Write unit tests for the chat endpoint (mock MCP server responses)

## 2. Frontend: CopilotKit Integration

- [x] 2.1 Install CopilotKit dependencies: `@copilotkit/react-core`, `@copilotkit/react-ui`, `@copilotkit/runtime`
- [x] 2.2 Create a `ChatProvider` component that wraps the app with CopilotKit context
- [x] 2.3 Configure CopilotKit to use OpenAI (or chosen AI provider) with API key from environment
- [x] 2.4 Implement the chat runtime that connects to `/api/v1/chat` endpoint
- [x] 2.5 Define the MCP tools in CopilotKit's tool format (place_order, cancel_order, etc.)
- [x] 2.6 Create a `ChatSidebar` component using CopilotKit's `<CopilotSidebar />`
- [x] 2.7 Add a chat toggle button to the Layout component (visible on all authenticated pages)
- [x] 2.8 Pass the active account ID from UI state to the chat context

## 3. Authentication & Authorization

- [x] 3.1 Ensure the chat endpoint extracts JWT from request headers
- [x] 3.2 Pass the JWT to MCP server for tool authorization
- [x] 3.3 Verify that tool invocations respect account ownership (user can only trade their own accounts)
- [ ] 3.4 Add tests for unauthorized access scenarios (missing JWT, wrong account)

## 4. UI/UX & Error Handling

- [x] 4.1 Add loading indicators while AI is processing (CopilotKit provides this out of the box)
- [x] 4.2 Display error messages when tool invocations fail (e.g., insufficient funds)
- [x] 4.3 Add a "retry" option for failed messages
- [x] 4.4 Ensure the chat sidebar is responsive (works on mobile, doesn't break layout)
- [x] 4.5 Add a welcome message when the chat is first opened

## 5. Testing & Documentation

- [ ] 5.1 Write integration tests for the full chat flow (user message → tool execution → response)
- [ ] 5.2 Test error scenarios (network failure, invalid tool parameters, unauthorized access)
- [x] 5.3 Update README.md with instructions on how to use the chat feature
- [x] 5.4 Document the required environment variables (OPENAI_API_KEY, etc.)
- [x] 5.5 Add a screenshot or GIF of the chat interface in action

## 6. Deployment & Configuration

- [x] 6.1 Add `OPENAI_API_KEY` to `.env.example` with a placeholder
- [x] 6.2 Update deployment scripts to include the new environment variable
- [x] 6.3 Document how to obtain and configure an OpenAI API key
- [x] 6.4 Add rate limiting to the chat endpoint to prevent abuse
