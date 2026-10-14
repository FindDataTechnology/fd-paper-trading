/**
 * CopilotKit runtime server — bridges the CopilotKit frontend to the
 * fd-paper-trading MCP server (which exposes place_order, cancel_order, etc.).
 *
 * The MCP server runs as a separate process over HTTP/SSE:
 *   uv run python -m fd_paper_trading.mcp.http_server
 *
 * Usage:
 *   OPENAI_API_KEY=sk-... node server.js
 */

import express from "express";
import { CopilotRuntime, BuiltInAgent, InMemoryAgentRunner } from "@copilotkit/runtime/v2";
import { createCopilotExpressHandler } from "@copilotkit/runtime/v2/express";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { config as loadEnv } from "dotenv";
import { fileURLToPath } from "node:url";

// Load the repo-root .env (the same file the Python backend reads via
// pydantic-settings) so the LLM config (OPENAI_API_KEY, OPENAI_API_BASE,
// COPILOT_MODEL, ...) is managed from .env, not hardcoded or ad-hoc.
// Path is relative to this file, so it works regardless of the cwd.
loadEnv({ path: fileURLToPath(new URL("../../.env", import.meta.url)), quiet: true });

const mcpUrl = process.env.MCP_SERVER_URL || "http://127.0.0.1:8100/sse";
const port = parseInt(process.env.COPILOT_PORT || "4100", 10);

const openaiApiBase = process.env.OPENAI_API_BASE || "https://ark.cn-beijing.volces.com/api/plan/v1";
const openaiApiKey = process.env.OPENAI_API_KEY;
const model = process.env.COPILOT_MODEL || "glm-5.2";

if (!openaiApiKey) {
  console.error("ERROR: OPENAI_API_KEY environment variable is required");
  process.exit(1);
}

// Create OpenAI-compatible provider for custom endpoints
const customProvider = createOpenAICompatible({
  name: "custom-llm",
  apiKey: openaiApiKey,
  baseURL: openaiApiBase,
});

const agent = new BuiltInAgent({
  model: customProvider(model),
  maxSteps: 5,
  prompt:
    "You are a paper-trading and quant-strategy assistant for fd-paper-trading. " +
    "You help the user trade on their paper accounts AND build, backtest, " +
    "and run automated trading strategies. Use the available MCP tools; " +
    "never invent tool names or parameters.\n\n" +

    "ORDERS: use list_accounts / get_account / place_order / cancel_order / " +
    "list_orders. Always confirm before placing or cancelling orders.\n\n" +

    "STRATEGIES — template track (the default, always available):\n" +
    "1. Call list_strategy_templates to see the catalog (sma_cross, " +
    "rsi_reversion, bollinger_breakout, macd_cross, dca) and each " +
    "template's params_schema.\n" +
    "2. Discuss with the user, then build a spec: " +
    '{"kind":"template","template":"<id>","symbol":"AAPL","params":{...}} ' +
    "and call create_strategy. Use update_strategy to change it later " +
    "(a spec change bumps the strategy version; name/description edits " +
    "do not).\n" +
    "3. Backtest: call run_backtest(strategy_id, start, end, initial_cash) " +
    "with ISO dates. The run is asynchronous — poll get_strategy_run " +
    "until status is COMPLETED (report total_return, max_drawdown, " +
    "sharpe, win_rate, trade_count, final_equity and describe the equity " +
    "curve) or FAILED (read the error, explain it, propose a fix).\n" +
    "4. Live paper run: start_strategy(strategy_id, account_id) trades the " +
    "strategy on a paper account on each new daily bar; " +
    "stop_strategy(run_id) stops it (cancels its resting LIMIT orders, " +
    "never closes positions). Always confirm before start/stop. Use " +
    "list_strategy_runs / get_strategy_run for status and history.\n\n" +

    "STRATEGIES — code track (only if the user's group allows custom " +
    "code; a 403 custom_code_not_allowed error means it is disabled — " +
    "fall back to templates): spec is " +
    '{"kind":"code","symbol":"AAPL","source":"<python>"}. The source ' +
    "MUST define a top-level `def on_bar(ctx):` and MUST NOT contain any " +
    "import statements, dunder/underscore-prefixed names, or calls to " +
    "eval/exec/open/compile/globals/locals/vars/input/breakpoint. " +
    "The ctx contract: ctx.closes() / ctx.opens() / ctx.highs() / " +
    "ctx.lows() / ctx.volumes() return oldest-first float lists; " +
    "ctx.sma(period), ctx.ema(period), ctx.rsi(period), " +
    "ctx.macd(fast, slow, signal) -> (macd, signal, histogram), " +
    "ctx.bollinger(period, num_std) -> (mid, upper, lower) compute " +
    "indicators over the closes; ctx.position is the signed share count, " +
    "ctx.cash the remaining cash; ctx.buy(quantity, limit_price=None) " +
    "and ctx.sell(quantity, limit_price=None) emit orders (market when " +
    "limit_price is omitted). Keep code short and numeric-only.\n\n" +

    "ERROR SELF-REPAIR: tool failures return a structured body with a " +
    "\"code\", \"message\", and often a \"fields\" list pinpointing each " +
    "spec violation. Read them, fix the arguments, and retry once before " +
    "asking the user. Be concise and clear.",
  mcpServers: [
    {
      type: "sse",
      url: mcpUrl,
      serverId: "fd-paper-trading",
    },
  ],
});

/**
 * Per-request middleware: read the `X-Account-Id` header the frontend sends
 * (see ChatProvider.tsx) and inject it into the agent run's `context` so the
 * BuiltInAgent appends it to its system prompt. This lets the assistant
 * default to the active account without the user specifying an account_id.
 *
 * The single-route request body is a JSON envelope `{ method, params, body }`
 * where `body` is the RunAgentInput. The classic BuiltInAgent renders each
 * `body.context[i]` as `{description}:\n{value}` inside the system prompt, so
 * adding a context entry here is enough to surface the account to the LLM.
 */
async function injectActiveAccountContext({ request }) {
  const accountId = request.headers.get("x-account-id");
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return;

  let envelope;
  try {
    envelope = await request.clone().json();
  } catch {
    return; // Not JSON / unreadable - let the original request pass through.
  }

  // Only agent-execution requests carry a RunAgentInput in `body`.
  if (!envelope || typeof envelope !== "object" || typeof envelope.body !== "object") {
    return;
  }

  const contextEntry = {
    description: "Active Account",
    value: accountId
      ? `The user's currently active account ID is "${accountId}". Use this ` +
        `account_id when calling place_order, get_account, or list_orders ` +
        `unless the user explicitly asks to use a different account.`
      : `No account is currently active. If the user asks to place an order, ` +
        `check a balance, or list orders, ask them to create or select an ` +
        `account first.`,
  };

  const body = { ...envelope.body };
  body.context = Array.isArray(body.context)
    ? [...body.context, contextEntry]
    : [contextEntry];

  const headers = new Headers(request.headers);
  headers.set("content-type", "application/json");
  headers.delete("content-length");

  return new Request(request.url, {
    method: request.method,
    headers,
    body: JSON.stringify({ ...envelope, body }),
    signal: request.signal,
  });
}

const runtime = new CopilotRuntime({
  agents: { default: agent },
  runner: new InMemoryAgentRunner(),
  beforeRequestMiddleware: injectActiveAccountContext,
});

const app = express();
app.use(express.json());

// Health check
app.get("/health", (req, res) => {
  res.json({ status: "ok", mcp: mcpUrl });
});

// CopilotKit info endpoint
app.get("/v1/runtime/info", (req, res) => {
  res.json({
    version: "1.66.2",
    supportsThreads: true,
    supportsState: true,
    agents: {
      default: {
        name: "default",
        description: "",
        capabilities: {
          tools: { supported: true, clientProvided: true },
          streaming: true,
          humanInTheLoop: { interrupts: true },
        },
      },
    },
    mode: "sse",
    suggestions: true,
    a2uiEnabled: false,
    openGenerativeUIEnabled: false,
    telemetryDisabled: false,
  });
});

// CopilotKit single-route handler
app.use("/copilotkit", createCopilotExpressHandler({
  runtime,
  basePath: "/",
  mode: "single-route",
  cors: true,
}));

app.listen(port, () => {
  console.log(`CopilotKit runtime listening on http://localhost:${port}`);
  console.log(`MCP server: ${mcpUrl}`);
  console.log(`Model: ${model} via ${openaiApiBase}`);
});
