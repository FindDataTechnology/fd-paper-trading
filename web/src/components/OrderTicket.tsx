import { useState } from "react";
import { useAccountSummary, usePlaceOrder } from "../api/hooks";
import { useQuote } from "../realtime/useRealtime";
import { apiErrorMessage } from "../api/client";
import { mid } from "../lib/format";
import { validateOrder, type OrderFormState } from "../lib/validation";
import type { OrderSide, OrderType } from "../api/types";

interface Props {
  symbol: string;
  accountId: string | null;
}

export default function OrderTicket({ symbol, accountId }: Props) {
  const { data: summary } = useAccountSummary(accountId);
  const quote = useQuote(symbol);
  const placeOrder = usePlaceOrder();

  const [form, setForm] = useState<OrderFormState>({
    symbol,
    side: "BUY",
    order_type: "MARKET",
    quantity: "",
    limit_price: "",
  });
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  const cash = summary?.cash_balance ?? "0";
  const ask = quote ? quote.ask : null;
  const result = validateOrder(form, cash, ask);
  const canSubmit = result.valid && (!result.warning || confirmOverwrite) && !placeOrder.isPending;

  function update<K extends keyof OrderFormState>(key: K, value: OrderFormState[K]) {
    setSuccess(null);
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!accountId || !canSubmit) return;
    setSuccess(null);
    try {
      await placeOrder.mutateAsync({
        account_id: accountId,
        symbol: form.symbol,
        side: form.side as OrderSide,
        order_type: form.order_type as OrderType,
        quantity: form.quantity,
        limit_price: form.order_type === "LIMIT" ? form.limit_price : null,
      });
      setSuccess("Order submitted");
      setForm((f) => ({ ...f, quantity: "" }));
      setConfirmOverwrite(false);
    } catch {
      // error surfaced via placeOrder.error
    }
  }

  return (
    <form className="card col gap8" onSubmit={handleSubmit}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Order ticket</strong>
        {quote && (
          <span className="muted" style={{ fontSize: 12 }}>
            bid {quote.bid} / ask {quote.ask} (mid {mid(quote.bid, quote.ask).toFixed(2)})
          </span>
        )}
      </div>

      <div className="row gap8">
        <label className="col" style={{ flex: 1 }}>
          <span className="muted" style={{ fontSize: 12 }}>Symbol</span>
          <input value={form.symbol} onChange={(e) => update("symbol", e.target.value.toUpperCase())} />
        </label>
        <label className="col">
          <span className="muted" style={{ fontSize: 12 }}>Side</span>
          <select value={form.side} onChange={(e) => update("side", e.target.value as OrderSide)}>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </label>
      </div>

      <div className="row gap8">
        <label className="col">
          <span className="muted" style={{ fontSize: 12 }}>Type</span>
          <select value={form.order_type} onChange={(e) => update("order_type", e.target.value as OrderType)}>
            <option value="MARKET">MARKET</option>
            <option value="LIMIT">LIMIT</option>
          </select>
        </label>
        <label className="col" style={{ flex: 1 }}>
          <span className="muted" style={{ fontSize: 12 }}>Quantity</span>
          <input
            value={form.quantity}
            inputMode="decimal"
            onChange={(e) => update("quantity", e.target.value)}
          />
        </label>
        {form.order_type === "LIMIT" && (
          <label className="col" style={{ flex: 1 }}>
            <span className="muted" style={{ fontSize: 12 }}>Limit price</span>
            <input
              value={form.limit_price}
              inputMode="decimal"
              onChange={(e) => update("limit_price", e.target.value)}
            />
          </label>
        )}
      </div>

      {result.errors.quantity && <div className="error-text">{result.errors.quantity}</div>}
      {result.errors.limit_price && <div className="error-text">{result.errors.limit_price}</div>}
      {result.errors.symbol && <div className="error-text">{result.errors.symbol}</div>}
      {result.warning && (
        <div className="warning-text">
          {result.warning}
          <label className="row gap8" style={{ marginTop: 4 }}>
            <input
              type="checkbox"
              checked={confirmOverwrite}
              onChange={(e) => setConfirmOverwrite(e.target.checked)}
            />
            <span>Confirm and submit anyway</span>
          </label>
        </div>
      )}
      {placeOrder.error && <div className="error-text">{apiErrorMessage(placeOrder.error)}</div>}
      {success && <div className="pos">{success}</div>}

      <button type="submit" disabled={!canSubmit}>
        {placeOrder.isPending ? "Submitting…" : `${form.side} ${form.symbol || ""}`}
      </button>
      <div className="muted" style={{ fontSize: 12 }}>
        Available cash: {Number(cash).toFixed(2)}
      </div>
    </form>
  );
}
