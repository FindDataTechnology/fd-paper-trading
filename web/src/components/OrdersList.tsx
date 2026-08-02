import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useCancelOrder, useOrders } from "../api/hooks";
import { useOrderEvents } from "../realtime/useRealtime";
import { ACTIVE_ORDER_STATUSES, type OrderOut } from "../api/types";
import { mergeOrderIntoList } from "../lib/orders";
import { price, qty } from "../lib/format";

const STATUS_FILTERS: { label: string; value: string }[] = [
  { label: "All", value: "" },
  { label: "Open", value: "OPEN" },
  { label: "Filled", value: "FILLED" },
  { label: "Cancelled", value: "CANCELLED" },
];

function isActive(status: string): boolean {
  return ACTIVE_ORDER_STATUSES.includes(status);
}

export default function OrdersList({ accountId }: { accountId: string | null }) {
  const [status, setStatus] = useState("");
  const { data: orders, isLoading } = useOrders(accountId, status || undefined);
  const cancelMutation = useCancelOrder();
  const qc = useQueryClient();

  // In-place update from real-time order/fill events (no full refetch).
  const onOrderEvent = useCallback(
    (o: OrderOut) => {
      if (!accountId || o.account_id !== accountId) return;
      qc.setQueriesData({ queryKey: ["orders", accountId] }, (old: OrderOut[] | undefined) => {
        if (!old) return old;
        return mergeOrderIntoList(old, o);
      });
      qc.setQueryData(["order", o.id], o);
    },
    [qc, accountId]
  );
  useOrderEvents(onOrderEvent);

  function handleCancel(orderId: string) {
    if (window.confirm("Cancel this order?")) {
      cancelMutation.mutate(orderId);
    }
  }

  return (
    <div className="card col gap8">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Orders</strong>
        <label className="row gap8" style={{ fontSize: 13 }}>
          <span className="muted">Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_FILTERS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {isLoading && <div className="muted">Loading orders…</div>}
      {!isLoading && orders && orders.length === 0 && (
        <div className="muted">No orders for this account.</div>
      )}
      {orders && orders.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Type</th>
              <th>Qty</th>
              <th>Filled</th>
              <th>Avg px</th>
              <th>Status</th>
              <th>Time</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.id}>
                <td>
                  <Link to={`/instruments/${o.symbol}`}>{o.symbol}</Link>{" "}
                  <Link to={`/orders/${o.id}`} className="muted" style={{ fontSize: 11 }}>
                    detail
                  </Link>
                </td>
                <td className={o.side === "BUY" ? "pos" : "neg"}>{o.side}</td>
                <td>{o.order_type}</td>
                <td>{qty(o.quantity)}</td>
                <td>{qty(o.filled_quantity)}</td>
                <td>{o.avg_fill_price ? price(o.avg_fill_price) : "-"}</td>
                <td>{o.status}</td>
                <td className="muted" style={{ fontSize: 12 }}>
                  {new Date(o.created_at).toLocaleString()}
                </td>
                <td>
                  {isActive(o.status) && (
                    <button
                      className="secondary danger"
                      disabled={cancelMutation.isPending}
                      onClick={() => handleCancel(o.id)}
                    >
                      Cancel
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
