// API types mirroring the backend Pydantic schemas. Decimals arrive as JSON
// strings (Pydantic serializes Decimal -> string), so numeric fields are typed
// as `string` and parsed at the edge with Number().

export interface UserOut {
  id: string;
  email: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  expires_in: number;
  user_id: string;
}

export interface AccountOut {
  id: string;
  name: string;
  venue: string;
  currency: string;
  starting_balance: string;
  cash_balance: string;
  created_at: string;
}

export interface AccountSummary {
  id: string;
  name: string;
  venue: string;
  currency: string;
  cash_balance: string;
  equity: string;
  open_positions: number;
  open_orders: number;
  created_at: string;
}

export interface FillOut {
  id: string;
  quantity: string;
  price: string;
  side: string;
  ts: string;
}

export interface OrderOut {
  id: string;
  account_id: string;
  client_order_id: string | null;
  instrument_id: string;
  symbol: string;
  side: string;
  order_type: string;
  quantity: string;
  limit_price: string | null;
  status: string;
  filled_quantity: string;
  avg_fill_price: string | null;
  reject_reason: string | null;
  created_at: string;
  updated_at: string;
  fills: FillOut[];
}

export interface PositionOut {
  id: string;
  instrument_id: string;
  symbol: string;
  quantity: string;
  avg_price: string;
  realized_pnl: string;
}

export interface InstrumentSearchResult {
  symbol: string;
  name: string;
  venue: string;
}

export interface Bar {
  ts: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}

export interface Quote {
  symbol: string;
  bid: string;
  ask: string;
  ts: number;
}

export type OrderSide = "BUY" | "SELL";
export type OrderType = "MARKET" | "LIMIT";

export interface OrderCreate {
  account_id: string;
  symbol: string;
  side: OrderSide;
  order_type: OrderType;
  quantity: string;
  limit_price?: string | null;
  client_order_id?: string | null;
}

// Active (non-terminal) order statuses, mirrored from the backend.
export const ACTIVE_ORDER_STATUSES = [
  "PENDING",
  "SUBMITTED",
  "OPEN",
  "PARTIALLY_FILLED",
];

// ---------------------------------------------------------------------------
// Strategies (quant-strategies spec). Decimals/dates arrive as JSON strings.
// ---------------------------------------------------------------------------

/** A template's params are a JSON Schema object (type/properties/required/...). */
export interface JsonSchema {
  type?: string;
  properties?: Record<string, JsonSchemaProp>;
  required?: string[];
  additionalProperties?: boolean;
}

export interface JsonSchemaProp {
  type?: string;
  minimum?: number;
  maximum?: number;
  default?: number | string | boolean | null;
  description?: string;
}

/** Template kind: a fixed strategy with a validated params schema. */
export interface TemplateSpec {
  kind: "template";
  template: string;
  symbol: string;
  params?: Record<string, number | string>;
}

/** Code kind: free-form LLM/user Python conforming to the on_bar(ctx) contract. */
export interface CodeSpec {
  kind: "code";
  symbol: string;
  source: string;
}

export type StrategySpec = TemplateSpec | CodeSpec;

export interface StrategyOut {
  id: string;
  name: string;
  description: string | null;
  spec: StrategySpec;
  spec_summary: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface StrategyTemplateOut {
  id: string;
  name: string;
  description: string;
  params_schema: JsonSchema;
}

export interface StrategyCreate {
  name: string;
  description?: string | null;
  spec: StrategySpec;
}

export interface StrategyUpdate {
  name?: string;
  description?: string | null;
  spec?: StrategySpec;
}

export interface BacktestRequest {
  start: string; // ISO date
  end: string; // ISO date
  initial_cash?: string;
}

export interface PaperRunRequest {
  account_id: string;
}

/** Run statuses mirrored from the backend runner state machine. */
export type StrategyRunStatus =
  | "QUEUED"
  | "STARTING"
  | "RUNNING"
  | "COMPLETED"
  | "STOPPED"
  | "FAILED"
  | "ERROR";

export const ACTIVE_RUN_STATUSES: StrategyRunStatus[] = [
  "QUEUED",
  "STARTING",
  "RUNNING",
];

export interface StrategyRunOut {
  id: string;
  strategy_id: string;
  strategy_version: number;
  mode: string; // "BACKTEST" | "PAPER"
  account_id: string | null;
  status: StrategyRunStatus;
  params: Record<string, unknown> | null;
  stats: Record<string, number> | null;
  error: string | null;
  started_at: string | null;
  stopped_at: string | null;
  created_at: string;
}

export interface EquityPointOut {
  date: string; // ISO date
  equity: string; // Decimal as string
}

export interface StrategyEventOut {
  id: string;
  run_id: string;
  ts: string;
  type: string;
  message: string;
  data: Record<string, unknown> | null;
}

export interface StrategyRunDetailOut extends StrategyRunOut {
  equity_points: EquityPointOut[];
  events: StrategyEventOut[];
}
