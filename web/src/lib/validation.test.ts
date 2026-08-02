import { describe, expect, it } from "vitest";
import { validateOrder, type OrderFormState } from "./validation";

const base: OrderFormState = {
  symbol: "AAPL",
  side: "BUY",
  order_type: "MARKET",
  quantity: "10",
  limit_price: "",
};

describe("validateOrder", () => {
  it("accepts a valid market order", () => {
    const r = validateOrder(base, "100000", "150.05");
    expect(r.valid).toBe(true);
    expect(r.errors).toEqual({});
    expect(r.warning).toBeUndefined();
  });

  it("rejects a non-positive quantity", () => {
    expect(validateOrder({ ...base, quantity: "0" }, "100000", "150").valid).toBe(false);
    expect(validateOrder({ ...base, quantity: "-5" }, "100000", "150").valid).toBe(false);
    expect(validateOrder({ ...base, quantity: "" }, "100000", "150").valid).toBe(false);
    expect(validateOrder({ ...base, quantity: "abc" }, "100000", "150").valid).toBe(false);
  });

  it("requires a positive limit price for LIMIT orders", () => {
    const r = validateOrder({ ...base, order_type: "LIMIT", limit_price: "" }, "100000", "150");
    expect(r.valid).toBe(false);
    expect(r.errors.limit_price).toBeTruthy();
  });

  it("accepts a LIMIT order with a positive price", () => {
    const r = validateOrder(
      { ...base, order_type: "LIMIT", limit_price: "149.00" },
      "100000",
      "150"
    );
    expect(r.valid).toBe(true);
  });

  it("warns (not errors) when a market BUY cost exceeds cash", () => {
    const r = validateOrder({ ...base, quantity: "1000" }, "1000", "150");
    expect(r.valid).toBe(true);
    expect(r.warning).toMatch(/exceeds available cash/);
  });

  it("does not warn for a SELL regardless of size", () => {
    const r = validateOrder({ ...base, side: "SELL", quantity: "1000000" }, "10", "150");
    expect(r.warning).toBeUndefined();
  });

  it("requires a symbol", () => {
    expect(validateOrder({ ...base, symbol: "  " }, "100000", "150").valid).toBe(false);
  });
});
