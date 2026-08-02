import { describe, expect, it } from "vitest";
import { mergeOrderIntoList } from "./orders";
import type { OrderOut } from "../api/types";

const mk = (id: string, status: string, filled: string): OrderOut => ({
  id,
  account_id: "a1",
  client_order_id: null,
  instrument_id: "AAPL.SIM",
  symbol: "AAPL",
  side: "BUY",
  order_type: "MARKET",
  quantity: "10",
  limit_price: null,
  status,
  filled_quantity: filled,
  avg_fill_price: filled === "0" ? null : "150",
  reject_reason: null,
  created_at: "",
  updated_at: "",
  fills: [],
});

describe("mergeOrderIntoList", () => {
  it("replaces an existing order in place (preserving order)", () => {
    const list = [mk("o1", "OPEN", "0"), mk("o2", "FILLED", "10")];
    const updated = mk("o1", "FILLED", "10");
    const result = mergeOrderIntoList(list, updated);
    expect(result.map((o) => o.id)).toEqual(["o1", "o2"]);
    expect(result[0].status).toBe("FILLED");
    expect(result[0].filled_quantity).toBe("10");
  });

  it("prepends a new order", () => {
    const list = [mk("o1", "FILLED", "10")];
    const result = mergeOrderIntoList(list, mk("o2", "OPEN", "0"));
    expect(result.map((o) => o.id)).toEqual(["o2", "o1"]);
  });

  it("handles an empty list", () => {
    const result = mergeOrderIntoList([], mk("o1", "OPEN", "0"));
    expect(result).toHaveLength(1);
  });
});
