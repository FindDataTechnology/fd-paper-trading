// In-place order-list update from real-time events (no full refetch).
import type { OrderOut } from "../api/types";

/** Merge an updated order into a list: replace if present, else prepend. */
export function mergeOrderIntoList(orders: OrderOut[], updated: OrderOut): OrderOut[] {
  return orders.some((o) => o.id === updated.id)
    ? orders.map((o) => (o.id === updated.id ? updated : o))
    : [updated, ...orders];
}
