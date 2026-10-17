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

/**
 * Weight kind: a universe of symbols plus target weights, rebalanced every N
 * bars. Exactly one of `source` (Python defining target_weights(ctx)) or
 * `weights_series` (ISO date -> {symbol: weight}) is present.
 *
 * `weights_series` periods are typed loosely on purpose: the API also accepts a
 * full TargetWeights contract instance per period (a pack's published output),
 * which is not a `Record<string, number>`. The editor authors only the plain
 * mapping form; a read-only view renders whatever came back.
 */
export interface WeightSpec {
  kind: "weight";
  universe: string[];
  rebalance: { every_n_bars: number };
  source?: string | null;
  weights_series?: Record<string, Record<string, unknown>> | null;
  cash_floors?: Record<string, number>;
}

export type StrategySpec = TemplateSpec | CodeSpec | WeightSpec;

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

/**
 * The full authoring surface (`GET /strategy-catalog`). `weight_track` is typed
 * loosely because it describes a surface that grows; the fields the editor reads
 * are the universe bounds and the rebalance field.
 */
export interface StrategyCatalogOut {
  templates: StrategyTemplateOut[];
  spec_kinds: string[];
  weight_track: {
    kind: string;
    universe: {
      min_size: number;
      max_size: number;
      max_size_config_key: string;
    };
    rebalance: { field: string; type: string; minimum: number };
    sources: { form: string; field: string }[];
    exactly_one_source: string[];
  };
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

/** Cost parameters actually used for a run (snapshot, not a live config). */
export interface CostConfig {
  enabled: boolean;
  commission_rate: string;
  stamp_tax_rate: string;
  slippage_bps: string;
}

/** Aggregated fees for a run. Values are decimal strings, not numbers. */
export interface CostSummary {
  commission: string;
  stamp_tax: string;
  slippage: string;
  total: string;
  fills: number;
}

export interface BenchmarkRelative {
  excess_return: number | null;
  tracking_error: number | null;
  information_ratio: number | null;
  aligned_points: number;
  reason?: string;
}

/** One benchmark's result. `available: false` carries the reason instead of a
 * curve — the API never silently omits a requested benchmark. */
export interface BenchmarkResult {
  symbol: string;
  available: boolean;
  reason: string | null;
  points: [string, string][] | null;
  stats: Record<string, number | null> | null;
  relative: BenchmarkRelative | null;
}

export interface WalkForwardWindow {
  window: number;
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  stats: Record<string, number | null>;
}

export interface ScanRow {
  params: Record<string, number>;
  stats: Record<string, number | null>;
}

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
  // Backtest v2 records: null on runs that predate these capabilities, which is
  // how the panel tells "not enabled" apart from "enabled, produced nothing".
  cost_config: CostConfig | null;
  cost_summary: CostSummary | null;
  benchmark_config: string[] | null;
  benchmark_results: BenchmarkResult[] | null;
  walkforward_windows: WalkForwardWindow[] | null;
  param_grid: Record<string, number[]> | null;
  scan_results: ScanRow[] | null;
  in_sample: boolean | null;
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
