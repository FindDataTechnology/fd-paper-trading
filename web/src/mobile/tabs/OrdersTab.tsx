import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useCancelOrder, useOrders } from "../../api/hooks";
import { useOrderEvents } from "../../realtime/useRealtime";
import { ACTIVE_ORDER_STATUSES, type OrderOut } from "../../api/types";
import { mergeOrderIntoList } from "../../lib/orders";
import { price, qty } from "../../lib/format";

const STATUS_FILTERS = [
  { label: "All", value: "" },
  { label: "Open", value: "OPEN" },
  { label: "Filled", value: "FILLED" },
  { label: "Cancelled", value: "CANCELLED" },
];

const isActive = (s: string) => ACTIVE_ORDER_STATUSES.includes(s);

export default function OrdersTab({
  accountId,
}: {
  accountId: string | null;
}) {
  const [status, setStatus] = useState("");
  const { data: orders, isLoading } = useOrders(accountId, status || undefined);
  const cancelMutation = useCancelOrder();
  const qc = useQueryClient();

  // In-place update from real-time order/fill events (no full refetch).
  const onOrderEvent = useCallback(
    (o: OrderOut) => {
      if (!accountId || o.account_id !== accountId) return;
      qc.setQueriesData(
        { queryKey: ["orders", accountId] },
        (old: OrderOut[] | undefined) => (old ? mergeOrderIntoList(old, o) : old)
      );
      qc.setQueryData(["order", o.id], o);
    },
    [qc, accountId]
  );
  useOrderEvents(onOrderEvent);

  function cancel(id: string) {
    if (window.confirm("Cancel this order?")) cancelMutation.mutate(id);
  }

  return (
    <div className="m-list">
      <div className="m-row" style={{ overflowX: "auto" }}>
        {STATUS_FILTERS.map((s) => (
          <button
            key={s.value}
            className={`m-btn-sm${status === s.value ? "" : " secondary"}`}
            onClick={() => setStatus(s.value)}
          >
            {s.label}
          </button>
        ))}
      </div>

      {isLoading && <div className="m-empty">Loading orders…</div>}
      {!isLoading && orders && orders.length === 0 && (
        <div className="m-empty">No orders for this account.</div>
      )}

      {orders?.map((o) => (
        <div key={o.id} className="m-card">
          <div className="m-between">
            <span>
              <strong>{o.symbol}</strong>{" "}
              <span className={o.side === "BUY" ? "pos" : "neg"} style={{ fontSize: 12 }}>
                {o.side}
              </span>{" "}
              <span className="muted" style={{ fontSize: 12 }}>
                {o.order_type}
              </span>
            </span>
            <span className="muted" style={{ fontSize: 11 }}>
              {o.status}
            </span>
          </div>
          <div className="m-row" style={{ fontSize: 13, marginTop: 6 }}>
            <span>Qty {qty(o.quantity)}</span>
            <span className="muted">filled {qty(o.filled_quantity)}</span>
            {o.avg_fill_price && (
              <span className="muted">@ {price(o.avg_fill_price)}</span>
            )}
            <span className="m-grow" />
            <span className="muted" style={{ fontSize: 11 }}>
              {new Date(o.created_at).toLocaleString()}
            </span>
          </div>
          {isActive(o.status) && (
            <div className="m-row" style={{ marginTop: 8 }}>
              <button
                className="secondary danger m-btn-sm"
                disabled={cancelMutation.isPending}
                onClick={() => cancel(o.id)}
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
