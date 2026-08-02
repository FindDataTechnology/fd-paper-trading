import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useCancelOrder, useOrder } from "../api/hooks";
import { useOrderEvents } from "../realtime/useRealtime";
import { ACTIVE_ORDER_STATUSES, type OrderOut } from "../api/types";
import { apiErrorMessage } from "../api/client";
import { price, qty } from "../lib/format";

function isActive(status: string): boolean {
  return ACTIVE_ORDER_STATUSES.includes(status);
}

export default function OrderDetailPage() {
  const { orderId } = useParams<{ orderId: string }>();
  const { data: order, isLoading } = useOrder(orderId ?? null);
  const cancelMutation = useCancelOrder();
  const qc = useQueryClient();

  // Keep the detail view fresh from real-time events (in-place, no refetch).
  const onOrderEvent = useCallback(
    (o: OrderOut) => {
      if (orderId && o.id === orderId) {
        qc.setQueryData(["order", orderId], o);
      }
    },
    [qc, orderId]
  );
  useOrderEvents(onOrderEvent);

  if (isLoading) return <div className="muted">Loading order…</div>;
  if (!order) return <div className="muted">Order not found.</div>;

  return (
    <div className="col">
      <div className="card col gap8">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <strong>
            {order.symbol} {order.side} {order.order_type}
          </strong>
          <Link to="/orders">← Back to orders</Link>
        </div>
        <table>
          <tbody>
            <Row k="Order ID" v={order.id} />
            <Row k="Status" v={order.status} />
            <Row k="Quantity" v={qty(order.quantity)} />
            <Row k="Filled" v={qty(order.filled_quantity)} />
            <Row k="Avg fill price" v={order.avg_fill_price ? price(order.avg_fill_price) : "-"} />
            <Row k="Limit price" v={order.limit_price ? price(order.limit_price) : "-"} />
            <Row k="Instrument" v={order.instrument_id} />
            <Row k="Created" v={new Date(order.created_at).toLocaleString()} />
            {order.reject_reason && <Row k="Reject reason" v={order.reject_reason} />}
          </tbody>
        </table>
        {isActive(order.status) && (
          <button
            className="secondary danger"
            disabled={cancelMutation.isPending}
            onClick={() => {
              if (window.confirm("Cancel this order?")) cancelMutation.mutate(order.id);
            }}
          >
            {cancelMutation.isPending ? "Cancelling…" : "Cancel order"}
          </button>
        )}
        {cancelMutation.error && (
          <div className="error-text">{apiErrorMessage(cancelMutation.error)}</div>
        )}
      </div>

      <div className="card col gap8">
        <strong>Fills</strong>
        {order.fills.length === 0 ? (
          <div className="muted">No fills.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Side</th>
                <th>Quantity</th>
                <th>Price</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {order.fills.map((f) => (
                <tr key={f.id}>
                  <td className={f.side === "BUY" ? "pos" : "neg"}>{f.side}</td>
                  <td>{qty(f.quantity)}</td>
                  <td>{price(f.price)}</td>
                  <td className="muted" style={{ fontSize: 12 }}>
                    {new Date(f.ts).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <tr>
      <th style={{ width: 160 }}>{k}</th>
      <td>{v}</td>
    </tr>
  );
}
