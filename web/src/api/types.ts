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
