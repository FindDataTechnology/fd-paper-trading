// Order-ticket validation. Pure functions so the rules are unit-testable
// independent of React.
import type { OrderSide, OrderType } from "../api/types";

export interface OrderFormState {
  symbol: string;
  side: OrderSide;
  order_type: OrderType;
  quantity: string;
  limit_price: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: Partial<Record<keyof OrderFormState, string>>;
  warning?: string;
}

/**
 * Validate an order form. `cash` is the account's available cash; `ask` is the
 * current ask quote (used to estimate a market BUY's cost). A buying-power
 * overage is returned as a warning (not a hard error) per the spec: the user
 * may confirm and submit.
 */
export function validateOrder(
  form: OrderFormState,
  cash: string,
  ask: string | null
): ValidationResult {
  const errors: ValidationResult["errors"] = {};

  if (!form.symbol.trim()) errors.symbol = "Symbol is required";

  const q = Number(form.quantity);
  if (!form.quantity || !isFinite(q) || q <= 0) {
    errors.quantity = "Quantity must be a positive number";
  }

  if (form.order_type === "LIMIT") {
    const lp = Number(form.limit_price);
    if (!form.limit_price || !isFinite(lp) || lp <= 0) {
      errors.limit_price = "A positive limit price is required for LIMIT orders";
    }
  }

  let warning: string | undefined;
  const cashN = Number(cash);
  if (form.side === "BUY" && !errors.quantity && isFinite(cashN)) {
    const ref =
      form.order_type === "LIMIT" ? Number(form.limit_price) : ask != null ? Number(ask) : NaN;
    if (isFinite(ref)) {
      const cost = q * ref;
      if (cost > cashN) {
        warning = `Estimated cost ${cost.toFixed(2)} exceeds available cash ${cashN.toFixed(2)}`;
      }
    }
  }

  return { valid: Object.keys(errors).length === 0, errors, warning };
}
